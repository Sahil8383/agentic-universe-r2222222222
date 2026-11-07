"""The reconcile loop. One cycle:

    ingest new chunks → reconcile proposals & configs → detect (caller ID, hallucination, decay)
    → daily review (experiments, call time) → plan one step per workflow → write metrics → advance time

Rule: time only moves (POST /sim/advance) after every decision of the cycle is committed. So whatever
moment the process dies at, a restart re-reads the store, reconciles with the simulator and carries on.
"""
from __future__ import annotations

import copy
import json
import resource
import sys
import time
from pathlib import Path

import yaml

from . import cohorts as C
from . import detectors as D
from . import experiments as X
from . import planner as PL
from . import policy
from . import schedule as SCH
from . import settings as S
from .client import SimClient, SimError
from .governance import Governance
from .ingest import Ingestor
from .store import Store

MIN_PER_DAY = 1440
FEATURES_SEEN = ("age_band", "city_tier", "lang", "value_band")


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def load_preferences() -> dict:
    p = Path(__file__).with_name("preferences.yaml")
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def fmt_minute(m: int) -> str:
    d, r = divmod(m, MIN_PER_DAY)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


class Controller:
    def __init__(self, url: str, db_path: str):
        self.client = SimClient(url)
        self._wait_for_sim()
        self.store = Store(self._fresh_db_if_new_world(db_path))
        self.gov = Governance(self.store, self.client)
        st = self.client.get("/sim/state")
        self.ing = Ingestor(self.store, self.client)
        self.ing.set_clock(st["sim_minute"], st["ts"])
        self.prefs = load_preferences()
        self.wfs: dict[str, dict] = {}
        self.now = 0
        self.cur: dict[str, dict] = {}    # wid -> {"version", "config"}
        self.credits_start = self.store.meta("credits_start")
        if self.credits_start is None:
            self.credits_start = st["credits_remaining"]
            self.store.set_meta("credits_start", self.credits_start)

    # ---------------------------------------------------------------- boot
    def _wait_for_sim(self):
        for _ in range(600):
            try:
                if self.client.get("/health").get("ok"):
                    return
            except Exception:
                pass
            time.sleep(1)
        raise SystemExit("simulator never came up")

    def _fresh_db_if_new_world(self, db_path: str) -> str:
        """The DB belongs to one world. If the simulator was restarted (new world) or is a different scenario,
        archive the old DB instead of mixing two worlds' memories."""
        st = self.client.get("/sim/state")
        hist = self.client.get("/events/chunks", kind="history", after=-1)
        sig = {"scenario": st["scenario"], "history": [h["last_event_id"] for h in hist][:3]}
        p = Path(db_path)
        if p.exists():
            s = Store(str(p))
            old = s.meta("world")
            seen = s.meta("sim_minute_seen", -1)
            s.db.close()
            if old != sig or seen > st["sim_minute"]:
                archive = p.with_suffix(f".{int(time.time())}.old.db")
                for suffix in ("", "-wal", "-shm"):
                    src = Path(str(p) + suffix)
                    if src.exists():
                        src.rename(str(archive) + suffix)
                log(f"new world detected, archived old store to {archive}")
        p.parent.mkdir(parents=True, exist_ok=True)
        s = Store(str(p))
        if s.meta("world") is None:
            s.set_meta("world", sig)
        s.db.close()
        return str(p)

    def pref(self, wid: str) -> dict:
        return self.prefs.get(self.wfs[wid]["tenant_id"]) or {}

    def S(self, wid, key, default=None):
        return self.store.state(wid, key, default)

    def set(self, wid, key, value):
        self.store.set_state(wid, key, value)

    # ---------------------------------------------------------------- main loop
    def run(self):
        while True:
            now, finished = self.cycle()
            if finished:
                log("scenario finished")
                return
            step = S.TICK_BUSY_MIN if self._busy(now) else S.TICK_IDLE_MIN
            try:
                self.client.advance(step, now)
            except SimError as e:
                if e.code == "finished":
                    self.cycle()
                    return
                raise

    def _busy(self, now: int) -> bool:
        for wid, w in self.wfs.items():
            if policy.is_dialling(now, w["dialling_window"]):
                return True
            lo, hi = w["governance"]["approver_hours"]
            if lo <= (now % MIN_PER_DAY) // 60 < hi:
                return True
            if self.gov.open_proposal(wid):
                return True
        # the next tick must not jump over the start of a busy hour
        return (now % 60) != 0

    def cycle(self) -> tuple[int, bool]:
        t0 = time.perf_counter()
        st = self.client.get("/sim/state")
        now = self.now = st["sim_minute"]
        if not self.wfs:
            self._load_workflows()
        ing = self.ing.run()
        n_dec_before = self.store.one("SELECT COUNT(*) FROM decisions")
        for wid in self.wfs:
            self.gov.reconcile(now, wid)
            self._refresh_config(now, wid)
        for wid in self.wfs:
            if not self.S(wid, "bootstrapped"):
                self._bootstrap(now, wid)
        for wid in self.wfs:
            self._detect(now, wid)
            self._incident_step(now, wid)
            self._review_if_due(now, wid)
        for wid in self.wfs:
            self._plan(now, wid)
            self.gov.reconcile(now, wid)       # an approval decided this tick gets applied right away
            self._refresh_config(now, wid)
        st2 = self.client.get("/sim/state")
        with self.store.tx():
            self.store.set_meta("sim_minute_seen", now)
            n_dec = self.store.one("SELECT COUNT(*) FROM decisions") - n_dec_before
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin"
                                                                        else 1024)
            lag = None if ing["max_minute"] is None else now - ing["max_minute"]
            self.store.db.execute("INSERT OR REPLACE INTO metrics VALUES(?,?,?,?,?,?,?,?,?)",
                                  (now, time.time(), ing["events"], ing["seconds"], time.perf_counter() - t0,
                                   lag, n_dec, st2["credits_remaining"], rss))
        if now % MIN_PER_DAY == 0:
            log(f"{fmt_minute(now)} events={ing['events']} ingest={ing['seconds']:.1f}s "
                f"credits={st2['credits_remaining']} rss={rss:.0f}MB")
        return now, st["finished"]

    def _load_workflows(self):
        with self.store.tx():
            for w in self.client.get("/workflows"):
                self.wfs[w["workflow_id"]] = w
                self.store.db.execute("INSERT OR REPLACE INTO workflows VALUES(?,?,?)",
                                      (w["workflow_id"], w["tenant_id"], json.dumps(w)))

    # ---------------------------------------------------------------- config tracking + supervisor edits
    def _refresh_config(self, now: int, wid: str):
        c = self.client.get(f"/workflows/{wid}/config")
        self.cur[wid] = {"version": c["version"], "config": c["config"]}
        known = self.S(wid, "known_version")
        if known is None:  # first sight: remember the starting point
            with self.store.tx():
                self.set(wid, "known_version", c["version"])
                self.store.db.execute("INSERT OR IGNORE INTO config_versions(wid,version,actor,minute,changed_dims,"
                                      "config_json) VALUES(?,?,?,?,?,?)",
                                      (wid, c["version"], c["actor"], now, "[]", policy.canonical(c["config"])))
            return
        if c["version"] == known:
            return
        hist = [h for h in self.client.get(f"/workflows/{wid}/config/history") if h["version"] > known]
        with self.store.tx():
            for h in hist:
                is_latest = h["version"] == c["version"]
                self.store.db.execute(
                    "INSERT INTO config_versions(wid,version,actor,minute,changed_dims,proposal_id,approval_id,"
                    "config_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(wid,version) DO UPDATE SET "
                    "config_json=COALESCE(config_versions.config_json, excluded.config_json)",
                    (wid, h["version"], h["actor"], now, json.dumps(h["changed_dims"]), h["proposal_id"],
                     h["approval_id"], policy.canonical(c["config"]) if is_latest else None))
                if "prompt_version" in h["changed_dims"]:
                    self.set(wid, "prompt_since", now)
                if h["actor"] == "supervisor":
                    self._absorb_supervisor(now, wid, h, c["config"])
            self.set(wid, "known_version", c["version"])

    def _absorb_supervisor(self, now: int, wid: str, h: dict, cfg: dict):
        """A supervisor's edit is the client's intent. We adopt it into our desired state for every dimension it
        touched (so we never silently overwrite it), pause experiments it interrupted, and say so."""
        dims = h["changed_dims"]
        da = self.S(wid, "default_alloc")
        plan = self.S(wid, "plan")
        exp = self.S(wid, "exp", {})
        notes = []
        for d in dims:
            if d in ("welcome_message", "voice", "caller_id") and da is not None:
                da[d] = copy.deepcopy(cfg["allocations"]["default"][d])
                for seg in (plan or {}).get("segments", []):
                    seg["alloc"].pop(d, None)
                if d in exp:
                    exp[d]["paused"] = {"version": h["version"], "minute": now}
                    notes.append(f"{d} experiment paused: interrupted by supervisor at v{h['version']}")
                if d == "caller_id":
                    did = self.S(wid, "did", {})
                    did["paused_until"] = now + MIN_PER_DAY
                    self.set(wid, "did", did)
            if d == "prompt_version":
                self.set(wid, "prompt_target", None)
            if d in ("cohorts", "schedule"):
                self.set(wid, "frozen_plan", True)
                notes.append("cohort/schedule plan frozen: supervisor owns it now")
        if da is not None:
            self.set(wid, "default_alloc", da)
        if plan is not None:
            self.set(wid, "plan", plan)
        self.set(wid, "exp", exp)
        self.store.decide(now, wid, "supervisor_edit", f"supervisor changed {', '.join(dims)} (v{h['version']}); "
                          "adopted as the new intent" + (": " + "; ".join(notes) if notes else ""),
                          {"changed_dims": dims, "version": h["version"]}, version=h["version"])

    # ---------------------------------------------------------------- bootstrap from history (C3)
    def _bootstrap(self, now: int, wid: str):
        w = self.wfs[wid]
        cur = self.cur[wid]["config"]
        hist_days = [d for d in self.ing.counters.days(wid) if d < 0]
        plan = C.empty_plan()
        g = SCH.learn_groups(self.ing.counters, wid, hist_days, w["dialling_window"], list(FEATURES_SEEN))
        if g:
            plan["schedule_feature"], plan["groups"], plan["evidence"] = g["feature"], g["groups"], g["evidence"]
        if cur.get("cohorts"):  # the business already defined cohorts: respect them, only tune the default
            self.set(wid, "frozen_plan", True)
        da = {d: dict(cur["allocations"]["default"][d]) for d in policy.AGENT_DIMS}
        exp = {}
        notes = []
        for dim in X.ENGAGEMENT_DIMS:
            alloc, st_, _ = X.review_dim(self.ing.counters, wid, dim, w["options"][dim], da[dim], X.new_state(),
                                         0, None)
            da[dim], exp[dim] = alloc, st_
            new = [o for o in alloc if o not in cur["allocations"]["default"][dim]]
            if new:
                notes.append({"why": f"explore {dim} options {', '.join(new)} at ≤{S.EXPLORE_SHARE:.0%} each "
                                     f"(≤{S.EXPLORE_CAP:.0%} total): history only ever used "
                                     f"{', '.join(cur['allocations']['default'][dim])}"})
        with self.store.tx():
            self.set(wid, "plan", plan)
            self.set(wid, "default_alloc", da)
            self.set(wid, "exp", exp)
            self.set(wid, "notes", notes)
            self.set(wid, "did", {"flagged": {}, "probation": {}})
            self.set(wid, "prompt_since", -10 ** 6)
            self.set(wid, "bootstrapped", True)
            self.store.decide(now, wid, "bootstrap", "learned from history: " + "; ".join(C.describe(plan) or
                              ["no significant call-time split"]), {"schedule": g and g["evidence"],
                                                                     "history_days": len(hist_days)})
        self._did_target(now, wid, {})

    # ---------------------------------------------------------------- detectors
    def _detect(self, now: int, wid: str):
        w = self.wfs[wid]
        if not policy.is_dialling(now, w["dialling_window"]):
            return
        self._halluc_check(now, wid)  # every tick: a bad prompt hurts every call
        if now % 60:
            return  # caller-ID and decay checks once an hour
        did = self.S(wid, "did", {"flagged": {}, "probation": {}})
        health = D.did_health(self.store, self.ing.counters, wid, now, did["probation"])
        changed = False
        for d, h in health.items():
            if h["verdict"] == "bad" and d not in did["flagged"]:
                did["flagged"][d] = now
                did["probation"].pop(d, None)
                changed = True
                with self.store.tx():
                    self.set(wid, "did", did)
                    self._add_urgent(wid, f"caller ID {d} unhealthy", ["caller_id"], 0.7)
                    self._note(wid, f"caller ID {d}: pickup {h['ratio']:.0%} of established numbers in the same hours "
                                    f"over {h['dials']} dials (p={h['p_low']:.1e}); moving all traffic off it")
                    self.store.decide(now, wid, "detect", f"caller ID {d} flagged: pickup ratio {h['ratio']} vs peers "
                                      "(a human should check it with the carrier; we won't bring it back on our own)", h)
            elif h["verdict"] == "good" and d in did["probation"]:
                did["probation"].pop(d)
                did.setdefault("cleared", {})[d] = now
                changed = True
                with self.store.tx():
                    self.set(wid, "did", did)
                    self._note(wid, f"caller ID {d} passed probation: pickup {h['ratio']:.0%} of established numbers "
                                    f"over {h['dials']} dials; giving it an equal share", kind="did_good")
                    self.store.decide(now, wid, "detect", f"caller ID {d} healthy (ratio {h['ratio']}), off probation", h)
        if changed:
            self._did_target(now, wid, health)
        if now % (4 * 60) == 0:
            self._decay_checks(now, wid)

    def _did_target(self, now: int, wid: str, health: dict):
        """Spread traffic evenly over the healthy numbers (load wears a number out, so concentration costs
        pickup); never-used numbers start on probation at a small share; flagged numbers get nothing."""
        w = self.wfs[wid]
        opts = list(w["options"]["caller_id"])
        did = self.S(wid, "did", {"flagged": {}, "probation": {}})
        life = D.did_lifetime_dials(self.ing.counters, wid)
        for d in opts:  # never used before (not even in history): probation from now
            if life.get(d, 0) < S.DID_TRUST_DIALS and d not in did["flagged"] and d not in did["probation"] \
                    and not did.get("cleared", {}).get(d):
                did["probation"][d] = now
        usable = [d for d in opts if d not in did["flagged"]]
        if not usable:
            self.store.decide(now, wid, "alert", "every caller ID is unhealthy; keeping the current mix, a human "
                              "must add numbers", health)
            return
        known = [d for d in usable if d not in did["probation"]]
        new = [d for d in usable if d in did["probation"]]
        if not known:
            known, new = new, []
        total_new = S.DID_NEW_TOTAL_SINGLE if len(known) <= 1 else S.DID_NEW_TOTAL
        share_new = (total_new / len(new) if len(known) <= 1 else min(S.DID_NEW_SHARE, total_new / len(new))) \
            if new else 0
        target = {d: share_new for d in new}
        rest = 1 - sum(target.values())
        for d in known:
            target[d] = rest / len(known)
        target = policy.norm(target)
        da = self.S(wid, "default_alloc")
        if did.get("paused_until", -1) > now:  # supervisor chose the mix recently: only remove flagged numbers
            target = policy.norm({d: v for d, v in da["caller_id"].items() if d not in did["flagged"]} or target)
        with self.store.tx():
            self.set(wid, "did", did)
            if policy.tv(target, da["caller_id"]) > 1e-3:
                da["caller_id"] = target
                self.set(wid, "default_alloc", da)
                if new and not any(n.get("kind") == "did_spread" for n in self.S(wid, "notes", []) or []):
                    self._note(wid, f"spread calls evenly over healthy caller IDs ({', '.join(known)}): load wears a "
                                    f"number out; new numbers {', '.join(new)} start at {share_new:.0%} on probation",
                               kind="did_spread")

    def _halluc_check(self, now: int, wid: str):
        inc = self.S(wid, "incident")
        if inc and inc["phase"] not in ("closed",):
            return
        cur = self.cur[wid]["config"]
        prompt = cur["prompt_version"]
        since = self.S(wid, "prompt_since", -10 ** 6)
        ref_prompt, ref = self._reference_prompt(wid, prompt, since)
        res = D.hallucination(self.store, self.ing.counters, wid, now, prompt, since, ref)
        if not res["fire"]:
            return
        known_good = ref_prompt if ref_prompt != prompt else None
        inc = {"opened": now, "prompt": prompt, "known_good": known_good, "phase": "validate", "detector": res,
               "candidates": []}
        with self.store.tx():
            self.set(wid, "incident", inc)
            self.store.decide(now, wid, "incident_open",
                              f"hallucination flags on {prompt}: {res['k']}/{res['n']} = {res['rate']:.1%} vs "
                              f"reference {res['baseline']:.1%} (p={res['p']:.1e})", res)

    def _reference_prompt(self, wid: str, prompt: str, since: int):
        """The prompt to compare against: the one that ran longest before the current one went live
        (normally the business default), measured on days before the change."""
        days = [d for d in self.ing.counters.days(wid) if d < 0 or (d + 1) * MIN_PER_DAY <= since]
        best, best_c = None, None
        for k, r in self.ing.counters.prefix(wid, days, "P|").items():
            p = k.split("|")[1]
            if best_c is None or r[1] > best_c[1]:
                best, best_c = p, (r[3], r[1])
        if best is None:
            return prompt, None
        return best, best_c

    def _decay_checks(self, now: int, wid: str):
        today = now // MIN_PER_DAY
        exp = self.S(wid, "exp", {})
        da = self.S(wid, "default_alloc")
        for dim, st_ in exp.items():
            new_st, note = X.decay_check(self.ing.counters, wid, dim, st_, today)
            if note:
                da[dim] = X.rollback_alloc(da[dim], note["option"], note["vs"])
                with self.store.tx():
                    self._add_urgent(wid, f"{dim} {note['option']} decayed", [dim], 0.6)
                    self._note(wid, note["why"], kind="rollback")
                    self.store.decide(now, wid, "decay_rollback", f"{dim}: {note['option']} stopped winning; rolling "
                                      f"back to {note['vs']}", note)
            exp[dim] = new_st
        with self.store.tx():
            self.set(wid, "exp", exp)
            self.set(wid, "default_alloc", da)

    # ---------------------------------------------------------------- hallucination incident (C8)
    def _incident_step(self, now: int, wid: str):
        inc = self.S(wid, "incident")
        if not inc or inc["phase"] == "closed":
            return
        w = self.wfs[wid]
        gov = w["governance"]
        cur_prompt = self.cur[wid]["config"]["prompt_version"]
        if inc["phase"] in ("validate", "llm", "proposed") and cur_prompt != inc["prompt"] and \
                cur_prompt != (self.S(wid, "prompt_target") or ""):
            inc["phase"] = "closed"
            self._save_incident(now, wid, inc, f"closed: prompt changed to {cur_prompt} by someone else")
            return
        if inc["phase"] == "validate":
            cand = inc.get("known_good")
            if cand and cand in w["options"]["prompt_version"]:
                v = self._validated(now, wid, cand)
                if v and v["pass_rate"] >= gov["min_validation_pass_rate"]:
                    self._propose_prompt(now, wid, inc, cand, v, "revert to the last known-good prompt")
                    return
                if v is None:
                    return  # out of credits for now: try again next cycle
            inc["phase"] = "llm"
            self._save_incident(now, wid, inc, "no usable known-good prompt; asking the LLM for fixes")
            return
        if inc["phase"] == "llm":
            st = self.client.get("/sim/state")
            if st["credits_remaining"] < 3 + S.VALIDATION_N / 100:
                return
            try:
                r = self.client.post("/llm/suggest-fixes", {"workflow_id": wid, "problem":
                                     f"hallucination flag rate {inc['detector']['rate']:.1%} on {inc['prompt']}"},
                                     idempotent=False)
            except SimError as e:
                self.store.decide(now, wid, "alert", f"LLM suggest-fixes failed: {e.code}", e.detail)
                return
            cands = [s["prompt_version"] for s in r["suggestions"] if s["prompt_version"] != inc["prompt"]]
            best = None
            for p in cands:
                v = self._validated(now, wid, p)
                if v and v["pass_rate"] >= gov["min_validation_pass_rate"] and \
                        (best is None or v["pass_rate"] > best[1]["pass_rate"]):
                    best = (p, v)
            inc["candidates"] = cands
            if best:
                self._propose_prompt(now, wid, inc, best[0], best[1], "best validated LLM suggestion")
            else:
                inc["phase"] = "stuck"
                self._save_incident(now, wid, inc, "no candidate passed validation; paging a human")
            return
        if inc["phase"] == "proposed" and cur_prompt == inc["fix"]:
            inc["phase"], inc["applied"] = "verifying", now
            self._save_incident(now, wid, inc, f"fix {inc['fix']} is live; verifying the flag rate")
            return
        if inc["phase"] == "verifying":
            res = D.hallucination(self.store, self.ing.counters, wid, now, cur_prompt, inc["applied"],
                                  self._reference_prompt(wid, cur_prompt, inc["opened"])[1])
            if res["n"] >= S.VERIFY_MIN_CONNECTED:
                if res["p"] > 0.01:
                    inc["phase"] = "closed"
                    self._save_incident(now, wid, inc, f"verified: {res['k']}/{res['n']} flagged since the fix, "
                                        f"consistent with the reference rate; incident closed", res)
                else:
                    inc["phase"], inc["known_good"] = "llm", None
                    self._save_incident(now, wid, inc, "fix did not bring flags down; trying LLM suggestions", res)

    def _validated(self, now, wid, prompt):
        r = self.store.q("SELECT run_id, pass_rate, n FROM validations WHERE wid=? AND prompt=? ORDER BY minute DESC "
                         "LIMIT 1", wid, prompt)
        if r:
            return {"validation_run_id": r[0][0], "pass_rate": r[0][1], "n": r[0][2], "reused": True}
        try:
            v = self.client.post("/validation/runs", {"workflow_id": wid, "prompt_version": prompt,
                                                      "n": S.VALIDATION_N}, idempotent=False)
        except SimError as e:
            self.store.decide(now, wid, "alert", f"validation run failed: {e.code}", e.detail)
            return None
        with self.store.tx():
            self.store.db.execute("INSERT INTO validations VALUES(?,?,?,?,?,?)",
                                  (v["validation_run_id"], wid, prompt, v["n"], v["pass_rate"], now))
            self.store.decide(now, wid, "validation", f"validated {prompt}: pass rate {v['pass_rate']:.3f} on n={v['n']}",
                              v)
        return v

    def _propose_prompt(self, now, wid, inc, prompt, v, how):
        inc["phase"], inc["fix"], inc["validation"] = "proposed", prompt, v
        with self.store.tx():
            self.set(wid, "prompt_target", prompt)
            self._add_urgent(wid, f"hallucination incident on {inc['prompt']}", ["prompt_version"], 0.9)
            d = inc["detector"]
            self._note(wid, f"since {inc['prompt']} went live, {d['k']}/{d['n']} connected calls were flagged "
                            f"({d['rate']:.1%}) vs {d['baseline']:.1%} before (p={d['p']:.1e}); {how}: {prompt}, "
                            f"validation {v['validation_run_id']} passed {v['pass_rate']:.1%}", kind="incident")
            self._save_incident(now, wid, inc, f"proposing {prompt} ({how})")

    def _save_incident(self, now, wid, inc, msg, ev=None):
        with self.store.tx():
            self.set(wid, "incident", inc)
            self.store.decide(now, wid, "incident", msg, ev or {k: inc.get(k) for k in ("prompt", "phase", "fix")})

    # ---------------------------------------------------------------- daily review (C6, C7, C4)
    def _review_if_due(self, now: int, wid: str):
        w = self.wfs[wid]
        today, mod = divmod(now, MIN_PER_DAY)
        lo = w["governance"]["approver_hours"][0]
        at = max(lo, self.pref(wid).get("not_before_hour") or lo) * 60
        if mod < at or self.S(wid, "review_day", -1) >= today:
            return
        da = self.S(wid, "default_alloc")
        exp = self.S(wid, "exp", {})
        obj = w["objective"]
        guard = (obj.get("guardrail") or {}).get("pickup_rate_max_drop")
        notes = []
        for dim in X.ENGAGEMENT_DIMS:
            alloc, st_, ns = X.review_dim(self.ing.counters, wid, dim, w["options"][dim], da[dim],
                                          exp.get(dim) or X.new_state(), today, guard)
            da[dim], exp[dim] = alloc, st_
            notes += ns
        plan = self.S(wid, "plan")
        if not self.S(wid, "frozen_plan"):
            for dim in X.ENGAGEMENT_DIMS:
                if (exp.get(dim) or {}).get("paused"):
                    continue
                g = max(da[dim], key=da[dim].get)
                for win in X.segment_winners(self.ing.counters, wid, dim, w["options"][dim], g, today,
                                             list(FEATURES_SEEN), (exp.get(dim) or {}).get("dropped", [])):
                    sid = C.segment_id(win["feature"], win["value"])
                    seg = next((x for x in plan["segments"] if x["id"] == sid), None)
                    if seg is None:
                        trial = copy.deepcopy(plan)
                        trial["segments"].append({"id": sid, "feature": win["feature"], "value": win["value"],
                                                  "alloc": {}})
                        if len(trial["segments"]) > S.MAX_SEGMENTS or len(C.build_config_parts(trial)[0]) > 20:
                            continue
                        plan, seg = trial, trial["segments"][-1]
                    if dim in seg["alloc"]:
                        continue
                    seg["alloc"][dim] = {win["option"]: 1 - S.HOLDOUT_SHARE, g: S.HOLDOUT_SHARE}
                    notes.append({"kind": "segment", "dim": dim, "option": win["option"], "vs": g, **win,
                                  "why": f"in {win['feature']}={win['value']} ({win['share']:.0%} of calls) "
                                         f"{win['option']} beats {g}: P = {win['p_better']:.3f} ≥ "
                                         f"{S.P_PROMOTE_SEGMENT} (segment bar), lift {win['lift']:+.1%}"})
        with self.store.tx():
            self.set(wid, "plan", plan)
            self.set(wid, "default_alloc", da)
            self.set(wid, "exp", exp)
            for n in notes:
                self._note(wid, n["why"], kind=n["kind"])
                self.store.decide(now, wid, f"experiment_{n['kind']}", f"{n['dim']}: {n['kind']} {n['option']} "
                                  f"(vs {n['vs']})", n)
            self.store.decide(now, wid, "review", "daily review: " + "; ".join(
                f"{d} {da[d]}" for d in X.ENGAGEMENT_DIMS), {"exp": exp})
            self.set(wid, "review_day", today)

    # ---------------------------------------------------------------- planning one step
    def _plan(self, now: int, wid: str):
        if self.gov.open_proposal(wid):
            return
        w = self.wfs[wid]
        gov = w["governance"]
        cur_v, cur = self.cur[wid]["version"], self.cur[wid]["config"]
        st = {k: self.S(wid, k) for k in ("plan", "default_alloc", "prompt_target", "frozen_plan", "urgent", "notes")}
        st["last_applied_minute"] = self.store.one("SELECT MAX(minute) FROM config_versions WHERE wid=? AND actor LIKE "
                                                   "'controller%'", wid) or -10 ** 9
        want = PL.desired_config(cur, st)
        dims = policy.changed_dims(cur, want)
        self._clear_done_urgent(wid, dims)
        st["urgent"] = self.S(wid, "urgent") or []
        if not dims:
            return
        urgent_dims = {d for u in st["urgent"] for d in u["dims"]}
        evidence = self._evidence(now, wid)

        # 1. auto-apply what the policy allows (and the client hasn't asked us to hold)
        aa = gov.get("auto_apply") or {}
        allowed = set(aa.get("allowed_dims") or []) if aa.get("enabled") else set()
        if self.pref(wid).get("auto_apply_customer_facing") is False:
            allowed -= {d for d in PL.CUSTOMER_FACING if d not in urgent_dims}
        auto_dims = set(dims) & allowed
        if auto_dims:
            part = PL.partial(cur, want, auto_dims)
            step = policy.step_toward(cur, part, float(aa.get("max_shift", 0)))
            sdims = policy.changed_dims(cur, step)
            if sdims and policy.auto_allowed(gov, sdims) and set(sdims) <= allowed:
                why = self._rationale(wid) or "within the auto-apply policy"
                self.gov.auto_apply(now, wid, "auto", cur_v, step, why[:1500],
                                    {"detector": evidence.get("did_health"), "max_shift": aa.get("max_shift")},
                                    f"auto-apply {', '.join(sdims)} (shift ≤ {aa.get('max_shift')})")
                return
        # 2. one batched approval for the rest
        rest = [d for d in dims if d not in allowed] or dims
        urgent = bool(urgent_dims & set(rest))
        cap = int(gov["max_requests_per_24h"])
        if not urgent:
            cap = min(cap, int(self.pref(wid).get("max_requests_per_day") or cap)) - S.INCIDENT_SLOTS
        if self.gov.requests_last_24h(wid, now) >= cap:
            return
        ok, why_not = policy.submit_window_ok(now, gov, self.pref(wid).get("not_before_hour"), urgent)
        if not ok:
            return
        last = self.gov.last_outcome(wid)
        if last and not urgent and last[0] in ("rejected", "expired", "lapsed") and \
                (last[1] or 0) > now - S.RETRY_REJECTED_AFTER_MIN:
            return
        today = now // MIN_PER_DAY
        if not urgent and self.S(wid, "review_day", -1) < today:
            return  # non-urgent changes go out once a day, after the morning review, as one batch
        fields = PL.proposal_fields(w, cur, want, dims, st, self.ing.counters, today, evidence)
        if "prompt_version" in dims:
            inc = self.S(wid, "incident") or {}
            v = inc.get("validation") or {}
            if not v:
                return  # never send a prompt change without validation evidence
            fields["evidence"]["validation_run_id"] = v["validation_run_id"]
        kind = "incident" if urgent else "batch"
        self.gov.submit_approval(now, wid, kind, cur_v, want, fields)

    def _evidence(self, now, wid) -> dict:
        ev = {"did_dials": D.did_lifetime_dials(self.ing.counters, wid)}
        try:
            ev["did_health"] = D.did_health(self.store, self.ing.counters, wid, now)
        except Exception:
            pass
        inc = self.S(wid, "incident")
        if inc and inc.get("phase") in ("proposed", "validate", "llm"):
            ev["incident"] = {k: inc.get(k) for k in ("prompt", "fix", "detector", "opened")}
            ev["validation"] = inc.get("validation")
        return ev

    def _rationale(self, wid) -> str:
        return " | ".join(n["why"] for n in (self.S(wid, "notes") or [])[-6:])

    def _note(self, wid, why: str, kind: str = "note"):
        notes = self.S(wid, "notes", []) or []
        notes.append({"why": why, "kind": kind, "minute": self.now})
        self.set(wid, "notes", notes[-20:])

    def _add_urgent(self, wid, reason, dims, impact):
        u = self.S(wid, "urgent", []) or []
        if not any(x["reason"] == reason for x in u):
            u.append({"reason": reason, "dims": dims, "impact": impact})
        self.set(wid, "urgent", u)

    def _clear_done_urgent(self, wid, open_dims):
        u = self.S(wid, "urgent", []) or []
        keep = [x for x in u if set(x["dims"]) & set(open_dims)]
        if len(keep) != len(u):
            self.set(wid, "urgent", keep)


def main(url: str, db_path: str):
    ctl = Controller(url, db_path)
    ctl.run()
