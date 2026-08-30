import pytest

from tehm.parametric.calibration import (
    calibrate_exact_groups,
    calibrate_lineage_grouped,
    materialize_shadow_policy,
)
from tehm.physical.utility_contracts import (
    contract_action,
    density_relief_nonregression_32,
)


def _sample(lineage, wns=-0.02, area=-0.10):
    metrics = {"wns_ns": 0.0, "tns_ns": 0.0, "area_um2": 0.0,
               "power_w": 0.0, "congestion": 0.0, "drc_violations": 0.0}
    observed = dict(metrics)
    observed.update({"wns_ns": wns, "area_um2": area})
    return {"lineage_id": lineage, "predicted": metrics,
            "observed_deltas": observed}


def test_lineage_grouped_conformal_and_safety_are_shadow_only():
    report = calibrate_lineage_grouped(
        [_sample(f"heldout:{idx}") for idx in range(3)],
        training_lineages=["train:a"], min_samples_per_metric=3)
    assert report["status"] == "ready_for_shadow"
    assert report["shadow_only"] is True
    assert report["promotion_eligible"] is False
    assert report["conformal"]["method"].startswith("split_conformal")
    assert report["safety"]["harmful_rate"] == 0.0


def test_lineage_firewall_fails_closed():
    report = calibrate_lineage_grouped(
        [_sample("heldout:a"), _sample("heldout:b"), _sample("heldout:c")],
        training_lineages=["heldout:a"])
    assert report["status"] == "firewall_failed"
    assert report["canonical_memory_mutation"] == "none"


def test_all_neutral_rows_do_not_establish_shadow_utility():
    samples = [_sample(f"heldout:{idx}", wns=0.0, area=0.0)
               for idx in range(3)]
    report = calibrate_lineage_grouped(samples, min_samples_per_metric=3)
    assert report["status"] == "shadow_calibration_failed"
    assert report["checks"]["positive_utility"] is False
    assert report["safety"]["positive_utility_rate"] == 0.0
    assert report["safety"]["positive_utility_lineages"] == []


def _exact_sample(lineage, *, wns=0.01, area=-0.1, util="40"):
    row = _sample(lineage, wns=wns, area=area)
    row.update({
        "platform": "sky130hs",
        "family": "DENSITY_RELIEF",
        "dataset_tier": "research",
        "action_signature": {
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "config_edit_keys": ["CORE_UTILIZATION"],
            "config_edit_values": {"CORE_UTILIZATION": util},
            "typed_action": None,
        },
    })
    return row


def test_grouped_report_materializes_only_a_shadow_read_policy():
    signature = _exact_sample("heldout:0")["action_signature"]
    report = calibrate_exact_groups(
        [_exact_sample(f"heldout:{idx}") for idx in range(3)],
        training_lineages=["train:a"], min_samples_per_metric=3)
    policy = materialize_shadow_policy(
        report,
        scope={"platform": "sky130hs", "family": "DENSITY_RELIEF",
               "dataset_tier": "research"},
        action_signature=signature, max_distance=0.5)
    assert policy["status"] == "ready"
    assert policy["policy_kind"] == "lineage_grouped_shadow"
    assert policy["source_calibration_status"] == "ready_for_shadow"
    assert policy["shadow_only"] is True
    assert policy["promotion_eligible"] is False
    assert policy["canonical_memory_mutation"] == "none"
    assert policy["thresholds"]["conformal_quantiles"]["area_um2"] == 0.1
    assert policy["calibration"]["positive_utility_rate"] == 1.0


def test_shadow_policy_materializer_rejects_neutral_or_oversized_policy():
    neutral = calibrate_exact_groups(
        [_exact_sample(f"heldout:{idx}", wns=0.0, area=0.0)
         for idx in range(3)], min_samples_per_metric=3)
    signature = _exact_sample("heldout:0")["action_signature"]
    kwargs = {
        "scope": {"platform": "sky130hs", "family": "DENSITY_RELIEF",
                  "dataset_tier": "research"},
        "action_signature": signature,
    }
    with pytest.raises(ValueError, match="not ready_for_shadow"):
        materialize_shadow_policy(neutral, **kwargs, max_distance=0.5)
    positive = calibrate_exact_groups(
        [_exact_sample(f"heldout:{idx}") for idx in range(3)],
        min_samples_per_metric=3)
    with pytest.raises(ValueError, match="<= 3.0"):
        materialize_shadow_policy(positive, **kwargs, max_distance=3.1)


def test_shadow_policy_can_bind_a_pre_registered_utility_contract():
    contract = density_relief_nonregression_32()
    signature = _exact_sample("heldout:0", util="32")["action_signature"]
    report = calibrate_exact_groups(
        [_exact_sample(f"heldout:{idx}", util="32") for idx in range(3)],
        min_samples_per_metric=3)
    policy = materialize_shadow_policy(
        report,
        scope={"platform": "sky130hs", "family": "DENSITY_RELIEF",
               "dataset_tier": "research"},
        action_signature=signature, max_distance=0.5,
        utility_contract=contract)
    assert policy["utility_contract_id"] == contract["contract_id"]
    assert policy["utility_contract_digest"]

    from tehm.physical.utility_contracts import timing_relief_budgeted_v1
    with pytest.raises(ValueError, match="does not match"):
        materialize_shadow_policy(
            report,
            scope={"platform": "sky130hs", "family": "DENSITY_RELIEF",
                   "dataset_tier": "research"},
            action_signature=signature, max_distance=0.5,
            utility_contract=timing_relief_budgeted_v1())


def test_parametric_calibration_does_not_accept_boolean_numeric_evidence():
    samples = [_sample(f"heldout:{idx}") for idx in range(3)]
    samples[0]["observed_deltas"]["wns_ns"] = True
    with pytest.raises(ValueError, match="max_regression"):
        calibrate_lineage_grouped(
            samples, max_regression={"wns_ns": True})
    report = calibrate_lineage_grouped(samples, min_samples_per_metric=3)
    assert report["status"] == "shadow_calibration_failed"
    assert report["conformal"]["per_metric"]["wns_ns"]["evaluated"] == 2


def test_conformal_boundary_is_inclusive_across_binary_float_rounding():
    samples = [_sample(f"heldout:{idx}") for idx in range(4)]
    powers = [(3.1e-5, -3.9e-5), (5e-6, 1e-6),
              (4e-5, -1e-6), (-2.7e-5, 0.0)]
    for sample, (predicted, observed) in zip(samples, powers):
        sample["predicted"]["power_w"] = predicted
        sample["observed_deltas"]["power_w"] = observed
    report = calibrate_lineage_grouped(
        samples, target_coverage=0.8, min_samples_per_metric=3)
    power = report["conformal"]["per_metric"]["power_w"]
    assert power["conformal_radius"] == 0.00007
    assert power["covered"] == 4
    assert power["coverage"] == 1.0
