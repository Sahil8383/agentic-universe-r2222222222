"""Config arithmetic and governance rules, written from docs/SIMULATOR.md (the contract), not from sim code.

- what counts as a changed dimension and how big an allocation shift is (total-variation distance);
- whether a change may be auto-applied, and how to cut a big change into allowed steps;
- when a request can be sent so that the approver decides before it expires and we can apply in time.
"""
from __future__ import annotations

import copy
import hashlib
import json

AGENT_DIMS = ("welcome_message", "voice", "caller_id")
CHANGE_DIMS = ("cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule")
EPS = 1e-6
MIN_PER_DAY = 1440


# ---------------------------------------------------------------- canonical form
def norm(weights: dict[str, float]) -> dict[str, float]:
    """Drop zero weights, normalise to 1, round so the same intent always serialises the same way."""
    w = {o: float(v) for o, v in weights.items() if v > EPS}
    s = sum(w.values())
    if s <= 0:
        raise ValueError("weights sum to 0")
    out = {o: round(v / s, 4) for o, v in sorted(w.items())}
    return {o: v for o, v in out.items() if v > 0}


def canonical(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"))


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(canonical(cfg).encode()).hexdigest()[:16]


def dist(cfg: dict, cohort: str, dim: str) -> dict[str, float]:
    """The distribution a lead in `cohort` is dialled with: the cohort's own, else default's."""
    return (cfg["allocations"].get(cohort) or {}).get(dim) or cfg["allocations"]["default"][dim]


def tv(a: dict[str, float], b: dict[str, float]) -> float:
    sa, sb = sum(a.values()) or 1, sum(b.values()) or 1
    return 0.5 * sum(abs(a.get(o, 0) / sa - b.get(o, 0) / sb) for o in set(a) | set(b))


def cohort_keys(*cfgs) -> set[str]:
    keys = {"default"}
    for c in cfgs:
        keys |= {x["id"] for x in c.get("cohorts", [])}
        keys |= set(c.get("allocations", {}))
    return keys


def changed_dims(old: dict, new: dict) -> list[str]:
    ch = set()
    if old.get("cohorts", []) != new.get("cohorts", []):
        ch.add("cohorts")
    for k in cohort_keys(old, new):
        for d in AGENT_DIMS:
            if tv(dist(old, k, d), dist(new, k, d)) > EPS:
                ch.add(d)
    if old.get("prompt_version") != new.get("prompt_version"):
        ch.add("prompt_version")
    if not schedule_equal(old.get("schedule") or {}, new.get("schedule") or {}):
        ch.add("schedule")
    return [d for d in CHANGE_DIMS if d in ch]


def schedule_equal(a: dict, b: dict) -> bool:
    ha, hb = a.get("hour_weights") or {}, b.get("hour_weights") or {}
    if set(ha) != set(hb):
        return False
    for k in ha:
        ta, tb = {str(h): float(w) for h, w in ha[k].items()}, {str(h): float(w) for h, w in hb[k].items()}
        if set(ta) != set(tb) or any(abs(ta[h] - tb[h]) > 1e-3 for h in ta):
            return False
    return (a.get("lead_preferred_hour") or {}) == (b.get("lead_preferred_hour") or {})


def max_shift(old: dict, new: dict) -> float:
    return max((tv(dist(old, k, d), dist(new, k, d)) for k in cohort_keys(old, new) for d in AGENT_DIMS),
               default=0.0)


# ---------------------------------------------------------------- auto-apply
def auto_allowed(gov: dict, dims: list[str]) -> bool:
    aa = gov.get("auto_apply") or {}
    return bool(aa.get("enabled")) and bool(dims) and all(d in (aa.get("allowed_dims") or []) for d in dims)


def step_toward(old: dict, target: dict, limit: float) -> dict:
    """A config that moves every allocation from `old` toward `target` by the same fraction, so the largest
    total-variation shift is at most `limit`. Non-allocation parts are taken from `target`."""
    shift = max_shift(old, target)
    alpha = 1.0 if shift <= limit + EPS else (limit * 0.999) / shift
    out = copy.deepcopy(target)
    if alpha >= 1.0:
        return out
    for k in cohort_keys(old, target):
        for d in AGENT_DIMS:
            explicit = d in (target["allocations"].get(k) or {}) or d in (old["allocations"].get(k) or {})
            if not explicit:
                continue
            a, b = dist(old, k, d), dist(target, k, d)
            mix = {o: (1 - alpha) * a.get(o, 0) + alpha * b.get(o, 0) for o in set(a) | set(b)}
            out["allocations"].setdefault(k, {})[d] = {o: round(v, 6) for o, v in sorted(mix.items()) if v > 1e-6}
    return out


# ---------------------------------------------------------------- approver timing
def next_working(t: int, hours) -> int:
    """First minute ≥ t at which the approver is at work (they decide only inside approver_hours)."""
    lo, hi = hours
    day, mod = divmod(t, MIN_PER_DAY)
    if mod < lo * 60:
        return day * MIN_PER_DAY + lo * 60
    if mod >= hi * 60:
        return (day + 1) * MIN_PER_DAY + lo * 60
    return t


def worst_decision_minute(now: int, gov: dict) -> int:
    return next_working(now + int(gov["approver_latency_minutes"][1]), gov["approver_hours"])


def submit_window_ok(now: int, gov: dict, not_before_hour: int | None, urgent: bool) -> tuple[bool, str]:
    """Can a request go out now? Only while the approver is at work (a request sent at night just waits and
    burns TTL), only after the client's preferred hour, and only if even the slowest decision lands before the
    request expires. A decision always lands inside approver hours, where we tick every 15 minutes, so the
    2 h apply window is never at risk."""
    lo, hi = gov["approver_hours"]
    hour = (now % MIN_PER_DAY) // 60
    if not lo <= hour < hi:
        return False, "approver is not at work"
    if not urgent and not_before_hour is not None and hour < not_before_hour:
        return False, f"client asked for no requests before {not_before_hour}:00"
    worst = worst_decision_minute(now, gov)
    if worst > now + int(gov["proposal_ttl_hours"] * 60):
        return False, "worst-case decision would land after the TTL (it would expire)"
    return True, "ok"


def is_dialling(now: int, window) -> bool:
    h = (now % MIN_PER_DAY) // 60
    return window[0] <= h < window[1]
