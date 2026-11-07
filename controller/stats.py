"""Small, dependency-free statistics. Every decision that picks a winner goes through here.

Determinism: Monte Carlo draws use a seed derived from what is being decided, so the same data always gives
the same decision (constraint 4: winner selection is statistical and deterministic).
"""
from __future__ import annotations

import hashlib
import math

import numpy as np


def seed_for(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a rate. Better than p ± 1.96·se at small n or rates near 0/1."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def posterior_draws(arms: dict[str, tuple[float, float]], seed: int, draws: int = 20000) -> dict[str, np.ndarray]:
    """Beta(1 + successes, 1 + failures) draws per arm. arms = {name: (successes, trials)}. Counts may be
    fractional (time-decayed), which a Beta handles fine."""
    rng = np.random.default_rng(seed)
    return {a: rng.beta(1 + s, 1 + max(n - s, 0), draws) for a, (s, n) in sorted(arms.items())}


def prob_best(arms: dict[str, tuple[int, int]], seed: int, draws: int = 20000) -> dict[str, float]:
    """P(each arm has the highest true rate)."""
    if not arms:
        return {}
    d = posterior_draws(arms, seed, draws)
    names = list(d)
    m = np.vstack([d[a] for a in names])
    wins = np.bincount(m.argmax(axis=0), minlength=len(names))
    return {a: float(w) / draws for a, w in zip(names, wins)}


def compare(a: tuple[int, int], b: tuple[int, int], seed: int, draws: int = 20000) -> dict:
    """Challenger a vs incumbent b: P(a > b), expected loss of choosing a, and the relative lift with a 90%
    credible interval."""
    d = posterior_draws({"a": a, "b": b}, seed, draws)
    diff = d["a"] - d["b"]
    rel = d["a"] / np.maximum(d["b"], 1e-9) - 1
    return {"p_better": float((diff > 0).mean()),
            "loss_if_choose_a": float(np.maximum(-diff, 0).mean()),
            "loss_if_keep_b": float(np.maximum(diff, 0).mean()),
            "lift": float(np.median(rel)), "lift_lo": float(np.quantile(rel, 0.05)),
            "lift_hi": float(np.quantile(rel, 0.95))}


def thompson_weights(arms: dict[str, tuple[int, int]], seed: int, floor: float, cap: float,
                     draws: int = 20000) -> dict[str, float]:
    """Traffic share ∝ P(best), clipped to [floor, cap] and renormalised. The floor keeps learning alive;
    the cap stops a lucky early streak from taking all traffic."""
    pb = prob_best(arms, seed, draws)
    w = {a: min(max(p, floor), cap) for a, p in pb.items()}
    s = sum(w.values())
    return {a: v / s for a, v in w.items()}


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X ≥ k) for X ~ Binomial(n, p). Exact, in log space so n in the thousands is fine."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
            for i in range(k, n + 1)]
    m = max(logs)
    return min(1.0, math.exp(m) * sum(math.exp(x - m) for x in logs))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k)."""
    return 1.0 - binom_sf(k + 1, n, p)
