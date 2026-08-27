"""Primary effect canonicalization (design doc 6.2, test list 27.1).

K_primary = Canon(ΔV_target/preserve, ΔF, ΔC):
  * deterministic (same input -> same key, any change -> different key)
  * CREATED_REGRESSION / NEWLY_OBSERVED are NOT primary keys (risk-stratified)
  * failure deltas are normalized to categories, not instance-specific values
  * config values are NOT part of the key; the transformation family is
"""
from __future__ import annotations

from tehm.canonical.capture import ExecutionRecord, capture
from tehm.canonical.transition import (
    Action,
    ObservationDelta,
    primary_effect_key,
)
from tehm.canonical.verifier import VerifierSnapshot
from tehm.crystallization.effects import (
    effect_key_from_transition_dict,
    normalize_divergence,
    normalize_tests,
)


def _args(action=None, delta=None, verifier=None, coarse=None):
    action = action or Action(
        domain="signoff.REPAIR_ACTION",
        transformation_family="ANTENNA_DIODE_REPAIR",
        payload={"rerun_from": "place", "recheck": "drc",
                 "dependency_cone_changed": True})
    delta = delta or ObservationDelta(
        original_failure="REMOVED",
        first_divergence={"before": 12, "after": None},
        failing_tests={"before": 1, "after": 0},
        created_regressions=[], newly_observed_failures=[])
    verifier = verifier or VerifierSnapshot(
        verdict="PASS", oracle_type="REGRESSION", confidence_tier="R",
        obligation_coverage=1.0)
    return {"action": action, "delta": delta, "verifier": verifier,
            "coarse_structural_delta": coarse}


def test_same_input_same_key():
    a = primary_effect_key(**_args())
    b = primary_effect_key(**_args())
    assert a == b
    assert a.startswith("effect_")


def test_different_family_different_key():
    other = Action(domain="signoff.REPAIR_ACTION",
                   transformation_family="DENSITY_RELIEF",
                   payload={"rerun_from": "place", "recheck": "drc"})
    assert primary_effect_key(**_args(action=other)) != primary_effect_key(**_args())


def test_different_verdict_different_key():
    v2 = VerifierSnapshot(verdict="FAIL", oracle_type="REGRESSION",
                          confidence_tier="R", obligation_coverage=1.0)
    assert primary_effect_key(**_args(verifier=v2)) != primary_effect_key(**_args())


def test_created_regression_does_not_change_primary_key():
    """Design doc 6.2: created regression is risk-stratified, NOT a primary key."""
    delta2 = ObservationDelta(
        original_failure="REMOVED",
        first_divergence={"before": 12, "after": None},
        failing_tests={"before": 1, "after": 0},
        created_regressions=["lvs_clean"], newly_observed_failures=["new_timing"])
    assert primary_effect_key(**_args(delta=delta2)) == primary_effect_key(**_args())


def test_config_values_not_in_key_same_family_groups_together():
    """Two antenna repairs that differ only in knob VALUES share an effect key."""
    a = Action(domain="signoff.REPAIR_ACTION", transformation_family="ANTENNA_DIODE_REPAIR",
               payload={"config_edits": {"PLACE_DENSITY_LB_ADDON": "0.14"}})
    b = Action(domain="signoff.REPAIR_ACTION", transformation_family="ANTENNA_DIODE_REPAIR",
               payload={"config_edits": {"PLACE_DENSITY_LB_ADDON": "0.20"}})
    assert primary_effect_key(**_args(action=a)) == primary_effect_key(**_args(action=b))


# -- normalized deltas --------------------------------------------------------

def test_normalize_divergence_categories():
    assert normalize_divergence({"before": 12, "after": None}) == "FIXED"
    assert normalize_divergence({"before": 12, "after": 5}) == "SHIFTED"
    assert normalize_divergence({"before": 12, "after": 12}) == "UNCHANGED"
    assert normalize_divergence({"before": None, "after": 3}) == "NEW"
    assert normalize_divergence(None) is None


def test_normalize_tests_categories():
    assert normalize_tests({"before": 1, "after": 0}) == "REDUCED"
    assert normalize_tests({"before": 0, "after": 2}) == "INCREASED"
    assert normalize_tests({"before": 1, "after": 1}) == "UNCHANGED"
    assert normalize_tests({"before": None, "after": 0}) == "UNKNOWN"
    assert normalize_tests(None) is None


def test_instance_values_normalized_to_same_key():
    """Different divergence VALUES that map to the same category share a key."""
    d1 = ObservationDelta(original_failure="REMOVED",
                          first_divergence={"before": 18, "after": None},
                          failing_tests={"before": 3, "after": 0})
    d2 = ObservationDelta(original_failure="REMOVED",
                          first_divergence={"before": 7, "after": None},
                          failing_tests={"before": 2, "after": 0})
    assert primary_effect_key(**_args(delta=d1)) == primary_effect_key(**_args(delta=d2))


# -- dict-level API + capture/preflight consistency ----------------------------

def test_effect_key_from_transition_dict():
    a = primary_effect_key(**_args())
    tdict = {
        "action": _args()["action"].to_dict(),
        "observation_delta": _args()["delta"].to_dict(),
        "verifier": _args()["verifier"].to_dict(),
    }
    assert effect_key_from_transition_dict(tdict) == a


def test_capture_stored_key_equals_preflight_recomputed_key(tmp_tehm, sample_record_dict):
    """The stored transition.primary_effect_key and the preflight grouping key
    share ONE canon (no drift between capture time and preflight time)."""
    conn, store, _ = tmp_tehm
    receipt = capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    row = conn.execute(
        "SELECT action_json, observation_delta_json, verifier_json "
        "FROM tehm_transitions WHERE transition_id=?",
        (receipt.transition_id,)).fetchone()
    from tehm.db import read_json
    from tehm.crystallization.effects import effect_key_from_transition_dict
    recomputed = effect_key_from_transition_dict({
        "action": read_json(row["action_json"]),
        "observation_delta": read_json(row["observation_delta_json"]),
        "verifier": read_json(row["verifier_json"]),
    })
    assert recomputed == receipt.primary_effect_key
