"""Primary effect key determinism (design doc 6.2, test list 27.1).

Same action + observation delta + verification + coarse structure -> the same
effect key; any change -> a different key. This is the Phase 4 Canon seed.
"""
from __future__ import annotations

from tehm.canonical.transition import (
    Action,
    ObservationDelta,
    primary_effect_key,
)
from tehm.canonical.verifier import VerifierSnapshot


def _args(**kw):
    action = Action(domain="signoff.REPAIR_ACTION",
                    transformation_family="ANTENNA_DIODE_REPAIR",
                    payload={"rerun_from": "place", "recheck": "drc"})
    delta = ObservationDelta(
        original_failure="REMOVED",
        first_divergence={"before": 12, "after": None},
        failing_tests={"before": 1, "after": 0},
        created_regressions=[],
        newly_observed_failures=[],
    )
    verifier = VerifierSnapshot(verdict="PASS", oracle_type="REGRESSION",
                                confidence_tier="R", obligation_coverage=1.0)
    coarse = {"register_boundary_changed": False,
              "dependency_cone_changed": True, "rerun_from": "place"}
    base = {"action": action, "delta": delta, "verifier": verifier,
            "coarse_structural_delta": coarse}
    base.update(kw)
    return base


def test_same_input_same_key():
    a = primary_effect_key(**_args())
    b = primary_effect_key(**_args())
    assert a == b
    assert a.startswith("effect_")


def test_different_family_different_key():
    a = primary_effect_key(**_args())
    action2 = Action(domain="signoff.REPAIR_ACTION",
                     transformation_family="DENSITY_RELIEF",
                     payload={"rerun_from": "place", "recheck": "drc"})
    b = primary_effect_key(**_args(action=action2))
    assert a != b


def test_different_outcome_signal_different_key():
    a = primary_effect_key(**_args())
    verifier2 = VerifierSnapshot(verdict="FAIL", oracle_type="REGRESSION",
                                 confidence_tier="R", obligation_coverage=1.0)
    b = primary_effect_key(**_args(verifier=verifier2))
    assert a != b


def test_created_regression_does_not_change_primary_key():
    """Design doc 6.2: created regression is NOT a primary key — it goes to risk
    stratification. The effect key must stay stable across created/newly-observed
    signals (they are orthogonal risk axes, not effect-grouping axes)."""
    a = primary_effect_key(**_args())
    delta2 = ObservationDelta(
        original_failure="REMOVED",
        first_divergence={"before": 12, "after": None},
        failing_tests={"before": 1, "after": 0},
        created_regressions=["lvs_clean"],
        newly_observed_failures=["new_timing_violation"],
    )
    b = primary_effect_key(**_args(delta=delta2))
    assert a == b
