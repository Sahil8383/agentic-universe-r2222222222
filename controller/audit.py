"""Audit queries (C12) and the one-screen views (C11, C14). Everything here reads only our own SQLite store.

    why <workflow> <time>   which config was live at that time, who changed it and why, who approved it, and
                            what happened to the KPIs in the 24 h before vs after
    view <workflow>         the supervisor's one screen: config, experiments, open proposal, incidents
    status                  loop health for the on-call engineer
"""
from __future__ import annotations

import json
import re

from .store import Store

MIN_PER_DAY = 1440


def parse_when(s: str) -> int:
    """'D3 14:00' → sim minute. A plain integer is taken as a sim minute."""
    s = s.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    m = re.fullmatch(r"[Dd](-?\d+)\s+(\d{1,2}):(\d{2})", s)
    if not m:
        raise SystemExit("time must look like 'D3 14:00'")
    return int(m.group(1)) * MIN_PER_DAY + int(m.group(2)) * 60 + int(m.group(3))


def fmt(m) -> str:
    if m is None:
        return "-"
    d, r = divmod(int(m), MIN_PER_DAY)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def _kpis(store: Store, wid: str, start: int, end: int) -> dict:
    n, c, e, f = store.q("SELECT COUNT(*), COALESCE(SUM(outcome & 1),0), COALESCE(SUM((outcome>>1)&1),0), "
                         "COALESCE(SUM((outcome>>2)&1),0) FROM calls WHERE wid=? AND minute>=? AND minute<?",
                         wid, start, end)[0]
    return {"dials": n, "pickup": round(c / n, 4) if n else None, "engagement": round(e / c, 4) if c else None,
            "halluc_flag_rate": round(f / c, 4) if c else None}


def why(store: Store, wid: str, when: str) -> str:
    t = parse_when(when)
    rows = store.q("SELECT version, actor, minute, changed_dims, proposal_id, approval_id FROM config_versions "
                   "WHERE wid=? AND minute<=? ORDER BY version DESC LIMIT 1", wid, t)
    if not rows:
        return f"no config change recorded for {wid} at or before {fmt(t)}"
    v, actor, at, dims, pid, aid = rows[0]
    out = [f"{wid} at {fmt(t)} was running config v{v}, applied {fmt(at)} by {actor} (changed: "
           f"{', '.join(json.loads(dims or '[]')) or 'nothing — starting config'})"]
    if pid:
        p = store.q("SELECT kind, path, state, body_json, submitted_minute, decided_minute, reasons_json "
                    "FROM proposals WHERE proposal_id=?", pid)
        if p:
            kind, path, state, body, sub, dec, reasons = p[0]
            b = json.loads(body)
            out.append(f"  proposal {pid} ({kind}, via {'auto-apply' if path == 'auto' else 'approval'}), "
                       f"sent {fmt(sub)}, final state {state}")
            if b.get("change_summary"):
                out.append(f"  summary:   {b['change_summary']}")
            out.append(f"  rationale: {b.get('rationale')}")
            if b.get("impact_score") is not None:
                out.append(f"  impact {b['impact_score']}, affected leads {b.get('affected_leads')}")
            if aid:
                out.append(f"  approved as {aid} at {fmt(dec)} by the client's approver; applied {fmt(at)}")
    elif actor == "supervisor":
        out.append("  a client supervisor made this edit directly (it bypasses governance); we adopted it")
    for kind, m, summary in store.q("SELECT kind, minute, summary FROM decisions WHERE wid=? AND minute BETWEEN ? "
                                    "AND ? AND kind IN ('detect','incident_open','incident','experiment_promote',"
                                    "'experiment_drop','decay_rollback','supervisor_edit','review') ORDER BY id",
                                    wid, at - MIN_PER_DAY, at):
        out.append(f"  trigger {fmt(m)} [{kind}] {summary[:160]}")
    before, after = _kpis(store, wid, at - MIN_PER_DAY, at), _kpis(store, wid, at, at + MIN_PER_DAY)
    out.append(f"  24h before: {before}")
    out.append(f"  24h after:  {after}")
    nxt = store.q("SELECT version, actor, minute FROM config_versions WHERE wid=? AND version>? ORDER BY version "
                  "LIMIT 1", wid, v)
    if nxt:
        out.append(f"  next change: v{nxt[0][0]} by {nxt[0][1]} at {fmt(nxt[0][2])}")
    return "\n".join(out)


def history(store: Store, wid: str, limit: int = 200) -> str:
    rows = store.q("SELECT minute, kind, summary FROM decisions WHERE wid=? ORDER BY id DESC LIMIT ?", wid, limit)
    return "\n".join(f"{fmt(m)}  [{k}] {s}" for m, k, s in reversed(rows))


def view(store: Store, wid: str) -> str:
    out = [f"== {wid} =="]
    v = store.q("SELECT version, actor, minute, config_json FROM config_versions WHERE wid=? ORDER BY version DESC "
                "LIMIT 1", wid)
    if v:
        out.append(f"live config v{v[0][0]} ({v[0][1]}, {fmt(v[0][2])})")
        if v[0][3]:
            cfg = json.loads(v[0][3])
            out.append(f"  default: {cfg['allocations']['default']}  prompt {cfg['prompt_version']}")
            out.append(f"  cohorts: {[c['id'] for c in cfg['cohorts']]}")
    for key in ("exp", "incident", "did", "urgent"):
        val = store.state(wid, key)
        if val:
            out.append(f"{key}: {json.dumps(val, default=str)[:600]}")
    p = store.q("SELECT proposal_id, state, kind, submitted_minute FROM proposals WHERE wid=? ORDER BY "
                "created_minute DESC LIMIT 5", wid)
    out.append("recent proposals:")
    out += [f"  {fmt(m)} {s:10s} {k:9s} {pid}" for pid, s, k, m in p]
    now = store.meta("sim_minute_seen", 0)
    out.append(f"last 24h KPIs: {_kpis(store, wid, now - MIN_PER_DAY, now + 1)}")
    return "\n".join(out)


def status(store: Store) -> str:
    m = store.q("SELECT minute, events, ingest_s, cycle_s, lag_min, credits, rss_mb FROM metrics ORDER BY minute DESC "
                "LIMIT 1")
    if not m:
        return "no cycles recorded yet"
    minute, ev, ing, cyc, lag, credits, rss = m[0]
    tot = store.q("SELECT COUNT(*), SUM(events), SUM(ingest_s), SUM(cycle_s) FROM metrics")[0]
    states = dict(store.q("SELECT state, COUNT(*) FROM proposals GROUP BY state"))
    alerts = store.q("SELECT minute, wid, summary FROM decisions WHERE kind='alert' ORDER BY id DESC LIMIT 5")
    out = [f"sim time {fmt(minute)} · last cycle {cyc:.2f}s · ingest lag {lag} min · credits {credits} · peak RSS "
           f"{rss:.0f} MB",
           f"cycles {tot[0]} · events {tot[1]} · ingest {tot[2]:.1f}s ({(tot[1] or 0) / max(tot[2] or 1e-9, 1e-9):,.0f}"
           f" events/s) · total loop {tot[3]:.1f}s",
           f"proposals by state: {states}"]
    if alerts:
        out.append("recent alerts:")
        out += [f"  {fmt(a)} {w} {s}" for a, w, s in alerts]
    return "\n".join(out)
