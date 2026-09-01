"""P13 localized updates stay isolated from canonical and runtime authority."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from contracts import MemoryRoutingDecision
from tehm.canonical.capture import capture
from tehm.evolution import (
    AntiForgettingWitness,
    AppliedShadowUpdateReceipt,
    P12ShadowUpdateTriggerReceipt,
    LocalizedUpdatePlan,
    ShadowUpdateError,
    apply_localized_update_shadow,
    build_p12_shadow_update_triggers,
)
from tehm.evaluation.candidate_executor import (
    CandidateExecutionReceipt, PairedCandidateExecutionReceipt,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.knowledge import MechanismKnowledge, register_knowledge


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def _anti_forgetting(tag: str = "shadow") -> AntiForgettingWitness:
    return AntiForgettingWitness(
        target_replay_receipt_id=f"{tag}:target",
        target_replay_digest=f"sha256:{tag}-target",
        target_replay_passed=True,
        non_target_regression_receipt_id=f"{tag}:non-target",
        non_target_regression_digest=f"sha256:{tag}-non-target",
        non_target_regression_free=True,
        heldout_audit_receipt_id=f"{tag}:heldout",
        heldout_audit_digest=f"sha256:{tag}-heldout",
        heldout_audit_passed=True,
        rollback_pointer=f"{tag}:rollback",
        rollback_receipt_digest=f"sha256:{tag}-rollback",
        rollback_verified=True,
        evidence_refs=(f"{tag}:target", f"{tag}:non-target",
                       f"{tag}:heldout", f"{tag}:rollback"),
    )


def _anti_evidence(witness: AntiForgettingWitness) -> dict:
    return {"anti_forgetting": {
        **witness.to_dict(), "receipt_digest": witness.receipt_digest}}


def _capture(tmp_tehm, name: str, *, oracle=None) -> str:
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECTS / name, oracle=oracle, store=store)
    return capture(
        conn, store, record, dataset_campaign_id="live",
        dataset_split="training", dataset_learner_eligible=True).transition_id


def _plan(transition_id: str, target: str, operation: str, *, refs=(),
          state_resolution_id=None, learner_eligible=True, knowledge_refs=()) -> LocalizedUpdatePlan:
    return LocalizedUpdatePlan(
        transition_id=transition_id, campaign_id="live",
        learner_eligible=learner_eligible, priority="P1_HIGH", value_score=0.8,
        update_target=target, candidate_targets=(target,), operation=operation,
        failure_type="STATE_RESOLUTION_FAILURE", evidence_refs=tuple(refs),
        state_resolution_id=state_resolution_id, knowledge_refs=tuple(knowledge_refs),
        rationale="P13 shadow test",
    )


def _knowledge_claim(knowledge_id: str, *, version: int = 1,
                     intervention: dict | None = None) -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id=knowledge_id, version=version,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention=intervention or {"family": "GUARD_RESTORE"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },),
        negative_applicability=(), preserved_obligations=("target_trace_pass",),
        known_failure_modes=("ambiguous_target_binding",),
        causal_path_ids=("causal_path_p13",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("lineage-p13",), status="shadow")


def _register_shadow_knowledge(conn, claim: MechanismKnowledge) -> None:
    register_knowledge(conn, claim, evidence_refs=[{
        "evidence_type": "manual_review", "evidence_id": "seed-" + claim.knowledge_id,
        "split": "training", "lineage_id": "lineage-p13",
        "evidence_level": claim.evidence_level,
    }])


def test_relation_update_is_applied_then_discarded(tmp_tehm):
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug")
    second = _capture(tmp_tehm, "req_ack_bug2")
    witness = _anti_forgetting("relation")
    before = conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0]
    plan = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE",
                 refs=(first, witness.receipt_digest))
    receipt = apply_localized_update_shadow(plan, conn, {
        **_anti_evidence(witness),
        "relation": {
            "source_type": "transition", "source_id": first,
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": second, "evidence_refs": [first],
        }
    })
    assert receipt.created_relation_ids
    assert receipt.before_resolution_id != receipt.after_resolution_id
    assert receipt.staging_discarded is True
    assert receipt.canonical_memory_mutation == "none"
    assert receipt.lifecycle_mutation == "isolated_staging_only"
    assert receipt.canonical_rows_changed is False
    assert receipt.production_authority_changed is False
    assert receipt.source_digest_before == receipt.source_digest_after
    assert receipt.raw_evidence_before_digest == receipt.raw_evidence_after_digest
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0] == before


def test_retain_shadow_update_is_a_deterministic_noop(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    plan = _plan(transition_id, "UPDATE_NONE", "RETAIN", learner_eligible=False)
    first = apply_localized_update_shadow(plan, conn)
    second = apply_localized_update_shadow(plan, conn)
    assert first.to_dict() == second.to_dict()
    assert first.created_object_ids == ()
    assert first.created_relation_ids == ()
    assert first.before_resolution_id == first.after_resolution_id


def test_causal_update_crystallizes_only_in_shadow(tmp_tehm):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug", oracle=oracle)
    second = _capture(tmp_tehm, "req_ack_bug2", oracle=oracle)
    witness = _anti_forgetting("causal")
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_rules", "tehm_rule_revisions", "tehm_memory_events")
    }
    plan = _plan(
        first, "UPDATE_CAUSAL_KNOWLEDGE", "ADD",
        refs=(first, second, witness.receipt_digest))
    receipt = apply_localized_update_shadow(
        plan, conn, {"transition_ids": [first, second], **_anti_evidence(witness)})
    assert any(item.startswith("rule:") for item in receipt.created_object_ids)
    assert any(item.startswith("rule_revision:")
               for item in receipt.created_object_ids)
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    } == before


def test_shadow_asset_update_never_promotes_or_persists(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    witness = _anti_forgetting("asset")
    plan = _plan(transition_id, "UPDATE_ASSET", "ADD",
                 refs=(transition_id, witness.receipt_digest))
    receipt = apply_localized_update_shadow(plan, conn, {
        **_anti_evidence(witness),
        "asset": {
            "asset_type": "DIAGNOSTIC_EXTRACTOR", "name": "shadow.asset",
            "version": "0.1", "definition": {"kind": "diagnostic"},
            "input_contract": {"state": "object"},
            "output_contract": {"diagnostic": "object"},
            "verifier_contract": {"independent": True},
            "compatibility": {"mechanism_family": "RTL_REPAIR"},
            "provenance": {"source": "P13-test"},
        }
    })
    assert len(receipt.created_object_ids) == 1
    assert receipt.created_object_ids[0].startswith("asset:")
    assert conn.execute("SELECT COUNT(*) FROM tehm_assets").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_asset_authority_receipts").fetchone()[0] == 0


def test_shadow_capability_update_is_candidate_only_and_discarded(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    witness = _anti_forgetting("capability")
    plan = _plan(transition_id, "UPDATE_CAPABILITY", "ADD",
                 refs=(transition_id, witness.receipt_digest))
    receipt = apply_localized_update_shadow(plan, conn, {
        **_anti_evidence(witness),
        "capability": {
            "mechanism_family": "RTL_REPAIR",
            "applicability": {"compatibility_profile": "rtl.fsm.single_guard.v1"},
            "required_rules": [], "required_assets": [],
            "obligations": {"TARGET": "PASS"}, "budget": {"max_runs": 1},
        }
    })
    assert receipt.created_object_ids[0].startswith("capability:")
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_capabilities").fetchone()[0] == 0


def test_shadow_update_rejects_non_learner_and_tampered_receipt(tmp_tehm):
    _conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    # LocalizedUpdatePlan itself rejects a mutating audit-only proposal.
    with pytest.raises(ValueError, match="audit-only evidence"):
        _plan(
            transition_id, "UPDATE_STATE_RELATION", "INVALIDATE",
            refs=(transition_id,), learner_eligible=False)


def test_shadow_receipt_replays_and_rejects_digest_tampering(tmp_tehm):
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug")
    second = _capture(tmp_tehm, "req_ack_bug2")
    witness = _anti_forgetting("replay")
    plan = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE",
                 refs=(first, witness.receipt_digest))
    receipt = apply_localized_update_shadow(plan, conn, {
        **_anti_evidence(witness),
        "relation": {
            "source_type": "transition", "source_id": first,
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": second, "evidence_refs": [first],
        }
    })
    assert AppliedShadowUpdateReceipt.from_dict(receipt.to_dict()) == receipt
    tampered = {**receipt.to_dict(), "after_resolution_id": "tampered"}
    with pytest.raises(ShadowUpdateError, match="replay digest mismatch|receipt digest"):
        AppliedShadowUpdateReceipt.from_dict(tampered)


def test_shadow_update_requires_and_records_explicit_p12_trigger(tmp_tehm):
    """A P13 mutation must carry the content-addressed P12 witness."""
    conn, _, _ = tmp_tehm
    first = _capture(tmp_tehm, "req_ack_bug")
    second = _capture(tmp_tehm, "req_ack_bug2")
    witness = _anti_forgetting("p12")

    def execution(case_id, candidate_id, source):
        return CandidateExecutionReceipt(
            case_id=case_id, candidate_id=candidate_id, source=source,
            action_digest="sha256:action-" + candidate_id,
            candidate_digest="sha256:candidate-" + candidate_id,
            compile_result="PASS", functional_result="PASS", signoff_result="PASS",
            outcome="PASS", created_regressions=(), obligations={},
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            produced_transition_id=None, budget=3,
            metadata={"oracle_available": True})

    cases = {}
    routing_decisions = {}
    for index, lineage in enumerate(("p12-lineage-a", "p12-lineage-b")):
        case_id = f"p12-case-{index}"
        routing = MemoryRoutingDecision(
            decision="CONSIDER", resolved_state_id=f"state:{case_id}",
            selected_rule_ids=(f"rule:{case_id}",),
            selected_path_ids=(f"path:{case_id}",), selected_asset_ids=(),
            applicability={"status": "APPLICABLE"},
            causal_support={"status": "SUPPORTED"}, risk={},
            abstain_reasons=(), no_memory_budget=2, memory_budget=1)
        routing_decisions[case_id] = routing
        baseline = execution(case_id, f"no_memory:{case_id}", "no_memory")
        memory = execution(case_id, f"memory:{case_id}", "structured_memory")
        cases[case_id] = PairedCandidateExecutionReceipt(
            case_id=case_id,
            arm_receipts={"NO_MEMORY": baseline, "ALWAYS_MEMORY": memory,
                          "APPLICABILITY_GATED": memory, "CAUSAL_NO_SKILL": memory},
            candidate_budget=3, case_digest="sha256:case-" + case_id,
            toolchain_digest="sha256:tool", oracle_digest="sha256:oracle",
            lineage_id=lineage, routing_receipt_id=routing.routing_receipt_id)

    class Cohort:
        campaign_id = "live"
        case_receipts = cases
        source_disjoint = True
        source_restore_verified = True
        evaluation_only = True
        receipt_digest = "sha256:p12-cohort"

    trigger = build_p12_shadow_update_triggers(
        Cohort(), memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        routing_decisions=routing_decisions,
        case_learner_eligibility={case_id: True for case_id in cases},
        evolution_reasons={case_id: ("CAPABILITY_GAP",) for case_id in cases})[0]
    plan = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE",
                 refs=(first, trigger.receipt_digest, witness.receipt_digest))
    receipt = apply_localized_update_shadow(plan, conn, {
        **_anti_evidence(witness),
        "p12_shadow_trigger": {
            **trigger.to_dict(), "receipt_digest": trigger.receipt_digest},
        "relation": {
            "source_type": "transition", "source_id": first,
            "relation_type": "INVALIDATES", "target_type": "transition",
            "target_id": second, "evidence_refs": [first],
        },
        })
    assert receipt.metadata["p12_shadow_trigger_digest"] == trigger.receipt_digest
    assert receipt.metadata["anti_forgetting_witness_digest"] == witness.receipt_digest

    tampered = _plan(first, "UPDATE_STATE_RELATION", "INVALIDATE", refs=(first,))
    with pytest.raises(ShadowUpdateError, match="P12 trigger digest"):
        apply_localized_update_shadow(tampered, conn, {
            "p12_shadow_trigger": {
                **trigger.to_dict(), "receipt_digest": trigger.receipt_digest},
            "relation": {
                "source_type": "transition", "source_id": first,
                "relation_type": "INVALIDATES", "target_type": "transition",
                "target_id": second, "evidence_refs": [first],
            },
            })


def test_mutating_shadow_update_requires_eligible_anti_forgetting_witness(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _capture(tmp_tehm, "req_ack_bug")
    plan = _plan(transition_id, "UPDATE_ASSET", "ADD", refs=(transition_id,))
    with pytest.raises(ShadowUpdateError, match="anti-forgetting witness"):
        apply_localized_update_shadow(plan, conn, {
            "asset": {
                "asset_type": "DIAGNOSTIC_EXTRACTOR", "name": "missing.witness",
                "version": "0.1", "definition": {"kind": "diagnostic"},
                "input_contract": {"state": "object"},
                "output_contract": {"diagnostic": "object"},
                "verifier_contract": {"independent": True},
                "compatibility": {"mechanism_family": "RTL_REPAIR"},
            }
        })

    witness = _anti_forgetting("ineligible")
    bad = AntiForgettingWitness(
        **{**witness.__dict__, "heldout_audit_passed": False})
    plan = _plan(transition_id, "UPDATE_ASSET", "ADD",
                 refs=(transition_id, bad.receipt_digest))
    with pytest.raises(ShadowUpdateError, match="anti-forgetting witness"):
        apply_localized_update_shadow(plan, conn, {
            "anti_forgetting": {
                **bad.to_dict(), "receipt_digest": bad.receipt_digest},
            "asset": {
                "asset_type": "DIAGNOSTIC_EXTRACTOR", "name": "bad.witness",
                "version": "0.1", "definition": {"kind": "diagnostic"},
                "input_contract": {"state": "object"},
                "output_contract": {"diagnostic": "object"},
                "verifier_contract": {"independent": True},
                "compatibility": {"mechanism_family": "RTL_REPAIR"},
            }
        })


def _typed_knowledge_evidence(witness: AntiForgettingWitness, claim_payload: dict,
                              *, transition_id: str, parent_ids=(), extra=None) -> dict:
    evidence = {
        "transition_ids": [transition_id],
        **_anti_evidence(witness),
        "knowledge": claim_payload,
        "knowledge_evidence_refs": [{
            "evidence_type": "p13_reason", "evidence_id": "reason-" + claim_payload["knowledge_id"],
            "split": "training", "lineage_id": "lineage-p13",
            "evidence_level": claim_payload["evidence_level"],
        }],
    }
    if parent_ids:
        evidence["parent_object_ids"] = list(parent_ids)
    if extra:
        evidence.update(extra)
    return evidence


def _verified_shadow_transition(tmp_tehm):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    return _capture(tmp_tehm, "req_ack_bug", oracle=oracle)


def test_shadow_knowledge_revise_and_specialize_are_typed_and_discarded(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _verified_shadow_transition(tmp_tehm)
    parent = _knowledge_claim("p13-parent")
    _register_shadow_knowledge(conn, parent)
    before_knowledge = conn.execute(
        "SELECT COUNT(*) FROM tehm_mechanism_knowledge").fetchone()[0]
    before_relations = conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0]
    witness = _anti_forgetting("knowledge-revise")
    child = replace(parent, version=2,
                    intervention={"family": "GUARD_RESTORE", "variant": "revised"})
    plan = _plan(
        transition_id, "UPDATE_CAUSAL_KNOWLEDGE", "REVISE",
        refs=(transition_id, witness.receipt_digest),
        knowledge_refs=(parent.object_id,))
    receipt = apply_localized_update_shadow(
        plan, conn,
        _typed_knowledge_evidence(witness, child.to_dict(),
                                  transition_id=transition_id,
                                  parent_ids=(parent.object_id,)))
    assert f"knowledge:{child.object_id}" in receipt.created_object_ids
    assert receipt.created_relation_ids
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_mechanism_knowledge").fetchone()[0] == before_knowledge
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_memory_relations").fetchone()[0] == before_relations

    specialize = replace(parent, knowledge_id="p13-specialized", version=1,
                         intervention={"family": "GUARD_RESTORE", "variant": "narrow"})
    witness = _anti_forgetting("knowledge-specialize")
    plan = _plan(
        transition_id, "UPDATE_CAUSAL_KNOWLEDGE", "SPECIALIZE",
        refs=(transition_id, witness.receipt_digest),
        knowledge_refs=(parent.object_id,))
    receipt = apply_localized_update_shadow(
        plan, conn,
        _typed_knowledge_evidence(witness, specialize.to_dict(),
                                  transition_id=transition_id,
                                  parent_ids=(parent.object_id,)))
    assert f"knowledge:{specialize.object_id}" in receipt.created_object_ids
    assert receipt.created_relation_ids
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_mechanism_knowledge").fetchone()[0] == before_knowledge


def test_shadow_knowledge_split_and_merge_require_explicit_witnesses(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _verified_shadow_transition(tmp_tehm)
    parent = _knowledge_claim("p13-split-parent")
    _register_shadow_knowledge(conn, parent)
    child_a = replace(parent, knowledge_id="p13-split-a", version=1,
                      intervention={"family": "GUARD_RESTORE", "variant": "a"})
    child_b = replace(parent, knowledge_id="p13-split-b", version=1,
                      intervention={"family": "GUARD_RESTORE", "variant": "b"})
    witness = _anti_forgetting("knowledge-split")
    split_plan = _plan(
        transition_id, "UPDATE_CAUSAL_KNOWLEDGE", "SPLIT",
        refs=(transition_id, witness.receipt_digest),
        knowledge_refs=(parent.object_id,))
    split_evidence = _typed_knowledge_evidence(
        witness, child_a.to_dict(), transition_id=transition_id,
        parent_ids=(parent.object_id,), extra={
            "knowledge_children": [child_a.to_dict(), child_b.to_dict()],
            "partition_evidence": {
                child_a.object_id: ["partition-a"],
                child_b.object_id: ["partition-b"],
            },
        })
    receipt = apply_localized_update_shadow(split_plan, conn, split_evidence)
    assert {f"knowledge:{child.object_id}" for child in (child_a, child_b)} <= set(
        receipt.created_object_ids)
    assert len(receipt.created_relation_ids) == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_mechanism_knowledge").fetchone()[0] == 1

    parent_b = _knowledge_claim("p13-merge-parent")
    _register_shadow_knowledge(conn, parent_b)
    merged = replace(parent, knowledge_id="p13-merged", version=1,
                     intervention={"family": "GUARD_RESTORE", "variant": "merged"})
    witness = _anti_forgetting("knowledge-merge")
    merge_plan = _plan(
        transition_id, "UPDATE_CAUSAL_KNOWLEDGE", "MERGE",
        refs=(transition_id, witness.receipt_digest),
        knowledge_refs=(parent.object_id, parent_b.object_id))
    merge_evidence = _typed_knowledge_evidence(
        witness, merged.to_dict(), transition_id=transition_id,
        parent_ids=(parent.object_id, parent_b.object_id), extra={
            "merge_witness": {
                parent.object_id: ["merge-a"],
                parent_b.object_id: ["merge-b"],
            },
        })
    receipt = apply_localized_update_shadow(merge_plan, conn, merge_evidence)
    assert f"knowledge:{merged.object_id}" in receipt.created_object_ids
    assert len(receipt.created_relation_ids) == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_mechanism_knowledge").fetchone()[0] == 2


def test_shadow_knowledge_revision_rejects_validated_claim(tmp_tehm):
    conn, _, _ = tmp_tehm
    transition_id = _verified_shadow_transition(tmp_tehm)
    parent = _knowledge_claim("p13-status-parent")
    _register_shadow_knowledge(conn, parent)
    witness = _anti_forgetting("knowledge-status")
    validated = replace(parent, knowledge_id="p13-status-child", version=1,
                        status="validated")
    plan = _plan(
        transition_id, "UPDATE_CAUSAL_KNOWLEDGE", "SPECIALIZE",
        refs=(transition_id, witness.receipt_digest),
        knowledge_refs=(parent.object_id,))
    with pytest.raises(ShadowUpdateError, match="cannot grant validated"):
        apply_localized_update_shadow(
            plan, conn,
            _typed_knowledge_evidence(witness, validated.to_dict(),
                                      transition_id=transition_id,
                                      parent_ids=(parent.object_id,)))
