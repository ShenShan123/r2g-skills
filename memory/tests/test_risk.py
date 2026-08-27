"""Risk stratification (design doc 8, test list 27.1).

Created regressions (PASS->FAIL) and newly observed failures (N/A->FAIL) are
recorded per-rule with activation contexts; v1 never auto-promotes a context
predicate to a hard precondition.
"""
from __future__ import annotations

from tehm.crystallization.risk import RISK_KINDS, stratify_rule_risk


def _t(tid, *, created=None, newly=None) -> dict:
    return {
        "transition_id": tid,
        "observation_delta": {
            "original_failure": "REMOVED",
            "created_regressions": created or [],
            "newly_observed_failures": newly or [],
        },
    }


def test_clean_sources_no_risk():
    profile = stratify_rule_risk({}, [_t("t1"), _t("t2")])
    assert profile == []


def test_created_regression_recorded():
    profile = stratify_rule_risk({}, [_t("t1", created=["lvs_clean"])])
    assert len(profile) == 1
    entry = profile[0]
    assert entry["risk"] == "CREATED_REGRESSION"
    assert entry["support"] == 1
    assert entry["status"] == "CONTEXT_DEPENDENT"  # never auto-promoted (8)
    assert entry["contexts"][0]["transition_id"] == "t1"
    assert entry["contexts"][0]["details"] == ["lvs_clean"]


def test_newly_observed_failure_recorded_separately():
    profile = stratify_rule_risk({}, [_t("t1", newly=["new_timing"])])
    assert len(profile) == 1
    assert profile[0]["risk"] == "NEWLY_OBSERVED_FAILURE"


def test_both_kinds_aggregated_per_rule():
    profile = stratify_rule_risk({}, [
        _t("t1", created=["lvs_clean"]),
        _t("t2", newly=["new_timing"]),
    ])
    kinds = {p["risk"] for p in profile}
    assert kinds == {"CREATED_REGRESSION", "NEWLY_OBSERVED_FAILURE"}
    assert all(p["status"] == "CONTEXT_DEPENDENT" for p in profile)


def test_risk_kinds_constant():
    assert RISK_KINDS == ("CREATED_REGRESSION", "NEWLY_OBSERVED_FAILURE")
