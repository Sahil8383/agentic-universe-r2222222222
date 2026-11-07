"""THE cohort object (C2). Detection, experiments, the supervisor view and the config all read it from here.

A workflow's cohort plan has two independent parts:
- schedule groups: one feature (e.g. age_band) cut into ≤3 groups that answer the phone at different hours;
  each group gets an hour_weights table;
- experiment segments: single feature values (e.g. lang=ta) where an option beats the workflow-wide choice;
  each segment gets its own allocation.

The simulator gives a lead exactly one cohort (first match wins), so a lead that is both "lang=ta" and in
the "morning" group needs a cohort that is both. We therefore emit the cross-product, segments first:
    seg_ta__g0, seg_ta__g1, seg_ta__g2, g0, g1, g2        (and "default" catches anything left over)
With ≤3 groups and ≤4 segments that's ≤15 cohorts, under the limit of 20.
"""
from __future__ import annotations

import copy


def empty_plan() -> dict:
    return {"schedule_feature": None, "groups": [], "segments": []}
    # groups:   [{"id": "g0", "values": ["60+", "46-60"], "hour_weights": {"9": 1.6, ...}}]
    # segments: [{"id": "seg_lang_ta", "feature": "lang", "value": "ta", "alloc": {"voice": {"V2": 0.9, ...}}}]


def segment_id(feature: str, value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in value)
    return f"seg_{feature}_{safe}"


def build_config_parts(plan: dict) -> tuple[list[dict], dict, dict]:
    """Plan -> (cohorts, extra allocations by cohort id, hour_weights by cohort id)."""
    cohorts, allocs, hours = [], {}, {}
    f = plan.get("schedule_feature")
    groups = plan.get("groups") or []
    for seg in plan.get("segments") or []:
        base_where = {seg["feature"]: [seg["value"]]}
        for g in groups:
            where = copy.deepcopy(base_where)
            if f == seg["feature"]:
                vals = [v for v in g["values"] if v == seg["value"]]
                if not vals:
                    continue
                where[f] = vals
            else:
                where[f] = list(g["values"])
            cid = f"{seg['id']}__{g['id']}"
            cohorts.append({"id": cid, "where": where})
            allocs[cid] = copy.deepcopy(seg["alloc"])
            hours[cid] = dict(g["hour_weights"])
        cid = seg["id"]  # segment leads not covered by any group (only if groups don't cover every value)
        cohorts.append({"id": cid, "where": base_where})
        allocs[cid] = copy.deepcopy(seg["alloc"])
    for g in groups:
        cohorts.append({"id": g["id"], "where": {f: list(g["values"])}})
        hours[g["id"]] = dict(g["hour_weights"])
    return cohorts, allocs, hours


def apply_plan(cfg: dict, plan: dict, default_alloc: dict) -> dict:
    """Return a full config: `cfg` with cohorts / allocations / hour_weights rebuilt from the plan."""
    out = copy.deepcopy(cfg)
    cohorts, allocs, hours = build_config_parts(plan)
    out["cohorts"] = cohorts
    out["allocations"] = {"default": copy.deepcopy(default_alloc), **allocs}
    sched = out.setdefault("schedule", {})
    sched["hour_weights"] = hours
    sched.setdefault("lead_preferred_hour", {})
    return out


def cohort_of(cfg: dict, features: dict[str, str]) -> str:
    """First-match membership, the same rule the simulator documents. Used to label calls and as a check."""
    for c in cfg.get("cohorts", []):
        if all(features.get(k) in vals for k, vals in c["where"].items()):
            return c["id"]
    return "default"


def describe(plan: dict) -> list[str]:
    out = []
    if plan.get("groups"):
        out.append(f"call-time groups by {plan['schedule_feature']}: " +
                   "; ".join(f"{g['id']}={'/'.join(g['values'])}" for g in plan["groups"]))
    for s in plan.get("segments") or []:
        out.append(f"segment {s['feature']}={s['value']}: " +
                   ", ".join(f"{d} {w}" for d, w in s["alloc"].items()))
    return out
