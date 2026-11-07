"""Planner: turns the controller's intent into at most one governed change per workflow per cycle.

Level-triggered, like a Kubernetes controller. Detectors and the daily review only edit the DESIRED state
(cohort plan, default allocation, prompt target). Each cycle the planner compares desired with the actual
config and, if they differ, takes one step:
  1. the parts the policy lets us auto-apply (and that the client hasn't asked us to hold), in steps no
     bigger than `max_shift`; or else
  2. one batched approval request carrying everything else, if the request budget and approver timing allow.
Never both in one cycle, and nothing while a proposal is open: our own apply would make our pending
approval stale.
"""
from __future__ import annotations

import copy

from . import cohorts as C
from . import policy
from . import settings as S
from .experiments import arm_counts, window
from .ingest import Counters

CUSTOMER_FACING = ("welcome_message", "voice", "prompt_version")


# ---------------------------------------------------------------- desired config
def desired_config(cur: dict, st: dict) -> dict:
    """The config we want right now, built from the persisted intent, snapped onto `cur` wherever we don't
    intend a change (so float noise never shows up as a change)."""
    if st.get("frozen_plan"):
        want = copy.deepcopy(cur)
        want["allocations"]["default"] = copy.deepcopy(st["default_alloc"])
    else:
        want = C.apply_plan(cur, st["plan"], st["default_alloc"])
    want["prompt_version"] = st.get("prompt_target") or cur["prompt_version"]
    want["schedule"]["lead_preferred_hour"] = copy.deepcopy((cur.get("schedule") or {}).get("lead_preferred_hour")
                                                            or {})
    return snap(want, cur)


def snap(want: dict, cur: dict) -> dict:
    want = copy.deepcopy(want)
    for k, dims in want["allocations"].items():
        for d, w in list(dims.items()):
            cw = (cur["allocations"].get(k) or {}).get(d)
            if cw is not None and policy.tv(w, cw) < 1e-3:
                dims[d] = copy.deepcopy(cw)
    if policy.schedule_equal(want.get("schedule") or {}, cur.get("schedule") or {}):
        want["schedule"] = copy.deepcopy(cur["schedule"])
    if want.get("cohorts") == cur.get("cohorts"):
        want["cohorts"] = copy.deepcopy(cur["cohorts"])
    return want


def partial(cur: dict, want: dict, dims: set[str]) -> dict:
    """`cur` with only `dims` taken from `want`. If the cohorts differ, only the default cohort's allocation
    can move (the other cohorts don't exist yet in `cur`)."""
    out = copy.deepcopy(cur)
    same_cohorts = cur.get("cohorts") == want.get("cohorts")
    keys = policy.cohort_keys(cur) if same_cohorts else {"default"}
    for d in dims & set(policy.AGENT_DIMS):
        for k in keys:
            if k == "default" or d in (want["allocations"].get(k) or {}) or d in (cur["allocations"].get(k) or {}):
                out["allocations"].setdefault(k, {})[d] = copy.deepcopy(policy.dist(want, k, d))
    if "schedule" in dims and same_cohorts:
        out["schedule"] = copy.deepcopy(want["schedule"])
    return snap(out, cur)


# ---------------------------------------------------------------- proposal content (C10)
def active_leads(counters: Counters, wid: str, today: int, cadence_days: int) -> int:
    days = list(range(today - cadence_days + 1, today + 1))
    uploaded = counters.get(wid, days, "U|ALL")[0]
    engaged = counters.get(wid, days, "ALL")[2]
    return max(uploaded - engaged, 0)


