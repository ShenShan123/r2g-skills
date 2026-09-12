"""Plan compiler guards; these deterministic tests are not ORFS gain evidence."""
import json

import pytest

from contracts import MemoryQuery
from scripts.build_p13_interference_source_bound_plan import (
    InterferenceSourceBoundPlanError, _is_intervention, _negative_contexts, _utility_binding,
    build_source_bound_plan,
)
from scripts.build_p13_interference_reason_bundle import build_interference_reason_bundle
from test_p13_interference_reason_bundle import _fixture


def _query(name="a", utilization="50"):
    return MemoryQuery(query_plan={
        "mechanism_family": "DENSITY_RELIEF", "target_scope": "flow_feasibility",
        "measurement_contract_digest": "sha256:measurement", "flow_design_id": name,
        "flow_config": {"CORE_UTILIZATION": utilization},
        "interference_signature": "caller-label-is-not-a-fact-to-learn"})


def _contexts(queries):
    return _negative_contexts(queries, utility_contract_id="zero-regression",
                              utility_contract_digest="sha256:utility")


def test_exclusion_compiler_uses_only_existing_measured_query_facts():
    contexts = _contexts({"a": _query(), "b": _query("b")})
    assert len(contexts) == 1
    assert contexts[0]["flow_config"] == {"CORE_UTILIZATION": "50"}
    assert "interference_signature" not in contexts[0]
    assert "flow_design_id" not in contexts[0]
    assert contexts[0]["utility_contract_id"] == "zero-regression"


def test_distinct_effective_configurations_are_not_collapsed():
    contexts = _contexts({"a": _query(), "b": _query("b", "60")})
    assert len(contexts) == 2


def test_same_family_baseline_control_is_not_an_intervention():
    treatment = {"domain": "flow.CONFIG_DELTA", "transformation_family": "DENSITY_RELIEF",
                 "payload": {"config_edits": {"CORE_UTILIZATION": "40"},
                             "recheck": "flow_feasibility", "measurement_contract_digest": "sha256:m"}}
    control = {**treatment, "domain": "flow.BASELINE_CONTROL",
               "payload": {**treatment["payload"], "config_edits": {}, "control": True}}
    assert _is_intervention(treatment, treatment)
    assert not _is_intervention(control, treatment)
    wrong = {**treatment, "payload": {**treatment["payload"], "config_edits": {"CORE_UTILIZATION": "99"}}}
    assert not _is_intervention(wrong, treatment)


def test_missing_measurement_scope_never_becomes_negative_applicability():
    query = _query()
    query.query_plan["target_scope"] = "global"
    with pytest.raises(InterferenceSourceBoundPlanError, match="measured exclusion"):
        _contexts({"a": query})


def test_utility_harm_cannot_become_an_unscoped_feasibility_veto():
    with pytest.raises(InterferenceSourceBoundPlanError, match="utility contract binding"):
        _negative_contexts({"a": _query()}, utility_contract_id="", utility_contract_digest="")


def test_proposed_utility_scope_must_match_actual_physical_receipt(tmp_path):
    _, _, receipt = _fixture(tmp_path)
    utility = next(iter(receipt.case_receipts.values())).arm_receipts["ALWAYS_MEMORY"].metadata["paired_utility"]
    context = {"utility_contract_id": utility["contract_id"],
               "utility_contract_digest": "sha256:" + utility["contract_digest"]}
    _utility_binding(receipt, context, context)
    wrong = {**context, "utility_contract_id": "some-other-objective"}
    with pytest.raises(InterferenceSourceBoundPlanError, match="physical utility context"):
        _utility_binding(receipt, wrong, wrong)


def test_diagnostic_reason_without_training_admission_cannot_produce_plan(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path)
    bundle_path = tmp_path / "bundle.json"
    build_interference_reason_bundle(cohort, manifest, output=bundle_path)
    with pytest.raises(InterferenceSourceBoundPlanError, match="admitted prospective training"):
        build_source_bound_plan(bundle_path, output=tmp_path / "plan.json")
    assert not (tmp_path / "plan.json").exists()


def test_single_lineage_training_cannot_skip_p12_trigger_gate(tmp_path):
    cohort, manifest, _ = _fixture(tmp_path, training=True)
    bundle_path = tmp_path / "bundle.json"
    build_interference_reason_bundle(cohort, manifest, output=bundle_path)
    with pytest.raises(InterferenceSourceBoundPlanError, match="P12 trigger rejected"):
        build_source_bound_plan(bundle_path, output=tmp_path / "plan.json")


def test_existing_plan_or_evidence_never_overwritten(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"keep": True}))
    with pytest.raises(InterferenceSourceBoundPlanError, match="output must be new"):
        build_source_bound_plan(path, output=path)
    assert json.loads(path.read_text()) == {"keep": True}
