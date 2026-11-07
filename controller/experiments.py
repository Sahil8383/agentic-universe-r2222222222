"""Welcome-message and voice experiments (C6), promotion and decay rollback (C7).

Design, in one paragraph: every dial already samples welcome and voice independently from the allocation,
so a single config is a factorial experiment; we read both main effects from the same calls. Untested
options get a small share (5% each, ≤15% per dimension), so even a bad one costs < 1% of engagement. A
challenger's share grows only as P(challenger > incumbent) climbs above 0.5, capped at 30%. A challenger is
promoted when P ≥ 0.95, the expected loss of switching is < 0.3 points and both arms have ≥300 connected
calls; the old incumbent stays as a 10% holdout so we can see the winner decay. Losers (P(worse) ≥ 0.95) go
to 0. Decisions read only the last 3 days, because effects change over time.

All decisions are pure functions of the counters and the day, seeded, so they're deterministic and
replayable from the audit log.
"""
from __future__ import annotations

from . import settings as S
from .ingest import Counters
from .stats import compare, seed_for

ENGAGEMENT_DIMS = ("welcome_message", "voice")


def window(today: int, n: int) -> list[int]:
    return list(range(max(0, today - n + 1), today + 1))


def arm_counts(counters: Counters, wid: str, days, dim: str, options, segment: str | None = None,
               half_life: float | None = None, today: int | None = None) -> dict:
    """{option: (engaged, connected, dials)} over the days, workflow-wide or inside one segment.

    With `half_life`, each day's counts are down-weighted by 0.5 ** (age / half_life). Effects in this world
    change over time (novelty wears off), so yesterday should count more than four days ago. Weighted counts
    feed the posteriors; raw counts still gate the minimum sample size."""
    out = {}
    for o in options:
        key = f"AS|{segment}|{dim}={o}" if segment else f"A|{dim}={o}"
        if half_life is None:
            d, c, e, _f = counters.get(wid, days, key)
            out[o] = (e, c, d)
            continue
        e_w = c_w = d_w = 0.0
        for day in days:
            d, c, e, _f = counters.get(wid, [day], key)
            wgt = 0.5 ** ((today - day) / half_life)
            e_w, c_w, d_w = e_w + wgt * e, c_w + wgt * c, d_w + wgt * d
        out[o] = (e_w, c_w, d_w)
    return out


def new_state() -> dict:
    return {"incumbent": None, "promoted": None, "dropped": [], "decay_strikes": 0, "paused": None}


