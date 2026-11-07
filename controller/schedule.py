"""Call-time optimisation (C4), per cohort, learned from history.

Question: do different kinds of people answer at different hours? History ran one config for weeks with the
dialler picking leads at random inside each hour, so pickup-by-hour per feature value is a fair comparison.

1. For each feature (age_band, city_tier, ...), test whether its values have different hour profiles
   (a chi-square test of "same shape" across values). Keep the feature with the strongest, significant split.
2. Merge values with similar shapes until there are ≤3 groups (morning people, evening people, ...).
3. Each group's hour weight = its pickup in that hour ÷ its own average pickup, shrunk toward 1 when data is
   thin and clipped to [0.4, 2.5]. At 09:00 a morning group (1.6) beats an evening group (0.5), so the
   dialler calls morning people first; at 19:00 the reverse. Every hour keeps weight > 0, so no capacity is
   wasted and nobody is starved.
"""
from __future__ import annotations

import math

from . import settings as S
from .ingest import Counters


def _curves(counters: Counters, wid: str, days, feature: str, hours: list[int]):
    """{value: {hour: (dials, connected)}} for one feature."""
    raw = counters.prefix(wid, days, f"HS|{feature}=")
    out: dict[str, dict[int, tuple[int, int]]] = {}
    for key, (d, c, _e, _f) in raw.items():
        _, fv, h = key.split("|")
        v = fv.split("=", 1)[1]
        h = int(h)
        if h in hours:
            out.setdefault(v, {})[h] = (d, c)
    return out


def _shape(cur: dict[int, tuple[int, int]], hours: list[int]) -> dict[int, float]:
    """Pickup in each hour relative to this group's average, shrunk toward 1 when an hour is thin."""
    d_tot = sum(d for d, _ in cur.values())
    c_tot = sum(c for _, c in cur.values())
    mean = c_tot / d_tot if d_tot else 0.0
    if mean <= 0:
        return {h: 1.0 for h in hours}
    out = {}
    for h in hours:
        d, c = cur.get(h, (0, 0))
        r = (c + S.SCHEDULE_SHRINK_N * mean) / (d + S.SCHEDULE_SHRINK_N)
        out[h] = r / mean
    return out


def _homogeneity(curves: dict[str, dict[int, tuple[int, int]]], hours: list[int]) -> tuple[float, float]:
    """Chi-square statistic for 'all values share one hour profile' (pickup level allowed to differ), and its
    degrees of freedom."""
    tot_d = {h: sum(c.get(h, (0, 0))[0] for c in curves.values()) for h in hours}
    tot_c = {h: sum(c.get(h, (0, 0))[1] for c in curves.values()) for h in hours}
    all_d, all_c = sum(tot_d.values()), sum(tot_c.values())
    if not all_d or not all_c:
        return 0.0, 1.0
    stat = 0.0
    for cur in curves.values():
        vd = sum(d for d, _ in cur.values())
        vc = sum(c for _, c in cur.values())
        level = (vc / vd) / (all_c / all_d) if vd else 0
        for h in hours:
            d, c = cur.get(h, (0, 0))
            if not d or not tot_d[h]:
                continue
            exp = d * (tot_c[h] / tot_d[h]) * level
            if exp > 0:
                stat += (c - exp) ** 2 / exp
    df = max((len(curves) - 1) * (len(hours) - 1), 1)
    return stat, df


def learn_groups(counters: Counters, wid: str, days, dialling_window, features: list[str]) -> dict | None:
    """Returns {"feature", "groups": [...], "evidence": {...}} or None when no split is significant."""
    hours = list(range(dialling_window[0], dialling_window[1]))
    best = None
    tested = {}
    for f in features:
        curves = {v: c for v, c in _curves(counters, wid, days, f, hours).items()
                  if sum(d for d, _ in c.values()) >= S.SCHEDULE_MIN_DIALS}
        if len(curves) < 2:
            continue
        stat, df = _homogeneity(curves, hours)
        z = (stat - df) / math.sqrt(2 * df)  # chi-square → approx standard normal
        tested[f] = round(z, 1)
        if z > 3.0 and (best is None or z > best[0]):
            best = (z, f, curves)
    if best is None:
        return None
    z, f, curves = best
    # agglomerative merge of values with the most similar shapes, until ≤ SCHEDULE_GROUPS groups
    groups = [{"values": [v], "cur": dict(c)} for v, c in sorted(curves.items())]

    def dist(a, b):
        sa, sb = _shape(a["cur"], hours), _shape(b["cur"], hours)
        return sum((sa[h] - sb[h]) ** 2 for h in hours)

    while len(groups) > S.SCHEDULE_GROUPS:
        i, j = min(((i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))),
                   key=lambda p: dist(groups[p[0]], groups[p[1]]))
        a, b = groups[i], groups.pop(j)
        for h, (d, c) in b["cur"].items():
            d0, c0 = a["cur"].get(h, (0, 0))
            a["cur"][h] = (d0 + d, c0 + c)
        a["values"] = sorted(a["values"] + b["values"])
    out = []
    for gi, g in enumerate(sorted(groups, key=lambda g: g["values"])):
        shape = _shape(g["cur"], hours)
        w = {str(h): round(min(max(shape[h], S.WEIGHT_MIN), S.WEIGHT_MAX), 2) for h in hours}
        peak = max(hours, key=lambda h: shape[h])
        out.append({"id": f"g{gi}", "values": g["values"], "hour_weights": w, "peak_hour": peak,
                    "dials": sum(d for d, _ in g["cur"].values())})
    return {"feature": f, "groups": out,
            "evidence": {"homogeneity_z_by_feature": tested, "chosen": f, "z": round(z, 1),
                         "rule": "z > 3 (chi-square homogeneity of hour profile, ≈ p < 0.002)"}}
