"""Governance client (C9): the proposal lifecycle as a persisted state machine.

    submitting ──200──▶ pending ──approved──▶ applying ──200──▶ applied
        │                  ├─rejected──▶ rejected        ├─410──▶ lapsed
        │                  └─expired───▶ expired         └─409 stale──▶ stale
        ├─409 stale_base_version──▶ stale      (never reached the approver, costs no slot)
        ├─409 duplicate_proposal_id──▶ look it up in GET /approvals (we sent it before a crash)
        └─422──▶ failed (a bug in our payload: alert)
    auto path:  submitting ──PUT 200──▶ applied | 403 refused | 409 stale

Crash safety comes from three rules:
1. The row is committed in state `submitting` BEFORE the HTTP call (write-ahead intent).
2. `proposal_id` is deterministic, so resending after a crash can't create a second approval: the simulator
   answers 409 duplicate, and we reconcile instead.
3. On every cycle, open rows are reconciled against the simulator (GET /approvals, /config/history), so a
   crash between "simulator did it" and "we wrote it down" heals itself.
"""
from __future__ import annotations

import json

from . import policy
from .client import SimClient, SimError
from .store import Store

OPEN = ("submitting", "pending", "approved", "applying")


class Governance:
    def __init__(self, store: Store, client: SimClient):
        self.store, self.client = store, client

    # ---------------------------------------------------------------- queries
    def open_proposal(self, wid: str):
        r = self.store.q("SELECT proposal_id, state, kind, path FROM proposals WHERE wid=? AND state IN "
                         "('submitting','pending','approved','applying') ORDER BY created_minute DESC LIMIT 1", wid)
        return r[0] if r else None

    def requests_last_24h(self, wid: str, now: int) -> int:
        """Requests the approver has seen in the last 24 h. Rejected and expired ones count too (that's how
        the fatigue limit works), stale-before-sending and failed-locally ones don't."""
        return self.store.one("SELECT COUNT(*) FROM proposals WHERE wid=? AND path='approval' AND "
                              "approval_id IS NOT NULL AND submitted_minute > ?", wid, now - 1440) or 0

    def last_outcome(self, wid: str, path: str = "approval"):
        r = self.store.q("SELECT state, decided_minute, reasons_json, kind FROM proposals WHERE wid=? AND path=? "
                         "AND state NOT IN ('submitting','pending','approved','applying') "
                         "ORDER BY created_minute DESC, rowid DESC LIMIT 1", wid, path)
        return r[0] if r else None

    def _new_id(self, wid: str, kind: str, base_version: int, cfg: dict) -> str:
        prefix = f"{wid}:{kind}:v{base_version}:{policy.config_hash(cfg)}"
        n = self.store.one("SELECT COUNT(*) FROM proposals WHERE proposal_id LIKE ?", prefix + ":%") or 0
        return f"{prefix}:{n}"

    def _set(self, pid: str, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        self.store.db.execute(f"UPDATE proposals SET {sets} WHERE proposal_id=?", (*cols.values(), pid))

    def _record_version(self, wid, version, actor, minute, dims, pid, aid, cfg):
        self.store.db.execute(
            "INSERT INTO config_versions(wid,version,actor,minute,changed_dims,proposal_id,approval_id,config_json) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(wid,version) DO UPDATE SET config_json=excluded.config_json, "
            "proposal_id=excluded.proposal_id, approval_id=excluded.approval_id",
            (wid, version, actor, minute, json.dumps(dims), pid, aid, policy.canonical(cfg)))

    # ---------------------------------------------------------------- approval path
    def submit_approval(self, now: int, wid: str, kind: str, base_version: int, cfg: dict, fields: dict) -> str:
        pid = self._new_id(wid, kind, base_version, cfg)
        body = {"proposal_id": pid, "workflow_id": wid, "base_version": base_version, "proposed_config": cfg,
                **fields}
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO proposals(proposal_id,wid,kind,path,state,base_version,config_json,body_json,"
                "created_minute,submitted_minute) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, wid, kind, "approval", "submitting", base_version, policy.canonical(cfg),
                 json.dumps(body, sort_keys=True), now, now))
            self.store.decide(now, wid, "propose", fields["change_summary"],
                              {"diff": fields["diff"], "impact_score": fields["impact_score"],
                               "affected_leads": fields["affected_leads"], "rationale": fields["rationale"],
                               "evidence": fields.get("evidence")}, proposal_id=pid)
        self._send_approval(now, pid, body)
        return pid

    def _send_approval(self, now: int, pid: str, body: dict):
        wid = body["workflow_id"]
        try:
            r = self.client.post("/approvals", body, idempotent=True)
        except SimError as e:
            with self.store.tx():
                if e.code == "duplicate_proposal_id":
                    self._adopt_remote(now, pid, wid)
                elif e.code == "stale_base_version":
                    self._set(pid, state="stale", note="base changed before sending")
                    self.store.decide(now, wid, "stale", "config changed before our request went out; will "
                                      "rebuild on the new version", e.detail, proposal_id=pid)
                else:
                    self._set(pid, state="failed", note=f"{e.status} {e.code}: {e.detail}")
                    self.store.decide(now, wid, "alert", f"request refused locally ({e.code}); this is our bug",
                                      e.detail, proposal_id=pid)
            return
        with self.store.tx():
            self._set(pid, state="pending", approval_id=r["approval_id"])
            self.store.decide(now, wid, "submitted", f"sent for approval as {r['approval_id']}, "
                              f"expires {r['expires_at']}", r, proposal_id=pid, approval_id=r["approval_id"])

    def _adopt_remote(self, now, pid, wid):
        """We sent this proposal before (a crash ate the response). Find it and continue from its real state."""
        for a in self.client.get("/approvals", workflow_id=wid):
            if a["proposal_id"] == pid:
                self._set(pid, state=("pending" if a["status"] == "pending" else
                                      "approved" if a["status"] == "approved" else a["status"]),
                          approval_id=a["approval_id"])
                self.store.decide(now, wid, "reconciled", f"found our earlier request {a['approval_id']} "
                                  f"({a['status']}) after restart", a, proposal_id=pid, approval_id=a["approval_id"])
                return
        self._set(pid, state="failed", note="duplicate id but not found in /approvals")

    # ---------------------------------------------------------------- auto path
    def auto_apply(self, now: int, wid: str, kind: str, base_version: int, cfg: dict, rationale: str,
                   evidence: dict, summary: str) -> int | None:
        pid = self._new_id(wid, kind, base_version, cfg)
        body = {"proposal_id": pid, "base_version": base_version, "proposed_config": cfg, "rationale": rationale,
                "evidence": evidence}
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO proposals(proposal_id,wid,kind,path,state,base_version,config_json,body_json,"
                "created_minute,submitted_minute) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, wid, kind, "auto", "submitting", base_version, policy.canonical(cfg),
                 json.dumps(body, sort_keys=True), now, now))
            self.store.decide(now, wid, "propose", summary, {"rationale": rationale, "evidence": evidence,
                                                             "path": "auto-apply"}, proposal_id=pid)
        return self._send_auto(now, pid, wid, body)

    def _send_auto(self, now, pid, wid, body) -> int | None:
        try:
            r = self.client.put(f"/workflows/{wid}/config", body, idempotent=True)
        except SimError as e:
            with self.store.tx():
                if e.code == "duplicate_proposal_id":
                    return self._adopt_auto(now, pid, wid, body)
                state = "stale" if e.code == "stale_base_version" else "refused"
                self._set(pid, state=state, note=f"{e.status} {e.code}: {e.detail}")
                self.store.decide(now, wid, "alert" if state == "refused" else "stale",
                                  f"auto-apply {state}: {e.code}", e.detail, proposal_id=pid)
            return None
        with self.store.tx():
            self._set(pid, state="applied", applied_version=r["version"], decided_minute=now)
            self._record_version(wid, r["version"], "controller_auto", now, r["changed_dims"], pid, None,
                                 body["proposed_config"])
            self.store.decide(now, wid, "applied", f"auto-applied as v{r['version']} ({', '.join(r['changed_dims'])})",
                              r, proposal_id=pid, version=r["version"])
        return r["version"]

    def _adopt_auto(self, now, pid, wid, body):
        for v in self.client.get(f"/workflows/{wid}/config/history"):
            if v["proposal_id"] == pid:
                self._set(pid, state="applied", applied_version=v["version"], decided_minute=now)
                self._record_version(wid, v["version"], v["actor"], now, v["changed_dims"], pid, None,
                                     body["proposed_config"])
                self.store.decide(now, wid, "reconciled", f"auto-apply {pid} had landed as v{v['version']}",
                                  v, proposal_id=pid, version=v["version"])
                return v["version"]
        self._set(pid, state="failed", note="duplicate id but not in config history")
        return None

    # ---------------------------------------------------------------- reconcile (every cycle, and on boot)
    def reconcile(self, now: int, wid: str) -> list[int]:
        """Moves every open proposal of a workflow forward. Returns versions we applied this cycle."""
        rows = self.store.q("SELECT proposal_id, state, path, approval_id, body_json FROM proposals "
                            "WHERE wid=? AND state IN ('submitting','pending','approved','applying')", wid)
        applied = []
        remote = None
        for pid, state, path, aid, body_json in rows:
            body = json.loads(body_json)
            if state == "submitting":  # we crashed between the intent and the answer
                if path == "auto":
                    v = self._send_auto(now, pid, wid, body)
                    if v:
                        applied.append(v)
                    continue
                self._send_approval(now, pid, body)
                continue
            if remote is None:
                remote = {a["approval_id"]: a for a in self.client.get("/approvals", workflow_id=wid)}
            a = remote.get(aid)
            if a is None:
                continue
            status = a["status"]
            if state == "pending" and status != "pending":
                with self.store.tx():
                    self._set(pid, state=("approved" if status == "approved" else status),
                              decided_minute=now, reasons_json=json.dumps(a.get("reasons") or []))
                    self.store.decide(now, wid, status, f"{aid} {status}" +
                                      (f": {'; '.join(a.get('reasons') or [])}" if a.get("reasons") else ""),
                                      a, proposal_id=pid, approval_id=aid)
                state = "approved" if status == "approved" else status
            if state in ("approved", "applying"):
                if status == "applied":  # applied before a crash; find which version
                    v = self._version_for_approval(wid, aid)
                    with self.store.tx():
                        self._set(pid, state="applied", applied_version=v)
                        if v:
                            self._record_version(wid, v, "controller_approved", now, a.get("changed_dims") or [],
                                                 pid, aid, body["proposed_config"])
                    continue
                if status == "lapsed":
                    with self.store.tx():
                        self._set(pid, state="lapsed")
                        self.store.decide(now, wid, "lapsed", f"{aid} lapsed before apply", a, pid, aid)
                    continue
                v = self._apply(now, wid, pid, aid, body)
                if v:
                    applied.append(v)
        return applied

    def _apply(self, now, wid, pid, aid, body) -> int | None:
        with self.store.tx():
            self._set(pid, state="applying")
        try:
            r = self.client.post(f"/workflows/{wid}/apply", {"approval_id": aid}, idempotent=True)
        except SimError as e:
            with self.store.tx():
                if e.status == 410:
                    self._set(pid, state="lapsed")
                    self.store.decide(now, wid, "lapsed", f"{aid} lapsed (apply window passed)", e.detail, pid, aid)
                elif e.code == "stale_base_version":
                    self._set(pid, state="stale", note="config changed between approval and apply")
                    self.store.decide(now, wid, "stale", f"{aid} approved but config moved on "
                                      f"(now v{(e.detail or {}).get('current_version')}); will re-propose on top",
                                      e.detail, pid, aid)
                elif e.code == "not_approved" and (e.detail or {}).get("status") == "applied":
                    v = self._version_for_approval(wid, aid)
                    self._set(pid, state="applied", applied_version=v)
                    return v
                else:
                    self._set(pid, state="approved")  # try again next cycle
                    self.store.decide(now, wid, "alert", f"apply of {aid} failed: {e.code}", e.detail, pid, aid)
            return None
        with self.store.tx():
            self._set(pid, state="applied", applied_version=r["version"])
            self._record_version(wid, r["version"], "controller_approved", now, r["changed_dims"], pid, aid,
                                 body["proposed_config"])
            self.store.decide(now, wid, "applied", f"applied {aid} as v{r['version']} ({', '.join(r['changed_dims'])})",
                              r, proposal_id=pid, approval_id=aid, version=r["version"])
        return r["version"]

    def _version_for_approval(self, wid, aid):
        for v in self.client.get(f"/workflows/{wid}/config/history"):
            if v.get("approval_id") == aid:
                return v["version"]
        return None