def review_dim(counters: Counters, wid: str, dim: str, options: list[str], alloc: dict, st: dict, today: int,
               guardrail_pickup_drop: float | None) -> tuple[dict, dict, list[dict]]:
    """One daily review of one dimension. Returns (new allocation, new state, decision notes)."""
    st = {**new_state(), **st}
    notes: list[dict] = []
    if st.get("paused"):
        return alloc, st, notes
    inc = st["incumbent"] or max(alloc, key=alloc.get)
    st["incumbent"] = inc
    days = window(today, S.WINDOW_DAYS)
    raw = arm_counts(counters, wid, days, dim, options)
    arms = arm_counts(counters, wid, days, dim, options, half_life=S.HALF_LIFE_DAYS, today=today)
    live = [o for o in options if o not in st["dropped"]]
    seed = seed_for(wid, dim, today)

    # 1. losers out, winners in
    best_challenger, best_cmp = None, None
    cmps = {}
    for o in live:
        if o == inc:
            continue
        e, c, d = arms[o]
        ei, ci, di = arms[inc]
        n_o, n_inc = raw[o][1], raw[inc][1]
        if n_o < 30 or n_inc < 30:
            continue
        cmp = compare((e, c), (ei, ci), seed_for(seed, o))
        cmps[o] = cmp
        if 1 - cmp["p_better"] >= S.P_DROP_LOSER and n_o >= S.MIN_ARM_N / 3:
            st["dropped"].append(o)
            notes.append({"kind": "drop", "dim": dim, "option": o, "vs": inc, **_fmt(cmp, arms[o], arms[inc]),
                          "why": f"P({o} worse than {inc}) = {1 - cmp['p_better']:.3f} ≥ {S.P_DROP_LOSER}"})
            continue
        rec = arm_counts(counters, wid, window(today, S.RECENT_DAYS), dim, [o, inc])
        ok = (cmp["p_better"] >= S.P_PROMOTE and cmp["loss_if_choose_a"] < S.MAX_EXPECTED_LOSS
              and n_o >= S.MIN_ARM_N and n_inc >= S.MIN_ARM_N and _guardrail_ok(raw[o], raw[inc], guardrail_pickup_drop)
              and _still_winning(rec[o], rec[inc], seed_for(seed, o, "recent")))
        if ok and (best_cmp is None or cmp["p_better"] > best_cmp["p_better"]):
            best_challenger, best_cmp = o, cmp

    live = [o for o in options if o not in st["dropped"]]
    if best_challenger:
        notes.append({"kind": "promote", "dim": dim, "option": best_challenger, "vs": inc,
                      **_fmt(best_cmp, arms[best_challenger], arms[inc]),
                      "why": f"P({best_challenger} > {inc}) = {best_cmp['p_better']:.3f} ≥ {S.P_PROMOTE}, expected "
                             f"loss {best_cmp['loss_if_choose_a']:.4f} < {S.MAX_EXPECTED_LOSS}, n ≥ {S.MIN_ARM_N}"})
        st["promoted"] = {"winner": best_challenger, "holdout": inc, "day": today}
        st["incumbent"] = best_challenger
        st["decay_strikes"] = 0
        inc = best_challenger

    # 2. shares: untested at EXPLORE_SHARE, challengers by P(better) with hysteresis and a step cap, holdout
    #    fixed, incumbent gets the rest. Nothing gains traffic unless it is still winning on recent data.
    w: dict[str, float] = {}
    holdout = (st.get("promoted") or {}).get("holdout")
    untested = [o for o in live if o != inc and o != holdout and raw[o][1] < S.MIN_ARM_N]
    explore = min(S.EXPLORE_SHARE, S.EXPLORE_CAP / max(len(untested), 1))
    recent = arm_counts(counters, wid, window(today, S.RECENT_DAYS), dim, options)
    for o in live:
        if o == inc:
            continue
        if o == holdout and holdout in live:
            w[o] = S.HOLDOUT_SHARE
            continue
        old = alloc.get(o, 0.0)
        cmp = cmps.get(o)
        if cmp is None or (o in untested and raw[o][1] < 100):
            w[o] = max(explore, old) if old <= S.CHALLENGER_CAP else explore
            continue
        lo, hi = S.HOLD_BAND
        if lo <= cmp["p_better"] <= hi:
            w[o] = max(old, explore)
            continue
        if cmp["p_better"] < lo:
            target = explore
        else:
            lift = (cmp["p_better"] - hi) / (S.P_PROMOTE - hi)
            target = explore + (S.CHALLENGER_CAP - explore) * min(max(lift, 0.0), 1.0)
        if target > old and not _still_winning(recent[o], recent[inc], seed_for(seed, o, "recent")):
            target = old
        w[o] = round(min(max(target, old - S.MAX_STEP, explore), old + S.MAX_STEP), 3)
    w[inc] = max(1.0 - sum(w.values()), 0.4)
    s = sum(w.values())
    new_alloc = {o: round(v / s, 4) for o, v in sorted(w.items()) if v > 0}
    return new_alloc, st, notes


def _still_winning(a, b, seed) -> bool:
    """Recent-day gate: True unless both arms have enough recent calls and the challenger is behind."""
    (ea, ca, _), (eb, cb, _) = a, b
    if ca < S.RECENT_MIN_N or cb < S.RECENT_MIN_N:
        return True
    return compare((ea, ca), (eb, cb), seed)["p_better"] >= 0.5


