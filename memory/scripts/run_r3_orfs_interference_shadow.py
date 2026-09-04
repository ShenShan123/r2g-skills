#!/usr/bin/env python3
"""Apply a real ORFS interference reason in isolated P13 staging.

The companion ``run_r3_orfs_interference_challenge.py`` proves a typed
``MEMORY_INTERFERENCE`` reason from real external ORFS executions.  This
producer takes that immutable challenge receipt, binds two real ORFS training
pairs into a disposable TEHM database, and applies the reason-specific
``SPECIALIZE`` proposal only to an in-memory SQLite backup.  A fresh post-route
cohort checks that both gated arms really fall back to no-memory, while the
forced-memory counterfactual remains an audit witness.

Nothing produced here is canonical memory or production authority.  All
artifacts are written outside the repository; ``memory/docs/`` is local-only
and is never read as a submission payload or copied into the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from scripts import run_r3_orfs_interference_challenge as challenge  # noqa: E402
from scripts.build_p13_anti_forgetting_witness import (  # noqa: E402
    build_p13_anti_forgetting_witness,
)
from tehm import db  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.evaluation.candidate_executor import P12_ARMS  # noqa: E402
from tehm.evaluation.orfs_candidate_oracle import (  # noqa: E402
    OrfsCandidateOracle, _file_sha256, _source_binding, _source_inputs,
    execute_orfs_candidate,
)
from tehm.evaluation.orfs_cohort import (  # noqa: E402
    OrfsPairedCohortReceipt, execute_orfs_paired_cohort,
)
from tehm.evolution.admission import EvolutionAdmissionReceipt  # noqa: E402
from tehm.evolution.anti_forgetting import (  # noqa: E402
    AntiForgettingWitness,
)
from tehm.evolution.apply_update import (  # noqa: E402
    AppliedShadowUpdateReceipt, apply_localized_update_shadow,
)
from tehm.evolution.interference_revision import (  # noqa: E402
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
from tehm.ids import stable_dumps  # noqa: E402
from tehm.capability.delta import memory_delta_from_shadow_update  # noqa: E402


SHADOW_CAMPAIGN_VERSION = "tehm-r3-orfs-interference-shadow-v0.1"
DEFAULT_CHALLENGE = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-challenge-20260903"
)


class OrfsInterferenceShadowError(ValueError):
    """The ORFS P13 shadow inputs are malformed or incomplete."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OrfsInterferenceShadowError(f"cannot read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise OrfsInterferenceShadowError(f"JSON must be an object: {path}")
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


def _load_challenge(challenge_artifacts: Path) -> tuple[
        dict, OrfsPairedCohortReceipt, dict[str, MemoryRoutingDecision],
        dict[str, challenge.StructuredRepairCandidate], dict[str, tuple],
        dict[str, P12ShadowUpdateTriggerReceipt], dict[str, EvolutionAdmissionReceipt],
        P13EvolutionReasonReceipt]:
    """Load and replay every typed pre-revision challenge receipt."""
    root = challenge_artifacts.expanduser().resolve()
    receipts = root / "receipts"
    cohort = OrfsPairedCohortReceipt.from_dict(_read_json(receipts / "cohort.json"))
    cases_payload = _read_json(receipts / "cases.json")
    routes = {
        case_id: MemoryRoutingDecision.from_dict(payload)
        for case_id, payload in (cases_payload.get("routing") or {}).items()
    }
    candidates = {
        case_id: challenge.StructuredRepairCandidate.from_dict(payload)
        for case_id, payload in (cases_payload.get("candidate_payloads") or {}).items()
    }
    raw_derivations = _read_json(receipts / "reason_derivation.json").get("derivations") or {}
    derivations = {
        case_id: tuple(EvolutionReasonDerivationReceipt.from_dict(item) for item in values)
        for case_id, values in raw_derivations.items()
    }
    raw_triggers = _read_json(receipts / "p12_triggers.json").get("triggers") or []
    triggers = {
        item["case_id"]: P12ShadowUpdateTriggerReceipt.from_dict(item)
        for item in raw_triggers
    }
    raw_admissions = _read_json(receipts / "admissions.json").get("admissions") or {}
    admissions = {
        case_id: EvolutionAdmissionReceipt.from_dict(item)
        for case_id, item in raw_admissions.items()
    }
    reason = P13EvolutionReasonReceipt.from_dict(
        _read_json(receipts / "p13_reason_receipt.json"))
    if (set(cohort.case_receipts) != set(routes) or
            set(routes) != set(candidates) or set(routes) != set(derivations) or
            set(routes) != set(triggers) or set(routes) != set(admissions) or
            set(routes) != set(reason.evolution_reasons)):
        raise OrfsInterferenceShadowError(
            "challenge receipts do not have exact case/reason coverage")
    if any(not item.admitted for item in admissions.values()):
        raise OrfsInterferenceShadowError("pre-revision challenge admission is incomplete")
    if reason.campaign_id != cohort.campaign_id or reason.cohort_receipt_digest != cohort.receipt_digest:
        raise OrfsInterferenceShadowError("challenge reason envelope does not bind cohort")
    return cases_payload, cohort, routes, candidates, derivations, triggers, admissions, reason


