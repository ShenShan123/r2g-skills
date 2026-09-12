"""Consumption guard fixtures, not a real P13 receipt or promotion proof."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.run_p13_interference_source_bound_shadow_update import (
    SourceBoundInterferenceShadowError, _execution_plan, _training_refs,
    run_source_bound_interference_shadow_update,
)
from scripts.build_p13_interference_source_bound_plan import _digest
from tehm.evolution import AntiForgettingWitness, LocalizedUpdatePlan


def _witness(passed=True):
    sha = "sha256:" + "a" * 64
    return AntiForgettingWitness("target", sha, True, "non-target", sha, passed,
                                 "heldout", sha, True, "rollback-pointer", sha, True)


def _plan():
    base = LocalizedUpdatePlan("tid:a", "fixture", True, "P1_HIGH", 1,
        "UPDATE_CAUSAL_KNOWLEDGE", ("UPDATE_CAUSAL_KNOWLEDGE",), "SPECIALIZE",
        "MEMORY_INTERFERENCE", knowledge_refs=("mk_fixture@1",),
        evidence_refs=("tid:a", "tid:b"), rationale="fixture negative scope")
    proposal = SimpleNamespace(knowledge_object_id="mk_fixture@1", campaign_id="fixture")
    report = {"source_bound_localized_update_plan": {**base.to_dict(), "plan_digest": base.plan_digest},
              "parent_transition_ids": ["tid:a", "tid:b"]}
    return base, proposal, report


def test_execution_plan_binds_witness_without_modifying_source_plan():
    base, proposal, report = _plan()
    witness = _witness()
    result = _execution_plan(report, proposal, witness, ("sha256:actual-evidence",))
    assert witness.receipt_digest in result.evidence_refs
    assert "sha256:actual-evidence" in result.evidence_refs
    assert base.plan_digest != result.plan_digest
    assert base.evidence_refs == ("tid:a", "tid:b")


def test_failed_non_target_gate_cannot_grant_structural_mutation():
    _, proposal, report = _plan()
    with pytest.raises(SourceBoundInterferenceShadowError, match="eligible source-bound"):
        _execution_plan(report, proposal, _witness(False), ())


def test_wrong_operation_cannot_be_smuggled_as_interference_specialization():
    base, proposal, report = _plan()
    changed = replace(base, operation="REVISE")
    report["source_bound_localized_update_plan"] = {**changed.to_dict(), "plan_digest": changed.plan_digest}
    with pytest.raises(SourceBoundInterferenceShadowError, match="eligible source-bound"):
        _execution_plan(report, proposal, _witness(), ())


def test_original_parent_training_transitions_must_be_in_plan_witness():
    _, proposal, report = _plan()
    report["parent_transition_ids"].append("tid:unwitnessed")
    with pytest.raises(SourceBoundInterferenceShadowError, match="eligible source-bound"):
        _execution_plan(report, proposal, _witness(), ())


def _partition():
    proposal = SimpleNamespace(case_ids=("a", "b"), paired_receipt_digests=("sha256:A", "sha256:B"))
    authority = {"cases": {"a": {"lineage_id": "L1"}, "b": {"lineage_id": "L2"}},
                 "learner_partition": {"learner_eligible": True, "cases": {
                     cid: {"dataset_split": "training", "role": "training", "learner_eligible": True}
                     for cid in ("a", "b")}}}
    return proposal, authority


def test_paired_evidence_stays_zipped_with_real_case_lineage():
    proposal, authority = _partition()
    refs = _training_refs(proposal, authority, "L3")
    assert [(r["evidence_id"], r["lineage_id"]) for r in refs] == [("sha256:A", "L1"), ("sha256:B", "L2")]
    assert len(refs) == 2


@pytest.mark.parametrize("role", ["held_out", "validation", "calibration"])
def test_audit_case_can_never_be_imported_as_training_revision_evidence(role):
    proposal, authority = _partition()
    authority["learner_partition"]["cases"]["b"] = {
        "dataset_split": role, "role": role, "learner_eligible": False}
    with pytest.raises(SourceBoundInterferenceShadowError, match="non-training"):
        _training_refs(proposal, authority, "L3")


def test_existing_shadow_output_is_never_overwritten(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserved")
    with pytest.raises(SourceBoundInterferenceShadowError, match="new and separate"):
        run_source_bound_interference_shadow_update("missing", output=output)
    assert output.read_text() == "preserved"


def test_ineligible_report_is_rejected_before_reading_database(tmp_path):
    report = {"version": "p13-interference-actual-anti-forgetting-evidence-v1", "eligible": False}
    report["report_digest"] = _digest(report)
    path = tmp_path / "anti.json"
    path.write_text(json.dumps(report))
    with pytest.raises(SourceBoundInterferenceShadowError, match="not eligible"):
        run_source_bound_interference_shadow_update(path, output=tmp_path / "new.json")
    assert not (tmp_path / "new.json").exists()