def proposal_fields(wf, cur: dict, want: dict, dims: list[str], st: dict, counters: Counters, today: int,
                    evidence: dict) -> dict:
    wid, spec = wf["workflow_id"], wf
    diff, dry, lines = {}, {}, []
    days = window(today, S.WINDOW_DAYS)
    exp_old = exp_new = None
    for d in dims:
        if d in ("welcome_message", "voice"):
            a, b = policy.dist(cur, "default", d), policy.dist(want, "default", d)
            diff[d] = {"default": {"from": a, "to": b}}
            seg = {k: v[d] for k, v in want["allocations"].items() if k != "default" and d in v}
            if seg:
                diff[d]["cohorts"] = seg
            arms = arm_counts(counters, wid, days, d, sorted(set(a) | set(b)))
            table, unsupported = {}, []
            o_sum = n_sum = 0.0
            for o in sorted(set(a) | set(b)):
                e, c, _ = arms[o]
                rate = e / c if c else None
                table[o] = {"engaged": e, "connected": c, "rate": None if rate is None else round(rate, 4),
                            "share_now": a.get(o, 0), "share_proposed": b.get(o, 0)}
                if c < 50:
                    unsupported.append(o)
                elif rate is not None:
                    o_sum += a.get(o, 0) * rate
                    n_sum += b.get(o, 0) * rate
            dry[d] = {"replay_window_days": days, "by_option": table,
                      "no_support": unsupported,
                      "note": "options without data are exploration: no estimate is claimed for them"}
            if not unsupported and o_sum > 0:
                dry[d]["expected_engagement_now"] = round(o_sum, 4)
                dry[d]["expected_engagement_proposed"] = round(n_sum, 4)
                exp_old = (exp_old or 0) + o_sum
                exp_new = (exp_new or 0) + n_sum
            lines.append(f"{d}: " + ", ".join(f"{o} {a.get(o, 0):.0%}→{b.get(o, 0):.0%}" for o in sorted(set(a) | set(b))))
        elif d == "caller_id":
            a, b = policy.dist(cur, "default", d), policy.dist(want, "default", d)
            diff[d] = {"default": {"from": a, "to": b}}
            dry[d] = {"lifetime_dials": evidence.get("did_dials", {}), "recent_health": evidence.get("did_health", {})}
            lines.append("caller ID: " + ", ".join(f"{o} {a.get(o, 0):.0%}→{b.get(o, 0):.0%}" for o in sorted(set(a) | set(b))))
        elif d == "cohorts":
            diff[d] = {"from": [c["id"] for c in cur["cohorts"]], "to": [c["id"] for c in want["cohorts"]],
                       "definitions": {c["id"]: c["where"] for c in want["cohorts"]}}
            lines.append(f"cohorts: {len(cur['cohorts'])}→{len(want['cohorts'])}")
        elif d == "schedule":
            groups = (st.get("plan") or {}).get("groups") or []
            diff[d] = {"hour_weights": want["schedule"]["hour_weights"]}
            dry[d] = {"method": "history pickup by hour per group, relative to the group's own average",
                      "groups": [{k: g[k] for k in ("id", "values", "peak_hour", "dials")} for g in groups],
                      "evidence": (st.get("plan") or {}).get("evidence")}
            lines.append("call-time weights for " + ", ".join(f"{'/'.join(g['values'])} (peak {g['peak_hour']}:00)"
                                                              for g in groups))
        elif d == "prompt_version":
            diff[d] = {"from": cur["prompt_version"], "to": want["prompt_version"]}
            dry[d] = {"validation": evidence.get("validation"), "incident": evidence.get("incident")}
            lines.append(f"prompt {cur['prompt_version']}→{want['prompt_version']}")
    cadence = len(spec["cadence"])
    active = active_leads(counters, wid, today, cadence)
    new_24h = int(spec["daily_batch"]["size"])
    affected = int(active + new_24h)  # every change here touches the default cohort, i.e. everyone
    # impact: conservative, comparable across workflows. 0.5 ≈ a credible +5% on the primary KPI.
    impact = 0.05
    if exp_old and exp_new:
        impact = max(impact, min(max((exp_new / exp_old - 1) / 0.10, 0.0), 1.0))
    for u in st.get("urgent") or []:
        impact = max(impact, u.get("impact", 0.5))
    since = st.get("last_applied_minute", -10 ** 9)
    notes = [n for n in (st.get("notes") or []) if n.get("minute", 0) >= since] or (st.get("notes") or [])[-3:]
    rationale = " | ".join(n["why"] for n in notes[-8:] if n.get("why")) or "keep exploring within safe shares"
    summary = "; ".join(lines)[:300]
    return {"change_summary": summary or "config update", "diff": diff or {"note": "see summary"},
            "affected_leads": affected,
            "dry_run": dry or {"note": "no replayable dimension changed"},
            "impact_score": round(float(impact), 3), "rationale": rationale[:2000],
            "evidence": {k: v for k, v in evidence.items() if k in ("validation_run_id", "incident", "detector")}}
