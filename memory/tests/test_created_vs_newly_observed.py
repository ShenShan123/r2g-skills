"""Created regression vs newly observed failure (design doc 8, 4.1).

A created regression (``PASS -> FAIL`` on a previously-good oracle) must map to
outcome REGRESSION; a newly observed failure (``N/A -> FAIL`` from a new oracle
or exposed deeper bug) maps to PARTIAL. The two are stored separately and never
merged.
"""
from __future__ import annotations

from tehm.canonical.transition import (
    Action,
    CanonicalTransition,
    ObservationDelta,
    classify_outcome,
)
from tehm.canonical.verifier import VerifierSnapshot


def _transition(delta, verifier):
    return CanonicalTransition(
        source_state_id="state_a",
        target_state_id="state_b",
        action=Action(domain="signoff.REPAIR_ACTION",
                      transformation_family="DENSITY_RELIEF",
                      payload={"config_edits": {"PLACE_DENSITY_LB_ADDON": "0.10"}}),
        observation_delta=delta,
        verifier=verifier,
        outcome=classify_outcome(delta, verifier),
        primary_effect_key="effect_x",
        created_regressions=list(delta.created_regressions),
        newly_observed=list(delta.newly_observed_failures),
    )


def _verifier(verdict="PASS"):
    return VerifierSnapshot(verdict=verdict, oracle_type="REGRESSION",
                            confidence_tier="R", obligation_coverage=1.0)


def test_created_regression_maps_to_regression():
    delta = ObservationDelta(
        original_failure="REMOVED",
        created_regressions=["lvs_clean"],
        newly_observed_failures=[],
    )
    t = _transition(delta, _verifier())
    assert t.outcome == "REGRESSION"
    assert t.created_regressions == ["lvs_clean"]
    assert t.newly_observed == []


def test_newly_observed_maps_to_partial():
    delta = ObservationDelta(
        original_failure="REMOVED",
        created_regressions=[],
        newly_observed_failures=["new_timing_violation"],
    )
    t = _transition(delta, _verifier())
    assert t.outcome == "PARTIAL"
    assert t.created_regressions == []
    assert t.newly_observed == ["new_timing_violation"]


def test_clean_pass():
    delta = ObservationDelta(
        original_failure="REMOVED",
        created_regressions=[],
        newly_observed_failures=[],
    )
    assert _transition(delta, _verifier()).outcome == "PASS"


def test_fail_verdict_overrides():
    delta = ObservationDelta(
        original_failure="REMOVED",
        created_regressions=[],
        newly_observed_failures=[],
    )
    assert _transition(delta, _verifier("FAIL")).outcome == "FAIL"


def test_regression_priority_over_verdict():
    """Created regression is the strongest signal (design doc 8.1)."""
    delta = ObservationDelta(
        original_failure="REMOVED",
        created_regressions=["lvs"],
        newly_observed_failures=[],
    )
    assert _transition(delta, _verifier("FAIL")).outcome == "REGRESSION"
