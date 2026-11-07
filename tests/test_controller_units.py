"""Unit tests for the parts where a silent bug would be expensive: statistics, policy arithmetic, and the
governance state machine (against a fake simulator that misbehaves on purpose).

Run: python -m pytest -q tests
"""

import numpy as np
import pytest

from controller import policy, stats
from controller.client import SimError
from controller.cohorts import apply_plan, build_config_parts, cohort_of
from controller.governance import Governance
from controller.store import Store


# ---------------------------------------------------------------- stats
def test_wilson_matches_known_values():
    lo, hi = stats.wilson(47, 90)
    assert (round(lo, 3), round(hi, 3)) == (0.420, 0.622)


def test_compare_is_deterministic_and_sane():
    a, b = (600, 1000), (550, 1000)
    r1, r2 = stats.compare(a, b, seed=7), stats.compare(a, b, seed=7)
    assert r1 == r2                                # same data + same seed = same decision
    assert r1["p_better"] > 0.95 and r1["lift"] > 0.05
    assert stats.compare(b, a, seed=7)["p_better"] < 0.05


def test_binomial_tails():
    assert stats.binom_sf(0, 10, 0.3) == 1.0
    assert stats.binom_sf(11, 10, 0.3) == 0.0
    # 14 flags in 200 calls when the reference rate is 1.5% is very surprising
    assert stats.binom_sf(14, 200, 0.015) < 1e-3
    # 4 in 200 is not
    assert stats.binom_sf(4, 200, 0.015) > 0.1
    assert abs(stats.binom_cdf(3, 10, 0.5) - 176 / 1024) < 1e-9


def test_fractional_counts_are_accepted():
    p = stats.compare((55.5, 100.25), (50.0, 100.0), seed=1)["p_better"]
    assert 0 < p < 1


def test_aa_false_promotion_rate_is_low():
    """A/A check: two identical arms, the promotion rule (P_PROMOTE, expected loss, decayed counts) applied
    every day for 8 days, i.e. with peeking. It should crown a false winner rarely (measured: ~3%)."""
    from controller import settings as S
    rng = np.random.default_rng(0)
    false, trials = 0, 300
    for t in range(trials):
        ea, eb = [], []
        for d in range(8):
            ea.append(rng.binomial(60, 0.55))
            eb.append(rng.binomial(900, 0.55))
            if (d + 1) * 60 < S.MIN_ARM_N:
                continue
            ks = range(max(0, d - S.WINDOW_DAYS + 1), d + 1)
            w = {k: 0.5 ** ((d - k) / S.HALF_LIFE_DAYS) for k in ks}
            a = (sum(w[k] * ea[k] for k in ks), sum(w[k] * 60 for k in ks))
            b = (sum(w[k] * eb[k] for k in ks), sum(w[k] * 900 for k in ks))
            r = stats.compare(a, b, seed=stats.seed_for(t, d), draws=4000)
            if r["p_better"] >= S.P_PROMOTE and r["loss_if_choose_a"] < S.MAX_EXPECTED_LOSS:
                false += 1
                break
    assert false / trials < 0.05