def _derive_edits(before: Path, after: Path) -> dict[str, str]:
    before_cfg = challenge._config_values(before)
    after_cfg = challenge._config_values(after)
    # The adapter needs a concrete executed config delta.  The producer only
    # binds the explicit CORE/PLATFORM/DESIGN values that changed; it never
    # evaluates config.mk as shell code.
    before_text = (before / "constraints" / "config.mk").read_text()
    after_text = (after / "constraints" / "config.mk").read_text()
    import re
    pattern = re.compile(r"^\s*export\s+([A-Za-z0-9_]+)\s*=\s*([^#\n]+)", re.MULTILINE)
    parse = lambda text: {key: value.strip() for key, value in pattern.findall(text)}
    old, new = parse(before_text), parse(after_text)
    edits = {key: new[key] for key in sorted(set(old) | set(new))
             if key in new and old.get(key) != new[key]}
    if not edits:
        raise OrfsInterferenceShadowError(
            f"training pair has no concrete config delta: {before} -> {after}")
    # Ensure the public parser was able to identify the actual design/platform;
    # retaining these values in the record prevents an accidental unrelated
    # pair from being accepted by a future caller.
    if before_cfg[:2] != after_cfg[:2]:
        raise OrfsInterferenceShadowError("training pair design/platform changed")
    return edits


def _capture_training(
        conn: sqlite3.Connection, store: ArtifactStore,
        before: Path, after: Path, lineage: str, campaign_id: str) -> str:
    record = build_orfs_pair_record(
        before, after, lineage_id=lineage, target_check="route",
        config_edits=_derive_edits(before, after),
        transformation_family="DENSITY_RELIEF", rerun_from="floorplan")
    captured = capture(
        conn, store, record, dataset_campaign_id=campaign_id,
        dataset_split="training", dataset_learner_eligible=True)
    # A clean-before/clean-after ORFS pair is an intentionally neutral
    # observation, so ``capture`` classifies its transition as NEUTRAL even
    # though the executable oracle is complete and PASS.  Learner admission
    # is based on the verifier witness, not on the repair-outcome taxonomy.
    if (record.verification.get("verdict") != "PASS" or
            record.verification.get("oracle_complete") is not True):
        raise OrfsInterferenceShadowError(
            f"training ORFS pair is not complete/pass: {lineage}")
    return captured.transition_id


