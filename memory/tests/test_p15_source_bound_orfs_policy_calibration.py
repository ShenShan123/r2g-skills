"""P15 utility-oracle/firewall conformance, not empirical model results."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import scripts.build_p15_source_bound_orfs_policy_calibration as module
from tehm.evaluation.orfs_paired_utility import apply_orfs_paired_utility_contract
from tehm.physical.utility_contracts import p12_density_relief_interference_nonregression_v1
from test_orfs_paired_utility import _bundle


def _pair():
    pair, arms = _bundle()
    return apply_orfs_paired_utility_contract(pair, arms,
        contract=p12_density_relief_interference_nonregression_v1())


def test_real_typed_utility_harm_labels_risk_without_rewriting_pass():
    pair = _pair()
    original = copy.deepcopy(pair.to_dict())
    label = module._oracle_label(pair)
    assert label["expected_decision"] == "NO_SKILL"
    assert label["expected_reason"] == "RISK"
    assert label["baseline_outcome"] == label["forced_outcome"] == "PASS"
    assert label["utility_receipt"]["observation"]["status"] == "FAIL"
    assert label["router_prediction_used"] is False
    assert label["execution_outcomes_rewritten"] is False
    assert pair.to_dict() == original


def test_router_prediction_is_not_used_by_the_oracle():
    pair = _pair()
    first = module._oracle_label(pair)
    changed = replace(pair, routing_decision="APPLY")
    second = module._oracle_label(changed)
    for key in ("expected_decision", "expected_reason", "utility_receipt", "forced_outcome"):
        assert first[key] == second[key]


@pytest.mark.parametrize("arm", ["NO_MEMORY", "ALWAYS_MEMORY"])
def test_unknown_is_not_a_calibration_oracle(arm):
    pair = _pair()
    receipts = dict(pair.arm_receipts)
    receipts[arm] = replace(receipts[arm], outcome="UNKNOWN")
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="unknown"):
        module._oracle_label(replace(pair, arm_receipts=receipts))


def test_missing_or_tampered_embedded_utility_cannot_label_harm():
    pair = _pair()
    receipts = dict(pair.arm_receipts)
    metadata = copy.deepcopy(receipts["ALWAYS_MEMORY"].metadata)
    metadata.pop("paired_utility")
    receipts["ALWAYS_MEMORY"] = replace(receipts["ALWAYS_MEMORY"], metadata=metadata)
    with pytest.raises(ValueError, match="utility receipt"):
        module._oracle_label(replace(pair, arm_receipts=receipts))


@pytest.mark.parametrize("status,improved,harm,expected", [
    ("PASS", ["wns_ns"], [], "USE_MEMORY"),
    ("PASS", [], [], None),
    ("PASS", ["wns_ns"], ["area_um2"], None),
    ("ABSTAINED", [], [], None),
    ("FAIL", ["wns_ns"], ["area_um2"], "NO_SKILL"),
])
def test_oracle_neutral_is_not_automatically_safe_help(monkeypatch, status, improved, harm, expected):
    monkeypatch.setattr(module, "replay_orfs_paired_utility_receipt", lambda *args: {
        "observation": {"status": status,
            "raw_pareto": {"improved_metrics": improved, "harmful_metrics": harm}}})
    label = module._oracle_label(_pair())
    assert label["expected_decision"] == expected
    assert label["expected_reason"] == ("RISK" if expected == "NO_SKILL" else None)


@pytest.mark.parametrize("baseline,memory", [("FAIL", "FAIL"), ("FAIL", "PASS")])
def test_physical_failure_does_not_fabricate_no_match_or_state_shift(baseline, memory):
    pair = _pair()
    receipts = dict(pair.arm_receipts)
    receipts["NO_MEMORY"] = replace(receipts["NO_MEMORY"], outcome=baseline)
    receipts["ALWAYS_MEMORY"] = replace(receipts["ALWAYS_MEMORY"], outcome=memory)
    label = module._oracle_label(replace(pair, arm_receipts=receipts))
    assert label["expected_decision"] is None
    assert label["expected_reason"] is None


def test_baseline_success_and_memory_failure_are_independent_risk():
    pair = _pair()
    receipts = dict(pair.arm_receipts)
    receipts["ALWAYS_MEMORY"] = replace(receipts["ALWAYS_MEMORY"], outcome="FAIL")
    label = module._oracle_label(replace(pair, arm_receipts=receipts))
    assert label["expected_reason"] == "RISK"
    assert label["utility_receipt"] is None


@pytest.mark.parametrize("decision", ["INAPPLICABLE", "ABSTAIN"])
def test_outside_binary_contract_is_not_no_skill(decision):
    route = SimpleNamespace(decision=decision)
    assert module._prediction(route) == (None, None)
    assert module._binary_sample("case", {"expected_decision": "NO_SKILL"}, route, {}) is None


def test_confidence_is_absent_and_actual_config_design_is_used():
    route = SimpleNamespace(decision="CONSIDER", routing_receipt_id="routing-real")
    sample = module._binary_sample("case", {"expected_decision": "NO_SKILL", "expected_reason": "RISK"},
        route, {"platform": "sky130hs", "flow_config_observation": {"values": {"DESIGN_NAME": "actual-design"}}})
    assert sample.confidence is None
    assert sample.strata["design"] == "actual-design"
    assert sample.routing_receipt_id == "routing-real"
    receipt = module.evaluate_no_skill_calibration([sample], minimum_sample_count=1)
    assert receipt.eligible is False


@pytest.mark.parametrize("role", ["training", "evolution", "held_out", "validation"])
def test_wrong_partition_is_rejected(role):
    case = {"case_id": "case", "dataset_split": role, "role": role,
        "learner_eligible": False, "lineage_id": "lineage"}
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="partition"):
        module._partition([case], "calibration")


def test_calibration_cannot_be_learner_eligible():
    case = {"case_id": "case", "dataset_split": "calibration", "role": "calibration",
        "learner_eligible": True, "lineage_id": "lineage"}
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="partition"):
        module._partition([case], "calibration")


def test_oracle_cannot_freeze_after_even_a_partial_arm(tmp_path):
    cases = [{"execution_artifacts_root": str(tmp_path / "execution")}]
    module._before_outcomes(cases)
    (tmp_path / "execution").mkdir()
    module._before_outcomes(cases)
    (tmp_path / "execution" / "partial-arm").mkdir()
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="before any"):
        module._before_outcomes(cases)


def _authority(prefix, hashes):
    return {"cases": {str(i): {"lineage_id": prefix + str(i), "rtl_sha256": [value]}
        for i, value in enumerate(hashes)}}


@pytest.mark.parametrize("role", ["training", "held_out"])
def test_renaming_the_same_rtl_does_not_establish_independence(role):
    calibration = _authority("calibration", ["sha256:A", "sha256:B"])
    training = _authority("training", ["C", "D"])
    heldout = _authority("heldout", ["E", "F"])
    if role == "training":
        training = _authority("renamed-training", ["A", "D"])
    else:
        heldout = _authority("renamed-heldout", ["sha256:A", "F"])
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="overlaps"):
        module._disjoint(calibration, training, heldout)


def test_disjoint_source_does_not_claim_iid():
    witness = module._disjoint(_authority("calib", ["A", "B"]),
        _authority("training", ["C", "D"]), _authority("heldout", ["E", "F"]))
    assert witness["disjoint"] is True
    assert witness["iid_or_population_representativeness_claimed"] is False


def test_existing_outputs_are_preserved(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="new and separate"):
        module.build_policy_calibration("missing", "missing", output_dir=existing)
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="must be new"):
        module.prepare_oracle_binding("missing", "missing", "missing", output=existing)
    assert list(existing.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("frozen_before_calibration_execution", False),
    ("oracle_spec", {}), ("oracle_source_binding", {}),
])
def test_oracle_drift_is_rejected_before_any_audit_output(tmp_path, field, value):
    binding = {"version": "p15-source-bound-orfs-calibration-oracle-binding-v1",
        "frozen_before_calibration_execution": True, "oracle_spec": module.ORACLE_SPEC,
        "oracle_source_binding": module._pin(module.Path(module.__file__).resolve()), field: value}
    binding["report_digest"] = module._digest(binding)
    source = tmp_path / "oracle.json"
    source.write_text(json.dumps(binding))
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="definition or source drift"):
        module.build_policy_calibration(source, tmp_path / "reports", output_dir=tmp_path / "output")
    assert not (tmp_path / "output").exists()


def _comparison_fixture():
    manifest = {key: key for key in ("toolchain_digest", "oracle_digest", "platform_digest", "pdk_digest",
        "candidate_budget", "utility_contract_digest", "utility_contract_id")}
    case = {key: key for key in ("source_digest", "source_inputs", "flow_config_observation", "environment",
        "lineage_id", "dataset_split", "role", "learner_eligible", "target_check")}
    case["case_id"] = "case"
    return {view: {"manifest": copy.deepcopy(manifest), "cases": [copy.deepcopy(case)]} for view in module.VIEWS}


@pytest.mark.parametrize("key", ["candidate_budget", "oracle_digest", "utility_contract_digest"])
def test_policy_view_cannot_change_comparison_contract(key):
    inputs = _comparison_fixture()
    inputs["Mt_plus_delta"]["manifest"][key] = "changed"
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="comparison"):
        module._comparison_inputs(inputs)


@pytest.mark.parametrize("key", ["source_inputs", "flow_config_observation", "environment", "target_check"])
def test_policy_view_cannot_change_source_constraints_or_objective(key):
    inputs = _comparison_fixture()
    inputs["Mt_plus_delta"]["cases"][0][key] = "changed"
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="source or constraint"):
        module._comparison_inputs(inputs)


def test_matching_policy_comparison_is_accepted():
    module._comparison_inputs(_comparison_fixture())


def test_arbitrary_heldout_tag_is_not_the_actual_p14_cohort(monkeypatch, tmp_path):
    af = {"eligible": True, "actual_gate_oracles_cold_replayed": True, "learner_support_imported": False,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "promotion_attempted": False, "gate_evidence": {"heldout": {}}, "source_bound_plan": {}}
    audit = {"purpose": "heldout", "passed": True, "policy_freeze": {}, "source_bound_plan": {}}
    monkeypatch.setattr(module, "_reference", lambda ref, name: (None, af if "anti-forgetting" in name else audit))
    monkeypatch.setattr(module, "_self_digest", lambda *args: None)
    monkeypatch.setattr(module, "_pin", lambda path: {"path": str(path), "sha256": "actual"})
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="actual P14"):
        module._heldout_reference({"anti_forgetting_evidence": {}}, tmp_path / "different.json", {"report_digest": "different"})


def _execution_freeze_fixture(tmp_path):
    def write(name, raw):
        raw["report_digest"] = module._digest(raw)
        path = tmp_path / name
        path.write_text(json.dumps(raw))
        return path, {**module._pin(path), "report_digest": raw["report_digest"]}
    manifests = {view: {"path": view, "sha256": view} for view in module.VIEWS}
    _, policy = write("policy.json", {"policy_manifests": manifests})
    binding_path, oracle = write("oracle.json", {
        "version": "p15-source-bound-orfs-calibration-oracle-binding-v1",
        "oracle_spec": module.ORACLE_SPEC,
        "oracle_source_binding": module._pin(module.Path(module.__file__).resolve()),
        "policy_freeze": policy, "frozen_before_calibration_execution": True,
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "promotion_attempted": False, "evaluation_only": True})
    source = tmp_path / "source.py"
    source.write_text("# source-pinned fixture\n")
    raw = {"version": "p15-source-bound-orfs-three-policy-execution-freeze-v1",
        "purpose": "CALIBRATION", "learner_eligible": False, "production_runtime_imported": False,
        "promotion_attempted": False, "canonical_memory_mutation": "none",
        "execution_order": list(module.VIEWS), "seed_binding": {"OR_SEED": "UNSET_PINNED_OPENROAD_DEFAULT"},
        "oracle_binding": oracle, "policy_freeze": policy,
        "views": {view: {"manifest": ref} for view, ref in manifests.items()},
        **{key: module._pin(source) for key in ("execution_driver_binding", "gate_audit_binding",
            "layout_audit_binding", "source_layout_audit")}}
    freeze_path, _ = write("freeze.json", raw)
    return freeze_path, binding_path, raw, source


def test_execution_freeze_accepts_matching_pinned_sources(tmp_path):
    freeze, binding, _, _ = _execution_freeze_fixture(tmp_path)
    assert module.validate_execution_freeze(freeze, binding)["purpose"] == "CALIBRATION"


@pytest.mark.parametrize("field,value", [
    ("purpose", "EVOLUTION_CHALLENGE"), ("learner_eligible", True),
    ("production_runtime_imported", True), ("promotion_attempted", True),
    ("canonical_memory_mutation", "write"), ("policy_freeze", {}),
    ("execution_order", []), ("seed_binding", {"OR_SEED": "1"}),
])
def test_execution_freeze_rejects_protocol_drift(tmp_path, field, value):
    freeze, binding, raw, _ = _execution_freeze_fixture(tmp_path)
    raw[field] = value
    raw.pop("report_digest")
    raw["report_digest"] = module._digest(raw)
    freeze.write_text(json.dumps(raw))
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="protocol or oracle drift"):
        module.validate_execution_freeze(freeze, binding)


def test_execution_freeze_rejects_driver_or_auditor_source_drift(tmp_path):
    freeze, binding, _, source = _execution_freeze_fixture(tmp_path)
    source.write_text("# changed after freeze\n")
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="source drift"):
        module.validate_execution_freeze(freeze, binding)


def test_execution_freeze_cannot_alias_another_oracle_binding(tmp_path):
    freeze, _, _, _ = _execution_freeze_fixture(tmp_path)
    with pytest.raises(module.SourceBoundPolicyCalibrationError, match="protocol or oracle drift"):
        module.validate_execution_freeze(freeze, tmp_path / "another-oracle.json")
