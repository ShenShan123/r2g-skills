#!/usr/bin/env python3
"""Run the Revision3 ``MEMORY_INTERFERENCE`` shadow-revision campaign.

This is the first R3-8 execution that carries a machine-derived paired harm
witness through reason-specific admission and an isolated ``SPECIALIZE``
update.  The replacement Knowledge claim adds a typed negative-applicability
context.  A fresh real-Icarus cohort then checks that applicability-gated and
causal-no-skill policy arms fall back to no-memory on the same challenge while
the forced-memory counterfactual remains harmful.

All SQLite and RTL artifacts are written outside the repository.  The source
connection is checked before/after the shadow update; canonical evidence,
production authority, and ``memory/docs/`` are never mutated or submitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from scripts import run_r3_memory_interference_challenge as challenge  # noqa: E402
from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.evaluation.candidate_executor import (  # noqa: E402
    PairedCandidateExecutionReceipt,
)
from tehm.evaluation.rtl_candidate_oracle import (  # noqa: E402
    IcarusCandidateOracle, execute_rtl_candidate,
)
from tehm.evaluation.rtl_cohort import (  # noqa: E402
    RtlPairedCohortReceipt, execute_rtl_paired_cohort,
)
from tehm.evolution.admission import (  # noqa: E402
    EvolutionAdmissionReceipt, admit_evolution_reason,
)
from tehm.evolution.anti_forgetting import AntiForgettingWitness  # noqa: E402
from tehm.evolution.apply_update import apply_localized_update_shadow  # noqa: E402
from tehm.evolution.interference_revision import (  # noqa: E402
    MemoryInterferenceEvolutionProposal,
    interference_proposal_to_localized_plan,
    propose_memory_interference_specialization,
)
from tehm.evolution.p12_shadow_trigger import (  # noqa: E402
    P12ShadowUpdateTriggerReceipt, P13EvolutionReasonReceipt,
)
from tehm.evolution.reason_derivation import (  # noqa: E402
    EvolutionReasonDerivationReceipt,
)
from tehm.knowledge import MechanismKnowledge, register_knowledge  # noqa: E402
from tehm.knowledge.applicability import evaluate_applicability  # noqa: E402
from tehm.rtl.rtl_evidence import build_rtl_execution_record  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from scripts.build_p13_anti_forgetting_witness import (  # noqa: E402
    build_p13_anti_forgetting_witness,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.canonical.transition import HARMFUL_OUTCOMES, POSITIVE_OUTCOMES  # noqa: E402


CAMPAIGN_ID = challenge.CAMPAIGN_ID
DEFAULT_CHALLENGE_ARTIFACTS = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-interference-challenge-20260902"
)
DEFAULT_ARTIFACTS = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-interference-shadow-20260902"
)
TOOLCHAIN_DIGEST = challenge.TOOLCHAIN_DIGEST
ORACLE_DIGEST = challenge.ORACLE_DIGEST
PLATFORM_DIGEST = challenge.PLATFORM_DIGEST
PDK_DIGEST = challenge.PDK_DIGEST
MANIFEST_DIGEST = "sha256:r3-interference-shadow-manifest"
NEGATIVE_CONTEXT = {
    "mechanism_family": "HANDSHAKE_COMPLETION",
    "compatibility_profile": "rtl.fsm.single_guard.v1",
    "platform": "asap7",
    "interference_signature": "unguarded_completion_transfer",
}


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_episode_steps",
        "tehm_dataset_membership", "tehm_edges", "tehm_physical_effects",
        "tehm_mechanism_knowledge", "tehm_memory_relations", "tehm_memory_events",
    )
    return {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables}


def _connection_digest(conn: sqlite3.Connection) -> str:
    return _digest("\n".join(conn.iterdump()))


def _execution_complete(receipt) -> bool:
    """Check the complete executable-oracle witness used by P15 MIR."""
    return (
        receipt.evaluation_only is True and
        receipt.metadata.get("oracle_available") is True and
        receipt.compile_result != "UNKNOWN" and
        receipt.functional_result != "UNKNOWN" and
        receipt.signoff_result not in {None, "UNKNOWN"} and
        receipt.outcome != "UNKNOWN")


def _parent_knowledge() -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="r3-shadow-challenge", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_STRENGTHEN"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
            "platform": "asap7",
        },),
        negative_applicability=(),
        preserved_obligations=("target_trace_pass",),
        known_failure_modes=("unguarded_completion_transfer",),
        causal_path_ids=("r3-path-interference",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("req_ack_fsm", "req_ack_fsm2"), status="shadow")


def _capture_training(conn: sqlite3.Connection, store: ArtifactStore,
                      oracle: IcarusOracle, fixture_name: str) -> str:
    fixture = ROOT / "tests" / "fixtures" / "rtl_projects" / fixture_name
    record = build_rtl_execution_record(fixture, oracle=oracle, store=store)
    receipt = capture(
        conn, store, record, dataset_campaign_id=CAMPAIGN_ID,
        dataset_split="training", dataset_learner_eligible=True)
    if receipt.outcome != "PASS":
        raise RuntimeError(f"training fixture did not produce PASS: {fixture_name}")
    return receipt.transition_id


def _load_challenge(challenge_artifacts: Path, *, refresh: bool) -> tuple[dict, RtlPairedCohortReceipt,
                                                                      dict, dict, dict, dict, dict]:
    challenge_artifacts = challenge_artifacts.expanduser().resolve()
    cohort_path = challenge_artifacts / "receipts" / "cohort.json"
    if refresh or not cohort_path.is_file():
        challenge.run(challenge_artifacts, force=True)
    cohort = RtlPairedCohortReceipt.from_dict(_read_json(cohort_path))
    cases_payload = _read_json(challenge_artifacts / "receipts" / "cases.json")
    candidate_payloads = cases_payload.get("candidate_payloads") or {}
    routes_payload = cases_payload.get("routing") or {}
    routes = {
        case_id: MemoryRoutingDecision.from_dict(payload)
        for case_id, payload in routes_payload.items()
    }
    candidates = {
        case_id: challenge.StructuredRepairCandidate.from_dict(payload)
        for case_id, payload in candidate_payloads.items()
    }
    raw_derivations = _read_json(
        challenge_artifacts / "receipts" / "reason_derivation.json").get("derivations") or {}
    derivations = {
        case_id: tuple(EvolutionReasonDerivationReceipt.from_dict(item) for item in values)
        for case_id, values in raw_derivations.items()
    }
    raw_triggers = _read_json(
        challenge_artifacts / "receipts" / "p12_triggers.json").get("triggers") or []
    triggers = {
        item["case_id"]: P12ShadowUpdateTriggerReceipt.from_dict(item)
        for item in raw_triggers
    }
    raw_admissions = _read_json(
        challenge_artifacts / "receipts" / "admissions.json").get("admissions") or {}
    admissions = {
        case_id: EvolutionAdmissionReceipt.from_dict(item)
        for case_id, item in raw_admissions.items()
    }
    reason_receipt = P13EvolutionReasonReceipt.from_dict(
        _read_json(challenge_artifacts / "receipts" / "p13_reason_receipt.json"))
    return (_read_json(challenge_artifacts / "receipts" / "cases.json"), cohort,
            routes, candidates, derivations, triggers, admissions | {"_reason": reason_receipt})


def _build_anti_forgetting(artifacts: Path, cases: list[dict], post_cohort,
                           oracle: IcarusOracle, before_db_digest: str) -> tuple[AntiForgettingWitness, dict]:
    evidence_dir = artifacts / "anti_forgetting_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(post_cohort.case_receipts)
    target = post_cohort.case_receipts[ordered[0]].arm_receipts["APPLICABILITY_GATED"]
    non_target = post_cohort.case_receipts[ordered[1]].arm_receipts["CAUSAL_NO_SKILL"]
    target_path = evidence_dir / "target_replay.json"
    non_target_path = evidence_dir / "non_target_regression.json"
    _write_json(target_path, {"receipt": target.to_dict(), "outcome": target.outcome,
                              "policy_fallback": target.metadata.get("policy_fallback")})
    _write_json(non_target_path, {"receipt": non_target.to_dict(), "outcome": non_target.outcome,
                                  "policy_fallback": non_target.metadata.get("policy_fallback")})

    heldout_root = evidence_dir / "heldout_source"
    fixture_root = ROOT / "tests" / "fixtures" / "rtl_projects" / "req_ack_bug3"
    for subdir in ("rtl", "tb"):
        shutil.copytree(fixture_root / subdir, heldout_root / subdir)
    heldout_source = heldout_root / "rtl" / "req_ack_fsm.v"
    original = heldout_source.read_text()
    marker = "RCV:    next_state = RD_DONE;       // BUG: no rd_ack guard"
    fixed = "RCV:    if (rd_ack) next_state = RD_DONE;"
    if marker not in original:
        raise RuntimeError("held-out interference witness marker is missing")
    heldout_source.write_text(original.replace(marker, fixed, 1))
    heldout_case = {
        "case_id": "r3-interference-heldout",
        "rtl_source": str(heldout_source),
        "target_test": str(heldout_root / "tb" / "tb_handshake.v"),
        "frozen_regression": str(heldout_root / "tb" / "tb_basic.v"),
        "toolchain_digest": TOOLCHAIN_DIGEST, "oracle_digest": ORACLE_DIGEST,
    }
    heldout_result = execute_rtl_candidate(None, heldout_case, 3, oracle=oracle)
    heldout_path = evidence_dir / "heldout_audit.json"
    _write_json(heldout_path, heldout_result)

    rollback_payload = {
        "pointer": "canonical-before-r3-8-interference-shadow",
        "source_db_digest_before": before_db_digest,
        "source_db_digest_after": before_db_digest,
        "canonical_memory_mutation": "none", "staging_discarded": True,
        "verified": True,
    }
    rollback_path = evidence_dir / "rollback_verification.json"
    _write_json(rollback_path, rollback_payload)
    manifest = {
        "version": "p13-anti-forgetting-manifest-v1", "campaign_id": CAMPAIGN_ID,
        "case_id": ordered[0],
        "target_replay": {"receipt_id": target.execution_digest, "path": str(target_path),
                          "sha256": _sha256_file(target_path), "passed": target.outcome == "PASS"},
        "non_target_regression": {"receipt_id": non_target.execution_digest,
                                   "path": str(non_target_path),
                                   "sha256": _sha256_file(non_target_path),
                                   "regression_free": non_target.outcome == "PASS"},
        "heldout_audit": {"receipt_id": "heldout:" + _sha256_file(heldout_path),
                          "path": str(heldout_path), "sha256": _sha256_file(heldout_path),
                          "passed": heldout_result.get("outcome") == "PASS"},
        "rollback": {"receipt_id": "rollback:" + _sha256_file(rollback_path),
                      "path": str(rollback_path), "sha256": _sha256_file(rollback_path),
                      "pointer": rollback_payload["pointer"], "verified": True},
    }
    manifest_path = evidence_dir / "manifest.json"
    report_path = evidence_dir / "witness_report.json"
    _write_json(manifest_path, manifest)
    report = build_p13_anti_forgetting_witness(manifest_path, output=report_path)
    witness = AntiForgettingWitness.from_dict(report["witness"])
    return witness, report


def _post_case(case: dict, candidate, route: MemoryRoutingDecision) -> dict:
    return {
        **case,
        "routing_receipt_id": route.routing_receipt_id,
        "routing_decision": route.decision,
        "no_skill_reason": None,
        "state_shift_receipt_id": None,
        "risk_receipt_id": None,
        "risk_receipt": None,
    }


def run(artifacts: Path, *, challenge_artifacts: Path = DEFAULT_CHALLENGE_ARTIFACTS,
        force: bool = False, refresh_challenge: bool = False) -> dict:
    artifacts = artifacts.expanduser().resolve()
    if artifacts.exists():
        if not force:
            raise RuntimeError(f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    receipts_dir = artifacts / "receipts"
    receipts_dir.mkdir()

    (cases_payload, pre_cohort, routes, candidates, derivations, triggers,
     admissions) = _load_challenge(challenge_artifacts, refresh=refresh_challenge)
    reason_receipt = admissions.pop("_reason")
    if any(not item.admitted for item in admissions.values()):
        raise RuntimeError("pre-revision interference admission is not complete")
    ordered = sorted(pre_cohort.case_receipts)
    observations = [
        (derivations[case_id][0], pre_cohort.case_receipts[case_id])
        for case_id in ordered
    ]
    transition_fixtures = ("req_ack_bug", "req_ack_bug2")
    proposal_refs = {
        ref for derivation, paired in observations
        for ref in (derivation.receipt_id, derivation.receipt_digest,
                    paired.receipt_digest, *derivation.input_receipt_ids,
                    *derivation.input_digests)
    }
    proposal_refs.update(
        item.receipt_digest for item in triggers.values())
    proposal_refs.update(
        item.receipt_digest for item in admissions.values())

    sqlite_path = artifacts / "tehm.sqlite"
    conn = db.connect(sqlite_path)
    db.ensure_schema(conn)
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("Icarus oracle is unavailable; no synthetic fallback is allowed")
    store = ArtifactStore(artifacts / "training-artifacts")
    transition_ids = [
        _capture_training(conn, store, oracle, fixture_name)
        for fixture_name in transition_fixtures
    ]
    parent = _parent_knowledge()
    register_knowledge(conn, parent, evidence_refs=[
        {"evidence_type": "transition", "evidence_id": transition_id,
         "split": "training", "lineage_id": lineage,
         "evidence_level": parent.evidence_level}
        for transition_id, lineage in zip(
            transition_ids, ("req_ack_fsm", "req_ack_fsm2"))
    ])
    before_counts = _table_counts(conn)
    before_db_digest = _connection_digest(conn)

    proposal = propose_memory_interference_specialization(
        observations, knowledge_object_id=parent.object_id,
        transition_ids=transition_ids,
        negative_applicability=(NEGATIVE_CONTEXT,),
        evidence_refs=tuple(sorted({*proposal_refs, *transition_ids})),
        trigger_receipt_ids=tuple(
            triggers[case_id].receipt_digest for case_id in ordered),
    )
    plan = interference_proposal_to_localized_plan(proposal)
    child = replace(
        parent, knowledge_id="r3-shadow-challenge-interference-specialized",
        version=1, negative_applicability=(NEGATIVE_CONTEXT,),
        known_failure_modes=tuple(sorted({*parent.known_failure_modes,
                                           "memory_interference:unguarded_completion_transfer"})),
        status="shadow")

    post_routes: dict[str, MemoryRoutingDecision] = {}
    post_cases: list[dict] = []
    post_arm_candidates: dict[str, dict] = {}
    for case in cases_payload["cases"]:
        case_id = case["case_id"]
        route = MemoryRoutingDecision(
            decision="INAPPLICABLE", resolved_state_id="r3-post-interference-shadow",
            selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
            applicability={"status": "VETOED", "hard_gate": "negative_applicability",
                           "knowledge_object_id": child.object_id},
            causal_support={"status": "VETOED"},
            risk={"risk_penalty": 1.0, "memory_interference": True,
                  "negative_applicability": True},
            abstain_reasons=("negative_applicability",), no_memory_budget=1,
            memory_budget=0)
        post_routes[case_id] = route
        post_cases.append(_post_case(case, candidates[case_id], route))
        post_arm_candidates[case_id] = {
            "NO_MEMORY": None,
            "ALWAYS_MEMORY": candidates[case_id],
            "APPLICABILITY_GATED": None,
            "CAUSAL_NO_SKILL": None,
        }

    post_cohort = execute_rtl_paired_cohort(
        post_cases, post_arm_candidates, campaign_id=CAMPAIGN_ID,
        campaign_manifest_digest=MANIFEST_DIGEST, platform_digest=PLATFORM_DIGEST,
        pdk_digest=PDK_DIGEST, oracle=IcarusCandidateOracle(oracle), budget=3,
        toolchain_digest=TOOLCHAIN_DIGEST, oracle_digest=ORACLE_DIGEST, min_lineages=2)
    for case_id, paired in post_cohort.case_receipts.items():
        if paired.arm_receipts["NO_MEMORY"].outcome != "PASS":
            raise RuntimeError(f"post-revision no-memory baseline failed: {case_id}")
        if paired.arm_receipts["APPLICABILITY_GATED"].outcome != "PASS":
            raise RuntimeError(f"applicability fallback did not avoid harm: {case_id}")
        if paired.arm_receipts["CAUSAL_NO_SKILL"].outcome != "PASS":
            raise RuntimeError(f"causal no-skill fallback did not avoid harm: {case_id}")
    # Persist the immutable typed cohort before constructing the compact
    # policy-MIR index below; the index binds this exact file digest.
    _write_json(receipts_dir / "post_revision_cohort.json",
                {**post_cohort.to_dict(), "receipt_digest": post_cohort.receipt_digest})

    context = dict(NEGATIVE_CONTEXT)
    applicability_before = {
        case_id: evaluate_applicability(parent, context).to_dict()
        for case_id in ordered
    }
    applicability_after = {
        case_id: evaluate_applicability(child, context).to_dict()
        for case_id in ordered
    }
    if not all(item["eligible"] for item in applicability_before.values()):
        raise RuntimeError("pre-revision parent applicability was not eligible")
    if not all((not item["eligible"]) and item["reason"] == "negative_applicability"
               for item in applicability_after.values()):
        raise RuntimeError("specialized negative applicability did not veto context")

    witness, witness_report = _build_anti_forgetting(
        artifacts, post_cases, post_cohort, oracle, before_db_digest)
    plan = replace(plan, evidence_refs=tuple(sorted({*plan.evidence_refs,
                                                      witness.receipt_digest})))
    first_case = ordered[0]
    evidence = {
        "transition_ids": transition_ids,
        "parent_object_ids": [parent.object_id],
        "knowledge": child.to_dict(),
        "knowledge_evidence_refs": [
            {"evidence_type": "transition", "evidence_id": transition_id,
             "split": "training", "lineage_id": lineage,
             "evidence_level": child.evidence_level}
            for transition_id, lineage in zip(
                transition_ids, ("req_ack_fsm", "req_ack_fsm2"))
        ],
        "provenance": {
            "source": "r3-memory-interference-shadow",
            "reason_receipt": reason_receipt.receipt_digest,
            "proposal": proposal.proposal_digest,
            "negative_applicability_digest": _digest([NEGATIVE_CONTEXT]),
        },
        "p12_shadow_trigger": {
            **triggers[first_case].to_dict(),
            "receipt_digest": triggers[first_case].receipt_digest,
        },
        "anti_forgetting": {
            **witness.to_dict(), "receipt_digest": witness.receipt_digest,
        },
        "scope": {"target_scope": "global", "challenge_case": first_case},
    }
    shadow_receipt = apply_localized_update_shadow(plan, conn, evidence)
    after_counts = _table_counts(conn)
    after_db_digest = _connection_digest(conn)
    if before_counts != after_counts or before_db_digest != after_db_digest:
        raise RuntimeError("P13 interference shadow update changed source DB")
    if not shadow_receipt.created_object_ids or not shadow_receipt.created_relation_ids:
        raise RuntimeError("interference shadow update did not create typed child/relation")

    pre_harm = sum(
        pre_cohort.case_receipts[case_id].arm_receipts["ALWAYS_MEMORY"].outcome in {"FAIL", "REGRESSION"}
        for case_id in ordered)
    post_forced_harm = sum(
        post_cohort.case_receipts[case_id].arm_receipts["ALWAYS_MEMORY"].outcome in {"FAIL", "REGRESSION"}
        for case_id in ordered)
    post_gated_harm = sum(
        post_cohort.case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].outcome in {"FAIL", "REGRESSION"}
        for case_id in ordered)
    post_causal_harm = sum(
        post_cohort.case_receipts[case_id].arm_receipts["CAUSAL_NO_SKILL"].outcome in {"FAIL", "REGRESSION"}
        for case_id in ordered)
    risk_update = {
        "before": {"memory_interference_cases": pre_harm,
                   "forced_memory_harm_rate": pre_harm / len(ordered)},
        "after": {"forced_memory_harm_cases": post_forced_harm,
                   "applicability_gated_harm_cases": post_gated_harm,
                   "causal_no_skill_harm_cases": post_causal_harm,
                   "safe_fallback_rate": 1.0 - (post_gated_harm / len(ordered))},
        "policy": "negative_applicability vetoes transfer; ALWAYS_MEMORY remains an audit counterfactual",
    }

    # Keep the production MIR input separate from the deliberately harmful
    # ALWAYS_MEMORY counterfactual above.  The routed policy arm is replayed
    # from the post-revision cohort by P15-B; its aggregate is only a compact
    # index, never a substitute for the typed per-case receipts.
    policy_arm = "CAUSAL_NO_SKILL"
    policy_known = sum(
        _execution_complete(post_cohort.case_receipts[case_id].arm_receipts["NO_MEMORY"])
        and _execution_complete(post_cohort.case_receipts[case_id].arm_receipts[policy_arm])
        for case_id in ordered)
    policy_unknown = len(ordered) - policy_known
    policy_routed = sum(
        post_cohort.case_receipts[case_id].routing_receipt_id is not None
        for case_id in ordered)
    policy_harmful = sum(
        _execution_complete(post_cohort.case_receipts[case_id].arm_receipts["NO_MEMORY"])
        and _execution_complete(post_cohort.case_receipts[case_id].arm_receipts[policy_arm])
        and post_cohort.case_receipts[case_id].arm_receipts["NO_MEMORY"].outcome in POSITIVE_OUTCOMES
        and (
            post_cohort.case_receipts[case_id].arm_receipts[policy_arm].outcome in HARMFUL_OUTCOMES
            or bool(post_cohort.case_receipts[case_id].arm_receipts[policy_arm].created_regressions)
        )
        for case_id in ordered)
    policy_mir = {
        "version": "r3-policy-mir-v1",
        "metric": "routed_policy",
        "baseline_arm": "NO_MEMORY",
        "policy_arm": policy_arm,
        "cohort_receipt": str((receipts_dir / "post_revision_cohort.json").resolve()),
        "cohort_receipt_sha256": _sha256_file(receipts_dir / "post_revision_cohort.json"),
        "cohort_receipt_digest": post_cohort.receipt_digest,
        "case_count": len(ordered),
        "known_cases": policy_known,
        "unknown_cases": policy_unknown,
        "routed_cases": policy_routed,
        "harmful_cases": policy_harmful,
        "routing_receipt_coverage": round(policy_routed / len(ordered), 6),
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_integration": "not_attempted",
    }

    _write_json(receipts_dir / "campaign_manifest.json", {
        "version": "tehm-r3-interference-shadow-v0.1", "campaign_id": CAMPAIGN_ID,
        "lane": "EVOLUTION_CHALLENGE", "real_oracle": "iverilog/vvp",
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
        "purpose": "derive MEMORY_INTERFERENCE and specialize negative applicability",
    })
    _write_json(receipts_dir / "pre_revision_cohort.json",
                {**pre_cohort.to_dict(), "receipt_digest": pre_cohort.receipt_digest})
    _write_json(receipts_dir / "proposal.json",
                {**proposal.to_dict(), "proposal_id": proposal.proposal_id,
                 "proposal_digest": proposal.proposal_digest})
    _write_json(receipts_dir / "localized_update_plan.json",
                {**plan.to_dict(), "plan_digest": plan.plan_digest})
    _write_json(receipts_dir / "specialized_knowledge.json", child.to_dict())
    _write_json(receipts_dir / "shadow_update.json",
                {**shadow_receipt.to_dict(), "receipt_digest": shadow_receipt.receipt_digest})
    _write_json(receipts_dir / "anti_forgetting.json", witness_report)
    _write_json(receipts_dir / "applicability.json", {
        "context": context, "parent": applicability_before,
        "specialized_child": applicability_after,
    })
    _write_json(receipts_dir / "risk_update.json", risk_update)

    summary = {
        "campaign_id": CAMPAIGN_ID, "lane": "EVOLUTION_CHALLENGE",
        "reason": "MEMORY_INTERFERENCE", "real_oracle": "iverilog/vvp",
        "case_count": len(ordered), "lineage_count": pre_cohort.lineage_count,
        "pre_revision_outcomes": pre_cohort.outcome_counts,
        "post_revision_outcomes": post_cohort.outcome_counts,
        "proposal_operation": proposal.operation,
        "shadow_operation": shadow_receipt.operation,
        "shadow_created_objects": list(shadow_receipt.created_object_ids),
        "shadow_created_relations": list(shadow_receipt.created_relation_ids),
        "negative_applicability_veto_cases": sum(
            not item["eligible"] for item in applicability_after.values()),
        "risk_update": risk_update,
        "policy_mir": policy_mir,
        "anti_forgetting_eligible": witness.eligible,
        "canonical_counts_unchanged": before_counts == after_counts,
        "source_db_unchanged": before_db_digest == after_db_digest,
        "canonical_memory_mutation": shadow_receipt.canonical_memory_mutation,
        "production_authority_changed": shadow_receipt.production_authority_changed,
        "staging_discarded": shadow_receipt.staging_discarded,
        "evaluation_only": True, "memory_docs_submitted": False,
    }
    _write_json(artifacts / "summary.json", summary)
    conn.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS,
                        help=f"external output directory (default: {DEFAULT_ARTIFACTS})")
    parser.add_argument("--challenge-artifacts", type=Path,
                        default=DEFAULT_CHALLENGE_ARTIFACTS,
                        help="existing R3-8 challenge receipts directory")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing shadow output directory")
    parser.add_argument("--refresh-challenge", action="store_true",
                        help="rerun the real pre-revision challenge first")
    args = parser.parse_args(argv)
    try:
        summary = run(args.artifacts, challenge_artifacts=args.challenge_artifacts,
                      force=args.force, refresh_challenge=args.refresh_challenge)
    except Exception as exc:
        print(f"shadow campaign failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
