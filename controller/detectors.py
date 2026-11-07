"""Detectors: caller-ID health (C5) and hallucination spikes (C8). Both work on populations, never one call.

Both read the call log for a recent window and compare against a reference measured on comparable calls:
- a caller ID is compared with the OTHER caller IDs in the same hours (randomisation is per dial, so the
  only difference between them is the number itself);
- a prompt's flag rate is compared with the flag rate of the prompt that ran before (or in history), with a
  one-sided binomial test, because the automated flag has false positives and a baseline is never zero.
"""
from __future__ import annotations

from . import settings as S
from .ingest import Counters
from .stats import binom_cdf, binom_sf
from .store import Store

MIN_PER_DAY = 1440


def did_health(store: Store, counters: Counters, wid: str, now: int, probation: dict | None = None) -> dict:
    """Per caller ID: dials, connected, the pickup its peers got in the same hours, the ratio, and a verdict.

    Established numbers are judged on the last DID_WINDOW_MIN (they carry a big share, so 3 h is plenty).
    Numbers on probation (new, small share) are judged on everything since they were introduced, up to two
    days, otherwise a 5% share would never collect enough dials inside a short window.
    verdict: "bad" (move off), "good" (probation passed), or None (keep watching)."""
    probation = probation or {}
    start_of = {d: max(since, now - 2 * MIN_PER_DAY) for d, since in probation.items()}
    oldest = min([now - S.DID_WINDOW_MIN] + list(start_of.values()))
    rows = store.q("SELECT did, minute / 60 AS hb, COUNT(*), SUM(outcome & 1) FROM calls WHERE wid=? AND minute>=? "
                   "GROUP BY did, hb", wid, oldest)
    by_did: dict[str, dict[int, tuple[int, int]]] = {}
    for did, hb, n, c in rows:
        by_did.setdefault(did, {})[hb] = (n, c or 0)
    out = {}
    hist_days = [d for d in counters.days(wid) if d < 0]
    for did, cells in by_did.items():
        start_hb = start_of.get(did, now - S.DID_WINDOW_MIN) // 60
        own = {hb: v for hb, v in cells.items() if hb >= start_hb}
        expected, n_used, c_used = 0.0, 0, 0
        for hb, (n, c) in own.items():
            pn = sum(o.get(hb, (0, 0))[0] for d2, o in by_did.items() if d2 != did and d2 not in probation)
            pc = sum(o.get(hb, (0, 0))[1] for d2, o in by_did.items() if d2 != did and d2 not in probation)
            if pn >= 30:
                rate = pc / pn
            else:  # no established peers in that hour: fall back to the workflow's history for that hour
                hd, hc, _, _ = counters.get(wid, hist_days, f"H|{hb % 24}")
                if hd < 100:
                    continue
                rate = hc / hd
            expected += n * rate
            n_used += n
            c_used += c
        ratio = c_used / expected if expected > 0 else None
        p = binom_cdf(c_used, n_used, min(max(expected / n_used, 1e-6), 1 - 1e-6)) if n_used else 1.0
        verdict = None
        if did in probation:
            if n_used >= S.DID_PROBATION_MIN_DIALS and ratio is not None and ratio < S.DID_PROBATION_BAD_RATIO \
                    and p < S.DID_PROBATION_P:
                verdict = "bad"
            elif n_used >= S.DID_TRUST_DIALS and ratio is not None and ratio >= S.DID_GOOD_RATIO:
                verdict = "good"
        elif n_used >= S.DID_MIN_DIALS and ratio is not None and ratio < S.DID_BAD_RATIO and p < S.DID_P:
            verdict = "bad"
        out[did] = {"dials": n_used, "connected": c_used, "expected_connected": round(expected, 1),
                    "ratio": None if ratio is None else round(ratio, 3), "p_low": p, "verdict": verdict,
                    "flagged": verdict == "bad", "probation": did in probation}
    return out


def did_lifetime_dials(counters: Counters, wid: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, r in counters.prefix(wid, counters.days(wid), "D|").items():
        did = k.split("|")[1]
        out[did] = out.get(did, 0) + r[0]
    return out


def flag_rate(counters: Counters, wid: str, days, prompt: str) -> tuple[int, int]:
    """(flagged, connected) for a prompt over some days."""
    _d, c, _e, f = counters.get(wid, days, f"P|{prompt}")
    return f, c


def hallucination(store: Store, counters: Counters, wid: str, now: int, prompt: str, since: int,
                  reference: tuple[int, int] | None) -> dict:
    """Is the current prompt flagging more than the reference? Counts connected calls since the prompt went
    live (capped at HALLUC_WINDOW_MIN back) and tests k flags out of n against the reference rate."""
    start = max(since, now - S.HALLUC_WINDOW_MIN)
    n, k = store.q("SELECT COUNT(*), COALESCE(SUM((outcome >> 2) & 1), 0) FROM calls WHERE wid=? AND minute>=? "
                   "AND prompt=? AND (outcome & 1)=1", wid, start, prompt)[0]
    if not reference or reference[1] < 200:
        return {"n": n, "k": k, "baseline": None, "p": 1.0, "fire": False}
    base = (reference[0] + 1) / (reference[1] + 2)  # a pinch of smoothing so a 0-flag history isn't "0%"
    p = binom_sf(k, n, base) if n else 1.0
    return {"n": n, "k": k, "rate": round(k / n, 4) if n else None, "baseline": round(base, 4), "p": p,
            "fire": n >= S.HALLUC_MIN_CONNECTED and p < S.HALLUC_P, "window_start": start}