# ---------------------------------------------------------------- policy
def _cfg(voice, did=None, cohorts=None):
    return {"cohorts": cohorts or [],
            "allocations": {"default": {"welcome_message": {"W1": 1.0}, "voice": voice,
                                        "caller_id": did or {"D1": 0.5, "D2": 0.5}}},
            "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}


def test_snap_removes_float_noise_before_comparing():
    """changed_dims mirrors the simulator's near-exact comparison on purpose; planner.snap is what makes float
    noise vanish, so a 0.3333-vs-1/3 difference never goes out as a 'voice change'."""
    from controller.planner import snap
    live, ours = _cfg({"V1": 1 / 3, "V2": 2 / 3}), _cfg({"V1": 0.3333, "V2": 0.6667})
    assert policy.changed_dims(live, ours) == ["voice"]
    assert policy.changed_dims(live, snap(ours, live)) == []
    assert policy.changed_dims(live, snap(_cfg({"V1": 0.5, "V2": 0.5}), live)) == ["voice"]


def test_step_toward_respects_max_shift():
    old, target = _cfg({"V1": 1.0}, {"D1": 1.0}), _cfg({"V1": 1.0}, {"D2": 1.0})
    step = policy.step_toward(old, target, 0.2)
    assert policy.max_shift(old, step) <= 0.2 + 1e-9
    assert policy.max_shift(old, step) > 0.19          # and it actually moves as far as allowed
    assert policy.step_toward(old, target, 1.0) == target


def test_submit_window_respects_ttl_and_hours():
    gov = {"approver_hours": [10, 19], "approver_latency_minutes": [20, 120], "proposal_ttl_hours": 12}
    day = 3 * 1440
    assert policy.submit_window_ok(day + 9 * 60, gov, None, False)[0] is False       # approver not at work
    assert policy.submit_window_ok(day + 11 * 60, gov, None, False)[0] is True
    # 18:00 + 120 min → decided 10:00 next day = 16 h later > 12 h TTL → would expire
    ok, why = policy.submit_window_ok(day + 18 * 60, gov, None, True)
    assert ok is False and "TTL" in why
    assert policy.submit_window_ok(day + 10 * 60 + 30, gov, 11, False)[0] is False   # client: not before 11
    assert policy.submit_window_ok(day + 10 * 60 + 30, gov, 11, True)[0] is True     # incidents may go


# ---------------------------------------------------------------- cohorts
def test_cohort_cross_product_is_first_match_correct():
    plan = {"schedule_feature": "age_band",
            "groups": [{"id": "g0", "values": ["18-30"], "hour_weights": {"9": 0.8}},
                       {"id": "g1", "values": ["31-45", "46-60", "60+"], "hour_weights": {"9": 1.2}}],
            "segments": [{"id": "seg_lang_ta", "feature": "lang", "value": "ta", "alloc": {"voice": {"V2": 1.0}}}]}
    cfg = apply_plan(_cfg({"V1": 1.0}), plan, _cfg({"V1": 1.0})["allocations"]["default"])
    assert cohort_of(cfg, {"lang": "ta", "age_band": "60+"}) == "seg_lang_ta__g1"
    assert cohort_of(cfg, {"lang": "hi", "age_band": "18-30"}) == "g0"
    assert cfg["allocations"]["seg_lang_ta__g1"]["voice"] == {"V2": 1.0}
    assert cfg["schedule"]["hour_weights"]["seg_lang_ta__g1"] == {"9": 1.2}
    assert len(build_config_parts(plan)[0]) <= 20


# ---------------------------------------------------------------- governance against a fake simulator
class FakeSim:
    """Just enough of the approvals API, with switches to misbehave."""

    def __init__(self):
        self.version = 1
        self.approvals = {}
        self.history = [{"version": 1, "actor": "business_default", "changed_dims": [], "proposal_id": None,
                         "approval_id": None, "applied_at": None}]
        self.pids = set()
        self.lose_response_once = False   # simulate a crash after the simulator acted
        self.calls = 0

    def get(self, path, **params):
        if path == "/approvals":
            return [a for a in self.approvals.values() if a["workflow_id"] == params.get("workflow_id")]
        if path.endswith("/config/history"):
            return self.history
        raise AssertionError(path)

    def post(self, path, body, idempotent):
        if path == "/approvals":
            if body["proposal_id"] in self.pids:
                raise SimError(409, "duplicate_proposal_id", body["proposal_id"])
            if body["base_version"] != self.version:
                raise SimError(409, "stale_base_version", {"current_version": self.version})
            self.pids.add(body["proposal_id"])
            aid = f"apr_{len(self.approvals) + 1:05d}"
            self.approvals[aid] = {"approval_id": aid, "proposal_id": body["proposal_id"],
                                   "workflow_id": body["workflow_id"], "status": "pending",
                                   "base_version": body["base_version"], "changed_dims": ["voice"],
                                   "expires_at": "x"}
            if self.lose_response_once:
                self.lose_response_once = False
                raise SimError(503, "unreachable", "response lost")
            return self.approvals[aid]
        if path.endswith("/apply"):
            a = self.approvals[body["approval_id"]]
            if a["status"] != "approved":
                raise SimError(409, "not_approved", {"status": a["status"]})
            if a["base_version"] != self.version:
                raise SimError(409, "stale_base_version", {"current_version": self.version})
            self.version += 1
            a["status"] = "applied"
            self.history.append({"version": self.version, "actor": "controller_approved", "changed_dims": ["voice"],
                                 "proposal_id": a["proposal_id"], "approval_id": a["approval_id"]})
            return {"workflow_id": "wf", "version": self.version, "changed_dims": ["voice"]}
        raise AssertionError(path)


FIELDS = {"change_summary": "s", "diff": {"x": 1}, "affected_leads": 1, "dry_run": {"x": 1}, "impact_score": 0.1,
          "rationale": "r"}


@pytest.fixture()
def gov(tmp_path):
    sim = FakeSim()
    g = Governance(Store(str(tmp_path / "t.db")), sim)
    return g, sim


def _state(g, pid):
    return g.store.one("SELECT state FROM proposals WHERE proposal_id=?", pid)


def test_happy_path_and_apply_once(gov):
    g, sim = gov
    pid = g.submit_approval(600, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    assert _state(g, pid) == "pending"
    sim.approvals["apr_00001"]["status"] = "approved"
    assert g.reconcile(615, "wf") == [2]
    assert _state(g, pid) == "applied"
    assert g.reconcile(630, "wf") == []          # nothing applied twice
    assert sim.version == 2


def test_lost_response_is_adopted_not_resent(gov):
    """The simulator created the approval but our process never saw the answer (crash / network)."""
    g, sim = gov
    sim.lose_response_once = True
    pid = g.submit_approval(600, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    assert _state(g, pid) == "submitting"        # unknown outcome: never 'failed', never a fresh proposal
    g.reconcile(615, "wf")                       # resends the same id → 409 duplicate → adopt
    assert _state(g, pid) == "pending"
    assert g.store.one("SELECT approval_id FROM proposals WHERE proposal_id=?", pid) == "apr_00001"
    assert len(sim.approvals) == 1               # one approval, not two


def test_stale_on_apply_after_supervisor_edit(gov):
    g, sim = gov
    pid = g.submit_approval(600, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    sim.version = 2                              # a supervisor edited in between
    sim.approvals["apr_00001"]["status"] = "approved"
    assert g.reconcile(615, "wf") == []
    assert _state(g, pid) == "stale"


def test_applied_before_crash_is_recognised(gov):
    g, sim = gov
    pid = g.submit_approval(600, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    sim.approvals["apr_00001"]["status"] = "approved"
    sim.post("/workflows/wf/apply", {"approval_id": "apr_00001"}, True)    # applied, but we "crashed"
    g.store.db.execute("UPDATE proposals SET state='applying' WHERE proposal_id=?", (pid,))
    g.reconcile(615, "wf")
    assert _state(g, pid) == "applied"
    assert sim.version == 2                      # not applied twice


def test_reproposing_same_config_gets_a_new_id(gov):
    g, sim = gov
    p1 = g.submit_approval(600, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    sim.approvals["apr_00001"]["status"] = "rejected"
    g.reconcile(615, "wf")
    p2 = g.submit_approval(700, "wf", "batch", 1, _cfg({"V2": 1.0}), FIELDS)
    assert p1 != p2 and _state(g, p2) == "pending"
    assert g.requests_last_24h("wf", 700) == 2   # both count toward approver fatigue