def decay_check(counters: Counters, wid: str, dim: str, st: dict, today: int) -> tuple[dict, dict | None]:
    """Is the promoted winner still better than its holdout on recent data? Two weak checks in a row → roll
    back. Returns (new state, rollback note or None)."""
    pr = st.get("promoted")
    if not pr or st.get("paused"):
        return st, None
    days = window(today, S.DECAY_WINDOW_DAYS)
    arms = arm_counts(counters, wid, days, dim, [pr["winner"], pr["holdout"]], half_life=S.HALF_LIFE_DAYS,
                      today=today)
    (ew, cw, _), (eh, ch, _) = arms[pr["winner"]], arms[pr["holdout"]]
    if ch < 60 or cw < 60:
        return st, None
    cmp = compare((ew, cw), (eh, ch), seed_for(wid, dim, "decay", today, cw, ch))
    st = dict(st)
    st["decay_strikes"] = st.get("decay_strikes", 0) + 1 if cmp["p_better"] < S.P_DECAY else 0
    if st["decay_strikes"] < S.DECAY_CHECKS:
        return st, None
    note = {"kind": "rollback", "dim": dim, "option": pr["winner"], "vs": pr["holdout"],
            **_fmt(cmp, arms[pr["winner"]], arms[pr["holdout"]]),
            "why": f"P({pr['winner']} still better than holdout {pr['holdout']}) = {cmp['p_better']:.3f} < "
                   f"{S.P_DECAY} on {S.DECAY_CHECKS} checks in a row (last {S.DECAY_WINDOW_DAYS} days)"}
    st["dropped"] = sorted(set(st.get("dropped", [])) | {pr["winner"]})
    st["incumbent"] = pr["holdout"]
    st["promoted"] = None
    st["decay_strikes"] = 0
    return st, note


def rollback_alloc(alloc: dict, winner: str, holdout: str) -> dict:
    """Winner to 0, its share to the holdout."""
    w = {o: v for o, v in alloc.items() if o != winner}
    w[holdout] = w.get(holdout, 0) + alloc.get(winner, 0)
    s = sum(w.values())
    return {o: round(v / s, 4) for o, v in sorted(w.items())}


def segment_winners(counters: Counters, wid: str, dim: str, options: list[str], global_choice: str, today: int,
                    features: list[str], dropped: list[str]) -> list[dict]:
    """Segments (one feature value) where another option beats the workflow-wide choice, at the stricter
    segment bar. Segment claims are many simultaneous looks, so P ≥ 0.99 instead of 0.95."""
    days = window(today, S.WINDOW_DAYS)
    tot = counters.get(wid, days, "ALL")[1] or 1
    out = []
    for f in features:
        for key, r in counters.prefix(wid, days, f"AS|{f}=").items():
            parts = key.split("|")
            if parts[2] != f"{dim}={global_choice}":
                continue
            fv = parts[1]
            seg_conn = sum(counters.get(wid, days, f"AS|{fv}|{dim}={o}")[1] for o in options)
            if seg_conn / tot < S.MIN_SEGMENT_SHARE:
                continue
            arms = arm_counts(counters, wid, days, dim, options, segment=fv)
            g = arms[global_choice]
            for o in options:
                if o == global_choice or o in dropped or arms[o][1] < S.MIN_ARM_N or g[1] < S.MIN_ARM_N:
                    continue
                cmp = compare(arms[o][:2], g[:2], seed_for(wid, dim, fv, o, today))
                if cmp["p_better"] >= S.P_PROMOTE_SEGMENT and cmp["loss_if_choose_a"] < S.MAX_EXPECTED_LOSS:
                    out.append({"feature": f, "value": fv.split("=", 1)[1], "dim": dim, "option": o,
                                "vs": global_choice, "share": round(seg_conn / tot, 3),
                                **_fmt(cmp, arms[o], g)})
    out.sort(key=lambda x: -x["p_better"])
    return out


def _guardrail_ok(a, b, max_drop: float | None) -> bool:
    """Block a promotion if the challenger's pickup is credibly lower than the incumbent's by more than the
    allowed drop (voice/welcome shouldn't move pickup at all, so this guards against surprises)."""
    if max_drop is None:
        return True
    (_, ca, da), (_, cb, db) = a, b
    if da < 100 or db < 100:
        return True
    cmp = compare((ca, da), (cb, db), seed_for("guard", ca, da, cb, db))
    return cmp["lift_hi"] > -max_drop


def _fmt(cmp: dict, a, b) -> dict:
    return {"p_better": round(cmp["p_better"], 4), "lift": round(cmp["lift"], 4),
            "lift_ci90": [round(cmp["lift_lo"], 4), round(cmp["lift_hi"], 4)],
            "expected_loss": round(cmp["loss_if_choose_a"], 5),
            "a": {"engaged": a[0], "connected": a[1]}, "b": {"engaged": b[0], "connected": b[1]}}