def _parent(lineages: Sequence[str]) -> MechanismKnowledge:
    return MechanismKnowledge(
        knowledge_id="r3-orfs-density-relief", version=1,
        mechanism_family="ORFS_DENSITY_RELIEF",
        compatibility_profile="orfs.flow.config.v1",
        antecedent={"failure": "route_not_observed"},
        intervention={"family": "DENSITY_RELIEF"},
        mediated_effects=({"effect": "route_complete"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "ORFS_DENSITY_RELIEF",
            "compatibility_profile": "orfs.flow.config.v1",
            "platform": "sky130hs",
        },),
        negative_applicability=(),
        preserved_obligations=("ORFS_ROUTE_PASS", "ORFS_SIGNOFF_PASS"),
        known_failure_modes=("high_utilization_negative_transfer",),
        causal_path_ids=("r3-orfs-density-relief-path",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=tuple(lineages), status="shadow")


def _post_route(case_id: str, candidate: challenge.StructuredRepairCandidate) -> MemoryRoutingDecision:
    return MemoryRoutingDecision(
        decision="INAPPLICABLE", resolved_state_id=candidate.resolved_state_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={"status": "VETOED", "hard_gate": "negative_applicability",
                       "challenge_case": case_id},
        causal_support={"status": "VETOED"},
        risk={"risk_penalty": 1.0, "memory_interference": True},
        abstain_reasons=("negative_applicability",), no_memory_budget=1,
        memory_budget=0)


def _build_anti_forgetting(
        artifacts: Path, post_cohort: OrfsPairedCohortReceipt,
        heldout_result: dict, before_digest: str,
        campaign_id: str) -> tuple[AntiForgettingWitness, dict]:
    evidence_dir = artifacts / "anti_forgetting_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(post_cohort.case_receipts)
    target = post_cohort.case_receipts[ordered[0]].arm_receipts["APPLICABILITY_GATED"]
    non_target = post_cohort.case_receipts[ordered[1]].arm_receipts["CAUSAL_NO_SKILL"]
    target_path = evidence_dir / "target_replay.json"
    non_target_path = evidence_dir / "non_target_regression.json"
    heldout_path = evidence_dir / "heldout_audit.json"
    _write_json(target_path, {"receipt": target.to_dict(), "outcome": target.outcome,
                              "policy_fallback": target.metadata.get("policy_fallback")})
    _write_json(non_target_path, {"receipt": non_target.to_dict(), "outcome": non_target.outcome,
                                  "policy_fallback": non_target.metadata.get("policy_fallback")})
    _write_json(heldout_path, heldout_result)
    rollback_path = evidence_dir / "rollback_verification.json"
    rollback = {
        "pointer": "canonical-before-r3-orfs-interference-shadow",
        "source_db_digest_before": before_digest,
        "source_db_digest_after": before_digest,
        "canonical_memory_mutation": "none", "staging_discarded": True,
        "verified": True,
    }
    _write_json(rollback_path, rollback)
    manifest = {
        "version": "p13-anti-forgetting-manifest-v1", "campaign_id": campaign_id,
        "case_id": ordered[0],
        "target_replay": {"receipt_id": target.execution_digest, "path": str(target_path),
                          "sha256": _file_digest(target_path), "passed": target.outcome == "PASS"},
        "non_target_regression": {"receipt_id": non_target.execution_digest,
                                   "path": str(non_target_path),
                                   "sha256": _file_digest(non_target_path),
                                   "regression_free": non_target.outcome == "PASS"},
        "heldout_audit": {"receipt_id": "heldout:" + _file_digest(heldout_path),
                          "path": str(heldout_path), "sha256": _file_digest(heldout_path),
                          "passed": heldout_result.get("outcome") == "PASS"},
        "rollback": {"receipt_id": "rollback:" + _file_digest(rollback_path),
                      "path": str(rollback_path), "sha256": _file_digest(rollback_path),
                      "pointer": rollback["pointer"], "verified": True},
    }
    manifest_path = evidence_dir / "manifest.json"
    report_path = evidence_dir / "witness_report.json"
    _write_json(manifest_path, manifest)
    report = build_p13_anti_forgetting_witness(manifest_path, output=report_path)
    return AntiForgettingWitness.from_dict(report["witness"]), report


def _heldout_case(base_case: dict, project: Path, *, case_id: str, lineage: str) -> dict:
    design, platform, source_string = challenge._config_values(project)
    source_paths = tuple(Path(item) for item in source_string.split(" "))
    source_inputs = _source_inputs([
        {"path": str(path), "sha256": _file_sha256(path)} for path in source_paths])
    case = dict(base_case)
    case.update({
        "case_id": case_id, "lineage_id": lineage, "project_dir": str(project),
        "platform": platform, "source_inputs": [dict(item) for item in source_inputs],
        "source_digest": _source_binding(project, source_inputs),
    })
    # Keep the external project's parsed identity visible in the audit file;
    # the ORFS oracle itself still receives only the frozen case mapping.
    case["design_name"] = design
    return case


def run(
        *, challenge_artifacts: Path | str = DEFAULT_CHALLENGE,
        training_pairs: Sequence[tuple[Path | str, Path | str]],
        training_lineages: Sequence[str], heldout_project: Path | str,
        heldout_lineage: str, artifacts: Path | str, force: bool = False,
        timeout: int | None = None, heldout_timeout: int | None = None) -> dict:
    if len(training_pairs) < 2:
        raise OrfsInterferenceShadowError("at least two ORFS training pairs are required")
    if len(training_pairs) != len(training_lineages):
        raise OrfsInterferenceShadowError("training lineages must cover every pair")
    if len(set(training_lineages)) != len(training_lineages):
        raise OrfsInterferenceShadowError("training lineages must be distinct")
    artifacts = Path(artifacts).expanduser().resolve()
    if artifacts.exists():
        if not force:
            raise OrfsInterferenceShadowError(
                f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    receipt_dir = artifacts / "receipts"
    receipt_dir.mkdir()
    challenge_root = Path(challenge_artifacts).expanduser().resolve()
    (cases_payload, pre_cohort, routes, candidates, derivations, triggers,
     admissions, reason_receipt) = _load_challenge(challenge_root)
    campaign_id = pre_cohort.campaign_id
    shadow_campaign_id = SHADOW_CAMPAIGN_VERSION

    conn = db.connect(artifacts / "tehm.sqlite")
    db.ensure_schema(conn)
    store = ArtifactStore(artifacts / "training-artifacts")
    training_paths = [(Path(before).expanduser().resolve(), Path(after).expanduser().resolve())
                      for before, after in training_pairs]
    if any(not before.is_dir() or not after.is_dir() for before, after in training_paths):
        raise OrfsInterferenceShadowError("training pair paths must be directories")
    transition_ids = [
        _capture_training(conn, store, before, after, lineage, campaign_id)
        for (before, after), lineage in zip(training_paths, training_lineages)
    ]
    parent = _parent(tuple(training_lineages))
    register_knowledge(conn, parent, evidence_refs=[
        {"evidence_type": "transition", "evidence_id": transition_id,
         "split": "training", "lineage_id": lineage,
         "evidence_level": parent.evidence_level}
        for transition_id, lineage in zip(transition_ids, training_lineages)
    ])
    before_counts = _table_counts(conn)
    before_db_digest = _connection_digest(conn)

    ordered = sorted(pre_cohort.case_receipts)
    observations = [(derivations[case_id][0], pre_cohort.case_receipts[case_id])
                    for case_id in ordered]
    negative_context = ({
        "mechanism_family": parent.mechanism_family,
        "compatibility_profile": parent.compatibility_profile,
        "platform": "sky130hs", "core_utilization": "99",
        "interference_signature": "forced_memory_high_utilization",
    },)
    proposal = propose_memory_interference_specialization(
        observations, knowledge_object_id=parent.object_id,
        transition_ids=transition_ids, negative_applicability=negative_context,
        trigger_receipt_ids=tuple(triggers[case_id].receipt_digest for case_id in ordered),
        evidence_refs=tuple(sorted({
            ref for derivation, paired in observations
            for ref in (derivation.receipt_id, derivation.receipt_digest,
                        paired.receipt_digest, *derivation.input_receipt_ids,
                        *derivation.input_digests)
        } | set(transition_ids)))
    )
    plan = interference_proposal_to_localized_plan(proposal)
    child = MechanismKnowledge(
        knowledge_id="r3-orfs-density-relief-specialized", version=1,
        mechanism_family=parent.mechanism_family,
        compatibility_profile=parent.compatibility_profile,
        antecedent=dict(parent.antecedent), intervention=dict(parent.intervention),
        mediated_effects=parent.mediated_effects, expected_outcome=dict(parent.expected_outcome),
        positive_applicability=parent.positive_applicability,
        negative_applicability=negative_context,
        preserved_obligations=parent.preserved_obligations,
        known_failure_modes=tuple(sorted({*parent.known_failure_modes,
                                           "memory_interference:forced_high_utilization"})),
        causal_path_ids=parent.causal_path_ids,
        evidence_level=parent.evidence_level, support_lineages=parent.support_lineages,
        status="shadow")

    # Execute the post-revision policy semantics with a fresh typed ORFS
    # receipt.  The dangerous ALWAYS_MEMORY arm remains a counterfactual; both
    # gated arms are explicitly passed None and therefore execute no-memory.
    post_cases = []
    post_routes: dict[str, MemoryRoutingDecision] = {}
    post_candidates: dict[str, dict] = {}
    for case in cases_payload["cases"]:
        case_id = case["case_id"]
        route = _post_route(case_id, candidates[case_id])
        frozen = dict(case)
        frozen.update({"routing_receipt_id": route.routing_receipt_id,
                       "routing_decision": route.decision})
        post_cases.append(frozen)
        post_routes[case_id] = route
        post_candidates[case_id] = {
            "NO_MEMORY": None, "ALWAYS_MEMORY": candidates[case_id],
            "APPLICABILITY_GATED": None, "CAUSAL_NO_SKILL": None,
        }

    # Freeze every input that controls the post-revision cohort before any
    # expensive ORFS execution.  The old producer wrote a minimal manifest
    # only after the cohort completed, which made an interrupted run unable to
    # prove what it had attempted and also left the manifest campaign ID
    # different from the cohort campaign ID.  This payload is immutable and
    # content-addressed; later anti-forgetting evidence is intentionally not
    # part of the execution manifest.
    shadow_manifest_payload = {
        "version": SHADOW_CAMPAIGN_VERSION,
        "campaign_id": shadow_campaign_id,
        "source_campaign_id": campaign_id,
        "lane": "EVOLUTION_CHALLENGE", "reason": "MEMORY_INTERFERENCE",
        "backend": "external_orfs",
        "pre_challenge_cohort_digest": pre_cohort.receipt_digest,
        "pre_reason_receipt_digest": reason_receipt.receipt_digest,
        "pre_trigger_receipt_digests": sorted(
            item.receipt_digest for item in triggers.values()),
        "training_transition_ids": list(transition_ids),
        "training_lineages": list(training_lineages),
        "parent_knowledge": parent.to_dict(),
        "post_cases": post_cases,
        "post_routes": {
            case_id: {**route.to_dict(),
                      "routing_receipt_id": route.routing_receipt_id,
                      "decision_digest": route.decision_digest}
            for case_id, route in sorted(post_routes.items())
        },
        "post_candidate_payloads": {
            case_id: {
                arm: (candidate.to_dict() if candidate is not None else None)
                for arm, candidate in sorted(arms.items())
            }
            for case_id, arms in sorted(post_candidates.items())
        },
        "proposal": {**proposal.to_dict(), "proposal_digest": proposal.proposal_digest},
        "localized_update_plan": {**plan.to_dict(), "plan_digest": plan.plan_digest},
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted", "memory_docs_submitted": False,
    }
    shadow_manifest_digest = _digest(shadow_manifest_payload)
    _write_json(receipt_dir / "campaign_manifest.json", shadow_manifest_payload)

    locks = artifacts / "locks"
    locks.mkdir()
    if timeout is not None:
        if type(timeout) is not int or timeout < 1:
            raise OrfsInterferenceShadowError("timeout must be positive")
        # run_orfs.sh reads this process environment for every post-revision
        # arm.  Keep the default untouched unless the caller explicitly opts
        # into a bounded experiment.
        os.environ["ORFS_TIMEOUT"] = str(timeout)
    if heldout_timeout is not None:
        if type(heldout_timeout) is not int or heldout_timeout < 1:
            raise OrfsInterferenceShadowError("heldout_timeout must be positive")
    try:
        post_cohort = execute_orfs_paired_cohort(
            post_cases, post_candidates, campaign_id=shadow_campaign_id,
            campaign_manifest_digest=shadow_manifest_digest,
            platform_digest=pre_cohort.platform_digest, pdk_digest=pre_cohort.pdk_digest,
            oracle=OrfsCandidateOracle(environment={"R2G_LOCK_DIR": str(locks)}),
            budget=3, toolchain_digest=pre_cohort.toolchain_digest,
            oracle_digest=pre_cohort.oracle_digest, min_lineages=2)
    except KeyboardInterrupt:
        failure = {
            "version": SHADOW_CAMPAIGN_VERSION,
            "campaign_id": shadow_campaign_id,
            "source_campaign_id": campaign_id,
            "lane": "EVOLUTION_CHALLENGE", "reason": "MEMORY_INTERFERENCE",
            "status": "EXECUTION_INTERRUPTED", "error_type": "KeyboardInterrupt",
            "error": "operator interrupted the ORFS shadow cohort",
            "campaign_manifest_digest": shadow_manifest_digest,
            "cohort_available": False, "evaluation_only": True,
            "canonical_memory_mutation": "none", "production_authority_changed": False,
            "production_runtime_imported": False,
            "production_integration": "not_attempted", "memory_docs_submitted": False,
        }
        _write_json(artifacts / "failure.json", failure)
        raise
    except Exception as exc:
        failure = {
            "version": SHADOW_CAMPAIGN_VERSION,
            "campaign_id": shadow_campaign_id,
            "source_campaign_id": campaign_id,
            "lane": "EVOLUTION_CHALLENGE", "reason": "MEMORY_INTERFERENCE",
            "status": "EXECUTION_FAILED", "error_type": type(exc).__name__,
            "error": str(exc), "campaign_manifest_digest": shadow_manifest_digest,
            "cohort_available": False, "evaluation_only": True,
            "canonical_memory_mutation": "none", "production_authority_changed": False,
            "production_runtime_imported": False,
            "production_integration": "not_attempted", "memory_docs_submitted": False,
        }
        _write_json(artifacts / "failure.json", failure)
        raise
    for case_id, paired in post_cohort.case_receipts.items():
        if paired.arm_receipts["NO_MEMORY"].outcome != "PASS":
            raise OrfsInterferenceShadowError(f"post-revision baseline failed: {case_id}")
        if paired.arm_receipts["APPLICABILITY_GATED"].source != "no_memory":
            raise OrfsInterferenceShadowError(f"applicability gate did not fallback: {case_id}")
        if paired.arm_receipts["CAUSAL_NO_SKILL"].source != "no_memory":
            raise OrfsInterferenceShadowError(f"causal no-skill did not fallback: {case_id}")
        if paired.arm_receipts["APPLICABILITY_GATED"].outcome != "PASS" or \
                paired.arm_receipts["CAUSAL_NO_SKILL"].outcome != "PASS":
            raise OrfsInterferenceShadowError(f"post-revision fallback failed: {case_id}")

    heldout = Path(heldout_project).expanduser().resolve()
    if not heldout.is_dir():
        raise OrfsInterferenceShadowError(f"heldout_project is not a directory: {heldout}")
    heldout_case = _heldout_case(
        cases_payload["cases"][0], heldout,
        case_id=f"{SHADOW_CAMPAIGN_VERSION}:heldout", lineage=heldout_lineage)
    heldout_env = {"R2G_LOCK_DIR": str(locks)}
    if heldout_timeout is not None:
        heldout_env["ORFS_TIMEOUT"] = str(heldout_timeout)
    heldout_result = execute_orfs_candidate(
        None, heldout_case, 3, environment=heldout_env)
    if heldout_result.get("outcome") != "PASS":
        raise OrfsInterferenceShadowError("held-out ORFS baseline did not PASS")
    witness, witness_report = _build_anti_forgetting(
        artifacts, post_cohort, heldout_result, before_db_digest, campaign_id)
    plan = type(plan)(**{**plan.__dict__, "evidence_refs": tuple(sorted({
        *plan.evidence_refs, reason_receipt.receipt_digest, witness.receipt_digest,
    }))})
    first_case = ordered[0]
    evidence = {
        "transition_ids": transition_ids,
        "parent_object_ids": [parent.object_id], "knowledge": child.to_dict(),
        "knowledge_evidence_refs": [
            {"evidence_type": "transition", "evidence_id": transition_id,
             "split": "training", "lineage_id": lineage,
             "evidence_level": child.evidence_level}
            for transition_id, lineage in zip(transition_ids, training_lineages)
        ],
        "provenance": {"source": "r3-orfs-interference-shadow",
                       "reason_receipt": reason_receipt.receipt_digest,
                       "proposal": proposal.proposal_digest},
        "p12_shadow_trigger": {**triggers[first_case].to_dict(),
                                "receipt_digest": triggers[first_case].receipt_digest},
        "anti_forgetting": {**witness.to_dict(), "receipt_digest": witness.receipt_digest},
        "scope": {"target_scope": "global", "challenge_case": first_case},
    }
    shadow_receipt = apply_localized_update_shadow(plan, conn, evidence)
    after_counts = _table_counts(conn)
    after_db_digest = _connection_digest(conn)
    if before_counts != after_counts or before_db_digest != after_db_digest:
        raise OrfsInterferenceShadowError("P13 shadow update changed source DB")
    if not shadow_receipt.created_object_ids or not shadow_receipt.created_relation_ids:
        raise OrfsInterferenceShadowError("ORFS shadow update created no typed child/relation")
    memory_delta = memory_delta_from_shadow_update(shadow_receipt)
    if not memory_delta.eligible:
        raise OrfsInterferenceShadowError("ORFS shadow update did not produce an eligible C1 delta")

    _write_json(receipt_dir / "training.json", {
        "transition_ids": transition_ids, "lineages": list(training_lineages),
        "knowledge": parent.to_dict(), "knowledge_object_id": parent.object_id,
    })
    _write_json(receipt_dir / "pre_challenge_cohort.json",
                {**pre_cohort.to_dict(), "receipt_digest": pre_cohort.receipt_digest})
    _write_json(receipt_dir / "post_revision_cohort.json",
                {**post_cohort.to_dict(), "receipt_digest": post_cohort.receipt_digest})
    _write_json(receipt_dir / "reason_derivation.json", {
        "derivations": {case_id: [{**item.to_dict(), "receipt_id": item.receipt_id,
                                    "receipt_digest": item.receipt_digest}
                                   for item in values]
                        for case_id, values in derivations.items()}
    })
    _write_json(receipt_dir / "p13_reason_receipt.json",
                {**reason_receipt.to_dict(), "receipt_digest": reason_receipt.receipt_digest})
    _write_json(receipt_dir / "p12_triggers.json", {
        "triggers": [{**item.to_dict(), "receipt_digest": item.receipt_digest}
                     for item in triggers.values()]
    })
    _write_json(receipt_dir / "admissions.json", {
        "admissions": {case_id: {**item.to_dict(), "receipt_id": item.receipt_id,
                                  "receipt_digest": item.receipt_digest}
                       for case_id, item in admissions.items()}
    })
    _write_json(receipt_dir / "proposal.json", {**proposal.to_dict(),
                                                  "proposal_id": proposal.proposal_id,
                                                  "proposal_digest": proposal.proposal_digest})
    _write_json(receipt_dir / "localized_update_plan.json",
                {**plan.to_dict(), "plan_digest": plan.plan_digest})
    _write_json(receipt_dir / "anti_forgetting.json", witness_report)
    _write_json(receipt_dir / "shadow_update.json",
                {**shadow_receipt.to_dict(), "receipt_digest": shadow_receipt.receipt_digest})
    _write_json(receipt_dir / "memory_delta.json",
                {**memory_delta.to_dict(), "receipt_digest": memory_delta.receipt_digest})
    _write_json(receipt_dir / "post_routes.json", {
        case_id: {**route.to_dict(), "routing_receipt_id": route.routing_receipt_id,
                  "decision_digest": route.decision_digest}
        for case_id, route in sorted(post_routes.items())
    })
    _write_json(receipt_dir / "heldout.json", heldout_result)

    summary = {
        "version": SHADOW_CAMPAIGN_VERSION, "campaign_id": campaign_id,
        "shadow_campaign_id": shadow_campaign_id,
        "campaign_manifest_digest": shadow_manifest_digest,
        "post_cohort_receipt_digest": post_cohort.receipt_digest,
        "lane": "EVOLUTION_CHALLENGE", "backend": "external_orfs",
        "reason": "MEMORY_INTERFERENCE", "training_lineage_count": len(training_lineages),
        "challenge_case_count": len(pre_cohort.case_receipts),
        "pre_outcomes": pre_cohort.outcome_counts, "post_outcomes": post_cohort.outcome_counts,
        "fallback_sources": {
            case_id: {arm: paired.arm_receipts[arm].source
                      for arm in ("APPLICABILITY_GATED", "CAUSAL_NO_SKILL")}
            for case_id, paired in sorted(post_cohort.case_receipts.items())
        },
        "proposal_operation": proposal.operation, "shadow_operation": shadow_receipt.operation,
        "shadow_created_objects": list(shadow_receipt.created_object_ids),
        "shadow_created_relations": list(shadow_receipt.created_relation_ids),
        "memory_delta_eligible": memory_delta.eligible,
        "anti_forgetting_eligible": witness.eligible,
        "canonical_counts_unchanged": before_counts == after_counts,
        "source_db_unchanged": before_db_digest == after_db_digest,
        "canonical_memory_mutation": shadow_receipt.canonical_memory_mutation,
        "production_authority_changed": shadow_receipt.production_authority_changed,
        "production_runtime_imported": shadow_receipt.production_runtime_imported,
        "staging_discarded": shadow_receipt.staging_discarded,
        "evaluation_only": True, "memory_docs_submitted": False,
    }
    # Publish completion evidence only after the campaign database has been
    # checkpointed successfully.  A failed checkpoint must not leave behind a
    # seemingly complete summary without its frozen sidecar.
    db.checkpoint_and_close(conn)
    _write_json(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--challenge-artifacts", type=Path, default=DEFAULT_CHALLENGE)
    parser.add_argument("--training-pair", action="append", nargs=2, required=True,
                        metavar=("BEFORE", "AFTER"))
    parser.add_argument("--training-lineage", action="append", required=True)
    parser.add_argument("--heldout-project", type=Path, required=True)
    parser.add_argument("--heldout-lineage", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--timeout", type=int,
                        help="ORFS wall-clock timeout for every post-revision arm")
    parser.add_argument("--heldout-timeout", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(
            challenge_artifacts=args.challenge_artifacts,
            training_pairs=[tuple(pair) for pair in args.training_pair],
            training_lineages=args.training_lineage,
            heldout_project=args.heldout_project, heldout_lineage=args.heldout_lineage,
            artifacts=args.artifacts, force=args.force,
            timeout=args.timeout,
            heldout_timeout=args.heldout_timeout)
    except (OSError, OrfsInterferenceShadowError, TypeError, ValueError, sqlite3.Error) as exc:
        print(f"ORFS shadow campaign failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
