"""Fail-closed replay for the Revision3 ORFS P14 attribution projection.

The P14 producer consumes an immutable P13 interference shadow and writes a
disposable projection database.  This module replays the boundary and typed
chain without running ORFS or opening the P13 database for writes.  It also
recomputes the database-bound capability attribution so a report cannot be
accepted merely because its JSON says that a gate passed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from contracts import MemoryRoutingDecision
from tehm import db
from tehm.capability import (
    evaluate_capability_attribution_from_db, load_policy_snapshot,
    validate_policy_load_row, validate_policy_snapshot_row,
)
from tehm.capability.delta import MemoryDeltaReceipt, memory_delta_from_shadow_update
from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt
from tehm.evolution.apply_update import AppliedShadowUpdateReceipt
from tehm.evolution.interference_revision import MemoryInterferenceEvolutionProposal
from tehm.evolution.local_revision import LocalizedUpdatePlan
from tehm.evolution.p12_shadow_trigger import (
    P12ShadowUpdateTriggerReceipt, P13EvolutionReasonReceipt,
)
from tehm.evolution.admission import EvolutionAdmissionReceipt
from tehm.evolution.reason_derivation import EvolutionReasonDerivationReceipt
from tehm.ids import stable_dumps
from tehm.knowledge import MechanismKnowledge
from tehm.state import verify_resolution_snapshot

from scripts import run_r3_orfs_interference_attribution as attribution
from scripts import run_r3_orfs_interference_shadow as shadow
from tehm.evaluation.orfs_interference_shadow_replay import replay as replay_shadow


VERSION = "tehm-r3-orfs-interference-p14-v0.1"


class OrfsInterferenceAttributionReplayError(ValueError):
    """A P14 attribution artifact is malformed, stale, or crosses a boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    if not path.is_file():
        raise OrfsInterferenceAttributionReplayError(f"{name} is missing: {path}")
    try:
        value = json.loads(path.read_text())
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise OrfsInterferenceAttributionReplayError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise OrfsInterferenceAttributionReplayError(f"{name} must be an object: {path}")
    return dict(value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OrfsInterferenceAttributionReplayError(f"{name} is required")
    return value.strip()


def _boundary(payload: Mapping, *, name: str) -> None:
    if payload.get("evaluation_only") is not True:
        raise OrfsInterferenceAttributionReplayError(f"{name} is not evaluation-only")
    if payload.get("canonical_memory_mutation") != "none":
        raise OrfsInterferenceAttributionReplayError(f"{name} crosses canonical-memory boundary")
    if payload.get("memory_docs_submitted") is not False:
        raise OrfsInterferenceAttributionReplayError(f"{name} crosses memory/docs boundary")
    if payload.get("production_authority_changed") is not False:
        raise OrfsInterferenceAttributionReplayError(f"{name} crosses production authority boundary")
    if payload.get("production_runtime_imported") is not False:
        raise OrfsInterferenceAttributionReplayError(f"{name} crosses production-runtime boundary")


def _typed(path: Path, cls, name: str):
    payload = _load(path, name)
    try:
        value = cls.from_dict(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceAttributionReplayError(f"{name} is invalid") from exc
    supplied = payload.get("receipt_digest")
    if supplied is not None and supplied != getattr(value, "receipt_digest", None):
        raise OrfsInterferenceAttributionReplayError(f"{name} digest mismatch")
    return payload, value


def _policy_row(conn, payload: Mapping, *, name: str) -> dict:
    policy_id = _text(payload.get("policy_snapshot_id"), f"{name}.policy_snapshot_id")
    try:
        row = load_policy_snapshot(conn, policy_id)
    except (KeyError, ValueError, sqlite3.Error) as exc:
        raise OrfsInterferenceAttributionReplayError(f"{name} is not loadable") from exc
    expected = dict(payload)
    if any(row.get(key) != expected.get(key) for key in
           ("policy_snapshot_id", "memory_snapshot_id", "policy_digest")):
        raise OrfsInterferenceAttributionReplayError(f"{name} content drifted")
    return row


def _policy_load(conn, payload: Mapping, *, name: str) -> dict:
    receipt_id = _text(payload.get("receipt_id"), f"{name}.receipt_id")
    row = conn.execute(
        "SELECT * FROM tehm_policy_load_receipts WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone()
    if row is None:
        raise OrfsInterferenceAttributionReplayError(f"{name} is missing from projection DB")
    try:
        checked = validate_policy_load_row(row)
    except ValueError as exc:
        raise OrfsInterferenceAttributionReplayError(f"{name} is invalid") from exc
    for key in ("receipt_id", "policy_snapshot_id", "runtime_id", "loaded", "receipt_digest"):
        if checked.get(key) != payload.get(key):
            raise OrfsInterferenceAttributionReplayError(f"{name} content drifted")
    return checked


def _read_p13(shadow_root: Path, challenge_root: Path):
    receipts = shadow_root / "receipts"
    shadow_replay = replay_shadow(shadow_root)
    if shadow_replay.get("terminal_status") != "COMPLETE":
        raise OrfsInterferenceAttributionReplayError("P13 shadow is not complete")
    try:
        _cases, pre_cohort, pre_routes, _candidates, _derivations, _triggers, _admissions, reason = shadow._load_challenge(challenge_root)
    except Exception as exc:
        raise OrfsInterferenceAttributionReplayError("P13 challenge cannot be loaded") from exc
    post_payload, post_cohort = _typed(
        receipts / "post_revision_cohort.json", OrfsPairedCohortReceipt,
        "P13 post-revision cohort")
    shadow_payload, shadow_update = _typed(
        receipts / "shadow_update.json", AppliedShadowUpdateReceipt, "P13 shadow update")
    delta_payload, delta = _typed(
        receipts / "memory_delta.json", MemoryDeltaReceipt, "P13 memory delta")
    reason_payload, reason_obj = _typed(
        receipts / "p13_reason_receipt.json", P13EvolutionReasonReceipt,
        "P13 reason receipt")
    proposal_payload, proposal = _typed(
        receipts / "proposal.json", MemoryInterferenceEvolutionProposal,
        "P13 proposal")
    plan_payload, plan = _typed(
        receipts / "localized_update_plan.json", LocalizedUpdatePlan,
        "P13 localized update plan")
    raw_triggers = _load(receipts / "p12_triggers.json", "P13 triggers").get("triggers")
    raw_admissions = _load(receipts / "admissions.json", "P13 admissions").get("admissions")
    if not isinstance(raw_triggers, list) or not isinstance(raw_admissions, Mapping):
        raise OrfsInterferenceAttributionReplayError("P13 trigger/admission receipts are malformed")
    triggers = []
    for raw in raw_triggers:
        try:
            item = P12ShadowUpdateTriggerReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceAttributionReplayError("P13 trigger is invalid") from exc
        if raw.get("receipt_digest") != item.receipt_digest:
            raise OrfsInterferenceAttributionReplayError("P13 trigger digest mismatch")
        triggers.append(item)
    admissions = []
    for raw in raw_admissions.values():
        try:
            item = EvolutionAdmissionReceipt.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise OrfsInterferenceAttributionReplayError("P13 admission is invalid") from exc
        if raw.get("receipt_digest") != item.receipt_digest:
            raise OrfsInterferenceAttributionReplayError("P13 admission digest mismatch")
        admissions.append(item)
    training = _load(receipts / "training.json", "P13 training")
    try:
        parent = MechanismKnowledge.from_dict(training["knowledge"])
    except (TypeError, ValueError, KeyError) as exc:
        raise OrfsInterferenceAttributionReplayError("P13 parent knowledge is invalid") from exc
    if (shadow_update.campaign_id != pre_cohort.campaign_id or
            any(tuple(values) != ("MEMORY_INTERFERENCE",)
                for values in reason_obj.evolution_reasons.values()) or
            proposal.operation != "SPECIALIZE" or plan.operation != "SPECIALIZE" or
            not delta.eligible or not triggers or not admissions or
            any(not item.admitted for item in admissions) or
            set(pre_cohort.case_receipts) != set(post_cohort.case_receipts)):
        raise OrfsInterferenceAttributionReplayError("P13 chain is incomplete or inconsistent")
    expected_delta = memory_delta_from_shadow_update(shadow_update)
    if delta.receipt_digest != expected_delta.receipt_digest:
        raise OrfsInterferenceAttributionReplayError("P13 memory delta derivation drifted")
    return {
        "cases": _cases["cases"], "candidates": _candidates,
        "shadow_replay": shadow_replay, "pre_cohort": pre_cohort,
        "post_cohort": post_cohort, "pre_routes": pre_routes, "reason": reason,
        "parent": parent, "training": training, "shadow_payload": shadow_payload,
        "shadow": shadow_update, "delta_payload": delta_payload, "delta": delta,
        "reason_payload": reason_payload, "reason_obj": reason_obj,
        "proposal_payload": proposal_payload, "proposal": proposal,
        "plan_payload": plan_payload, "plan": plan, "triggers": triggers,
        "admissions": admissions,
    }


def _verify_safety_ablation(path: Path, pre, post, *, chain: Mapping) -> None:
    payload = _load(path, "P14 safety ablation")
    _boundary(payload, name="P14 safety ablation")
    if (payload.get("version") != "tehm-r3-orfs-interference-safety-ablation-v0.1" or
            payload.get("comparison") != "harmful forced memory vs negative-applicability fallback vs no-memory" or
            payload.get("capability_gain_claimed") is not False or
            payload.get("strategy_safety_gain") is not True):
        raise OrfsInterferenceAttributionReplayError("P14 safety ablation claims drifted")
    rows = payload.get("cases")
    if not isinstance(rows, list) or set(row.get("case_id") for row in rows) != set(pre.case_receipts):
        raise OrfsInterferenceAttributionReplayError("P14 safety ablation case coverage is invalid")
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str):
            raise OrfsInterferenceAttributionReplayError("P14 safety ablation case ID is invalid")
        expected = {
            "M_t_forced_memory": pre.case_receipts[case_id].arm_receipts["ALWAYS_MEMORY"].to_dict(),
            "M_t+1_negative_applicability": post.case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].to_dict(),
            "M_t+1_minus_delta_M_no_memory": pre.case_receipts[case_id].arm_receipts["NO_MEMORY"].to_dict(),
        }
        for key, value in expected.items():
            stored = row.get(key)
            if not isinstance(stored, Mapping) or any(stored.get(k) != v for k, v in value.items()):
                raise OrfsInterferenceAttributionReplayError(
                    f"P14 safety ablation receipt drifted for {case_id}/{key}")
    if _digest(payload) != chain.get("safety_ablation", {}).get("digest"):
        raise OrfsInterferenceAttributionReplayError("P14 safety ablation digest drifted")


def replay(artifacts: Path | str, *, shadow_artifacts: Path | str,
           challenge_artifacts: Path | str) -> dict:
    """Replay one ORFS P14 attribution artifact without mutating evidence."""
    root = Path(artifacts).expanduser().resolve()
    shadow_root = Path(shadow_artifacts).expanduser().resolve()
    challenge_root = Path(challenge_artifacts).expanduser().resolve()
    if not root.is_dir():
        raise OrfsInterferenceAttributionReplayError(f"artifacts is not a directory: {root}")
    report = _load(root / "p14_strategy_attribution.json", "P14 strategy attribution")
    cap_payload = _load(root / "p14_capability_attribution.json", "P14 capability attribution")
    summary = _load(root / "summary.json", "P14 summary")
    _boundary(report, name="P14 strategy attribution")
    _boundary(summary, name="P14 summary")
    recorded_router_audit = _load(root / "router_replay.json", "P14 actual-router replay")
    if (report.get("version") != VERSION or report.get("reason") != "MEMORY_INTERFERENCE" or
            report.get("scope") != "L2_STRATEGY_EVOLUTION" or
            report.get("capability_attribution") != cap_payload):
        raise OrfsInterferenceAttributionReplayError("P14 report identity or attribution copy is invalid")
    p13 = _read_p13(shadow_root, challenge_root)
    chain = report.get("p14_chain")
    source = report.get("source_integrity")
    if not isinstance(chain, Mapping) or not isinstance(source, Mapping):
        raise OrfsInterferenceAttributionReplayError("P14 chain/source integrity is missing")
    shadow_campaign = p13["shadow_replay"]["campaign_id"]
    if report.get("campaign_id") != shadow_campaign:
        raise OrfsInterferenceAttributionReplayError("P14 campaign is not bound to P13 shadow")
    expected_chain = {
        "reason_receipt_digest": p13["reason_obj"].receipt_digest,
        "proposal_digest": p13["proposal"].proposal_digest,
        "localized_update_plan_digest": p13["plan"].plan_digest,
        "shadow_update_receipt_digest": p13["shadow"].receipt_digest,
        "memory_delta_receipt_digest": p13["delta"].receipt_digest,
        "p12_trigger_receipt_digests": sorted(item.receipt_digest for item in p13["triggers"]),
        "admission_receipt_digests": sorted(item.receipt_digest for item in p13["admissions"]),
    }
    for key, expected in expected_chain.items():
        if chain.get(key) != expected:
            raise OrfsInterferenceAttributionReplayError(f"P14 chain field drifted: {key}")
    source_db = Path(_text(source.get("source_db"), "source_db"))
    projection_db = Path(_text(source.get("projection_db"), "projection_db"))
    if source_db != (shadow_root / "tehm.sqlite").resolve() or projection_db != (root / "p14-attribution.sqlite").resolve():
        raise OrfsInterferenceAttributionReplayError("P14 source/projection paths are not bound")
    if not projection_db.is_file() or projection_db.with_name(projection_db.name + "-wal").exists() or projection_db.with_name(projection_db.name + "-shm").exists():
        raise OrfsInterferenceAttributionReplayError("P14 projection database is incomplete")
    source_conn = db.connect_read_only(source_db)
    projection_conn = db.connect_read_only(projection_db)
    try:
        source_digest = shadow._connection_digest(source_conn)
        projection_digest = shadow._connection_digest(projection_conn)
        if (source_digest != source.get("source_digest_before") or
                source.get("source_digest_before") != source.get("source_digest_after") or
                source.get("unchanged") is not True or
                source.get("p13_candidate_memory_digest") != p13["delta"].candidate_memory_digest or
                projection_digest != source.get("projection_memory_digest")):
            raise OrfsInterferenceAttributionReplayError("P14 database integrity receipt drifted")
        baseline_policy = _policy_row(projection_conn, chain.get("baseline_policy_snapshot", {}), name="baseline policy")
        candidate_policy = _policy_row(projection_conn, chain.get("candidate_policy_snapshot", {}), name="candidate policy")
        _policy_load(projection_conn, chain.get("baseline_policy_load", {}), name="baseline policy load")
        _policy_load(projection_conn, chain.get("candidate_policy_load", {}), name="candidate policy load")
        if (baseline_policy["memory_snapshot_id"] != p13["delta"].baseline_memory_digest or
                candidate_policy["memory_snapshot_id"] != p13["delta"].candidate_memory_digest):
            raise OrfsInterferenceAttributionReplayError("P14 policy/memory binding drifted")

        parent = p13["parent"]
        child = attribution._child(parent)
        post_resolution_id = _text(chain.get("candidate_resolution_id"), "candidate_resolution_id")
        recorded_post_routes = {
            case_id: MemoryRoutingDecision.from_dict(payload)
            for case_id, payload in _load(
                shadow_root / "receipts/post_routes.json", "P13 post routes").items()}
        router_audit = {
            "baseline": attribution.audit_router_outputs(
                source_conn, p13["cases"], p13["candidates"], parent, p13["pre_routes"]),
            "candidate": attribution.audit_router_outputs(
                projection_conn, p13["cases"], p13["candidates"], parent, recorded_post_routes),
        }
        if (not all(item["eligible"] for item in router_audit.values()) or
                router_audit != recorded_router_audit or
                chain.get("router_replay_digest") != _digest(router_audit)):
            raise OrfsInterferenceAttributionReplayError("P14 actual-router replay mismatch")
        post_routes = {
            case_id: MemoryRoutingDecision.from_dict(row["actual"])
            for case_id, row in router_audit["candidate"]["cases"].items()}
        baseline_behavior = attribution._behavior_digest(
            p13["pre_routes"], p13["pre_cohort"].case_receipts, "NO_MEMORY")
        candidate_behavior = attribution._behavior_digest(
            post_routes, p13["post_cohort"].case_receipts, "APPLICABILITY_GATED")
        detail = cap_payload.get("detail")
        if not isinstance(detail, Mapping):
            raise OrfsInterferenceAttributionReplayError("P14 capability detail is missing")
        memory_delta = {"version": "memory-delta-v1",
                        "baseline_memory_digest": p13["delta"].baseline_memory_digest,
                        "candidate_memory_digest": p13["delta"].candidate_memory_digest,
                        **p13["delta"].delta}
        recomputed = evaluate_capability_attribution_from_db(
            projection_conn, capability_id=cap_payload.get("capability_id"),
            baseline_memory_digest=detail["baseline"]["memory_digest"],
            candidate_memory_digest=detail["candidate"]["memory_digest"],
            baseline_policy_snapshot_id=chain["baseline_policy_snapshot"]["policy_snapshot_id"],
            candidate_policy_snapshot_id=chain["candidate_policy_snapshot"]["policy_snapshot_id"],
            runtime_id=detail["runtime_behavior_binding"]["runtime_id"],
            baseline_behavior_digest=baseline_behavior,
            candidate_behavior_digest=candidate_behavior,
            target_gain=detail["candidate"]["target_gain"],
            no_regression=detail["candidate"]["no_regression"],
            heldout=detail["heldout"], ablation=detail["ablation"],
            memory_delta=memory_delta, shadow_update_receipt=p13["shadow"],
            strict_memory_delta=True)
        if recomputed.to_dict() != cap_payload:
            raise OrfsInterferenceAttributionReplayError("P14 capability attribution replay drifted")
        if chain.get("baseline_resolution_id") == post_resolution_id:
            raise OrfsInterferenceAttributionReplayError("P14 did not change resolved state")
        checked_state = verify_resolution_snapshot(projection_conn, post_resolution_id)
        if checked_state.resolution_id != post_resolution_id:
            raise OrfsInterferenceAttributionReplayError("P14 candidate state is not loadable")
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        if isinstance(exc, OrfsInterferenceAttributionReplayError):
            raise
        raise OrfsInterferenceAttributionReplayError("P14 projection replay failed") from exc
    finally:
        projection_conn.close()
        source_conn.close()
    _verify_safety_ablation(root / "p14_safety_ablation.json",
                            p13["pre_cohort"], p13["post_cohort"], chain=chain)
    gates = report.get("strategy_gates")
    expected_gates = {
        "C1_memory_delta_bound": True,
        "C2_specialized_knowledge_and_relation": bool(
            f"knowledge:{child.object_id}" in p13["delta"].changed_ids and
            p13["shadow"].created_relation_ids),
        "C3_shadow_state_loadable": True,
        "C4_route_changed_to_veto": all(
            p13["pre_routes"][case_id].decision == "CONSIDER" and
            post_routes[case_id].decision == "INAPPLICABLE" and
            p13["pre_routes"][case_id].routing_receipt_id != post_routes[case_id].routing_receipt_id
            for case_id in post_routes),
        "C5_fallback_changed_and_executed": all(
            p13["post_cohort"].case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].source == "no_memory" and
            p13["post_cohort"].case_receipts[case_id].arm_receipts["APPLICABILITY_GATED"].outcome == "PASS" and
            p13["pre_cohort"].case_receipts[case_id].arm_receipts["ALWAYS_MEMORY"].outcome in {"FAIL", "REGRESSION"}
            for case_id in post_routes),
    }
    if gates != expected_gates or report.get("strategy_attribution_eligible") is not True:
        raise OrfsInterferenceAttributionReplayError("P14 strategy gates drifted")
    if (summary.get("strategy_attribution_eligible") is not True or
            summary.get("capability_claim_promotable") is not False or
            summary.get("standard_gates") != cap_payload.get("gates") or
            summary.get("standard_missing_gates") != cap_payload.get("missing_gates") or
            summary.get("source_unchanged") is not True or
            summary.get("production_runtime_imported") is not False):
        raise OrfsInterferenceAttributionReplayError("P14 summary disagrees with receipts")
    return {
        "mode": "replay", "status": "REPLAY_PASS", "terminal_status": "COMPLETE",
        "campaign_id": report["campaign_id"],
        "reason": report["reason"], "strategy_attribution_eligible": True,
        "standard_gates": dict(cap_payload["gates"]),
        "standard_missing_gates": list(cap_payload["missing_gates"]),
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "memory_docs_submitted": False, "evaluation_only": True,
    }


__all__ = ["OrfsInterferenceAttributionReplayError", "replay"]
