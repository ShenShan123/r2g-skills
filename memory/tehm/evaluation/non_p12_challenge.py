"""Fail-closed replay for the Revision3 non-P12 challenge reports.

The R3-9 capability-gap and repeated-failure producers intentionally write
their SQLite projections outside the repository.  This module replays those
reports without executing a new action and without opening a lifecycle or
runtime boundary.  A report is accepted only when its source/derived database
digests, typed detector receipt, reason-specific admission, and proposal (if
present) all recompute to the recorded values.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from contracts import MemoryRoutingDecision
from tehm import db
from tehm.assets.receipts import CapabilityGapReceipt
from tehm.assets import detect_capability_gaps
from tehm.evolution.admission import (
    EvolutionAdmissionReceipt, admit_evolution_reason,
)
from tehm.evolution.capability_gap import (
    CapabilityGapEvolutionProposal, propose_capability_gap_expansion,
)
from tehm.evolution.reason_derivation import (
    EvolutionReasonDerivationReceipt, derive_capability_gap_reason,
    derive_repeated_failure_reason,
)
from tehm.evolution.repeated_failure import (
    RepeatedFailureReceipt, detect_repeated_failures,
)


CAPABILITY_GAP_CHALLENGE_VERSION = "r3-capability-gap-challenge-v1"
REPEATED_FAILURE_CHALLENGE_VERSION = "r3-repeated-failure-challenge-v1"
NON_P12_CHALLENGE_REPLAY_VERSION = "r3-non-p12-challenge-replay-v1"


class NonP12ChallengeReplayError(ValueError):
    """A non-P12 challenge report or its immutable evidence is invalid."""


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NonP12ChallengeReplayError(
            f"challenge report is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise NonP12ChallengeReplayError("challenge report must be an object")
    return dict(payload)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise NonP12ChallengeReplayError(f"challenge {name} is required")
    return value.strip()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise NonP12ChallengeReplayError(
            f"challenge evidence is unreadable: {path}") from exc


def _verify_digest(path: Path, expected: object, name: str) -> str:
    actual = _sha256(path)
    # The R3-9 producers predate the repository-wide ``sha256:`` spelling for
    # database file fields.  Accept that legacy spelling and the new one, but
    # never accept a digest-less or arbitrary value.
    if type(expected) is not str or expected not in {actual, "sha256:" + actual}:
        raise NonP12ChallengeReplayError(
            f"challenge {name} digest mismatch: {path}")
    return actual


def _path(report: Mapping, field: str) -> Path:
    return Path(_text(report.get(field), field)).expanduser().resolve()


def _boundary(report: Mapping, *, version: str) -> str:
    if report.get("version") != version:
        raise NonP12ChallengeReplayError("challenge report version mismatch")
    campaign_id = _text(report.get("campaign_id"), "campaign_id")
    if report.get("canonical_memory_mutation") != "none":
        raise NonP12ChallengeReplayError(
            "challenge report crosses canonical-memory boundary")
    runtime = report.get("production_runtime")
    if not isinstance(runtime, Mapping) or \
            runtime.get("promotion_attempted") is not False or \
            runtime.get("production_promotion_eligible") is not False or \
            runtime.get("runtime_authority_changed") is not False:
        raise NonP12ChallengeReplayError(
            "challenge report crosses production-runtime boundary")
    if report.get("memory_docs_submitted") not in (None, False):
        raise NonP12ChallengeReplayError(
            "challenge report crosses memory/docs boundary")
    if report.get("real_oracle") != "icarus/vvp":
        raise NonP12ChallengeReplayError("challenge oracle is not the pinned Icarus/VVP")
    return campaign_id


def _verify_databases(report: Mapping) -> tuple[Path, Path]:
    source = _path(report, "source_db")
    derived = _path(report, "derived_db")
    if source == derived:
        raise NonP12ChallengeReplayError(
            "challenge source and derived databases must be distinct")
    if not source.is_file() or not derived.is_file():
        raise NonP12ChallengeReplayError(
            "challenge source/derived database is missing")
    _verify_digest(source, report.get("source_db_sha256"), "source_db")
    _verify_digest(derived, report.get("derived_db_sha256"), "derived_db")
    for path in (source, derived):
        for suffix in ("-wal", "-shm"):
            if Path(str(path) + suffix).exists():
                raise NonP12ChallengeReplayError(
                    f"challenge database is not a frozen snapshot: {path}{suffix}")
    return source, derived


def _counts(conn: sqlite3.Connection, names: tuple[str, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name in names:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if exists:
            result[name] = db.count_rows(conn, name)
    return result


def _verify_counts(conn: sqlite3.Connection, report: Mapping,
                   names: tuple[str, ...]) -> None:
    recorded = report.get("counts_after")
    if not isinstance(recorded, Mapping):
        raise NonP12ChallengeReplayError("challenge counts_after is missing")
    actual = _counts(conn, names)
    if dict(recorded) != actual:
        raise NonP12ChallengeReplayError(
            "challenge derived database counts disagree with report")


def _capture_transition_ids(report: Mapping) -> set[str]:
    rows = report.get("training_capture")
    if not isinstance(rows, list) or not rows:
        raise NonP12ChallengeReplayError("challenge training_capture is missing")
    result: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping):
            raise NonP12ChallengeReplayError("challenge training capture is malformed")
        capture = item.get("capture", item)
        if not isinstance(capture, Mapping):
            raise NonP12ChallengeReplayError("challenge capture receipt is malformed")
        transition_id = _text(capture.get("transition_id"), "capture.transition_id")
        if transition_id in result:
            raise NonP12ChallengeReplayError("challenge capture transition IDs are duplicated")
        result.add(transition_id)
    return result


def _assert_receipt_identity(payload: Mapping, receipt: object,
                             *, label: str) -> None:
    receipt_id = getattr(receipt, "receipt_id")
    receipt_digest = getattr(receipt, "receipt_digest")
    if payload.get("receipt_id") != receipt_id or payload.get("receipt_digest") != receipt_digest:
        raise NonP12ChallengeReplayError(f"{label} ID/digest mismatch")


def _assert_admission_identity(payload: Mapping,
                               receipt: EvolutionAdmissionReceipt) -> None:
    _assert_receipt_identity(payload, receipt, label="evolution admission")


def _assert_proposal_identity(payload: Mapping,
                              proposal: CapabilityGapEvolutionProposal) -> None:
    if payload.get("proposal_id") != proposal.proposal_id or \
            payload.get("proposal_digest") != proposal.proposal_digest:
        raise NonP12ChallengeReplayError("capability-gap proposal ID/digest mismatch")


def replay_capability_gap_challenge(report_path: Path | str) -> dict:
    """Replay a R3-9 CAPABILITY_GAP report without mutating any database."""
    report_path = Path(report_path).expanduser().resolve()
    report = _load(report_path)
    campaign_id = _boundary(report, version=CAPABILITY_GAP_CHALLENGE_VERSION)
    source, derived = _verify_databases(report)
    source_digest = _sha256(source)
    captured_ids = _capture_transition_ids(report)
    try:
        conn = db.connect_read_only(derived)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise NonP12ChallengeReplayError(
            f"challenge derived database cannot replay: {derived}") from exc
    try:
        _verify_counts(conn, report, (
            "tehm_transitions", "tehm_knowledge", "tehm_relations",
            "tehm_memory_events", "tehm_asset_status", "tehm_rule_status",
        ))
        gaps = detect_capability_gaps(
            conn, campaign_id=campaign_id, min_lineages=2, min_failures=2)
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise NonP12ChallengeReplayError(
            "capability-gap diagnostic cannot replay") from exc
    finally:
        conn.close()

    selected_payload = report.get("selected_gap")
    try:
        selected = CapabilityGapReceipt.from_dict(selected_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise NonP12ChallengeReplayError("selected capability gap is invalid") from exc
    matching = [item for item in gaps
                if item.gap_id == selected.gap_id and
                item.receipt_digest == selected.receipt_digest]
    if len(matching) != 1:
        raise NonP12ChallengeReplayError(
            "selected capability gap does not replay from derived database")
    if not set(selected.evidence_transitions) <= captured_ids:
        raise NonP12ChallengeReplayError(
            "capability-gap evidence is outside training captures")

    try:
        route = MemoryRoutingDecision.from_dict(report.get("route"))
    except (TypeError, ValueError, KeyError) as exc:
        raise NonP12ChallengeReplayError("capability-gap route is invalid") from exc
    route_payload = report["route"]
    if (route.decision != "NO_SKILL" or route.no_skill_reason != "NO_MATCH" or
            route.memory_budget != 0 or route.selected_asset_ids or
            route_payload.get("routing_receipt_id") != route.routing_receipt_id or
            route_payload.get("decision_digest") != route.decision_digest):
        raise NonP12ChallengeReplayError("capability-gap route binding is invalid")

    derivation_payload = report.get("evolution_reason_derivation")
    admission_payload = report.get("evolution_admission")
    proposal_payload = report.get("capability_gap_proposal")
    try:
        derivation = EvolutionReasonDerivationReceipt.from_dict(derivation_payload)
        admission = EvolutionAdmissionReceipt.from_dict(admission_payload)
        proposal = CapabilityGapEvolutionProposal.from_dict(proposal_payload)
        expected_derivation = derive_capability_gap_reason(
            selected, campaign_id=campaign_id, case_id=derivation.case_id,
            min_lineages=2, min_failures=2,
            failure_transition_ids=selected.evidence_transitions, routing=route)
        if expected_derivation is None or \
                expected_derivation.receipt_digest != derivation.receipt_digest:
            raise NonP12ChallengeReplayError(
                "capability-gap derivation does not replay")
        expected_admission = admit_evolution_reason(
            derivation, campaign_id=campaign_id, learner_eligible=True,
            capability_gap=selected,
            failure_transition_ids=selected.evidence_transitions, routing=route)
        expected_proposal = propose_capability_gap_expansion(
            selected, derivation, admission,
            proposal_kind=proposal.proposal_kind,
            failure_transition_ids=selected.evidence_transitions, routing=route)
    except NonP12ChallengeReplayError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise NonP12ChallengeReplayError(
            "capability-gap typed evidence cannot replay") from exc
    _assert_receipt_identity(derivation_payload, derivation,
                             label="evolution derivation")
    _assert_admission_identity(admission_payload, admission)
    _assert_proposal_identity(proposal_payload, proposal)
    if expected_admission != admission or expected_proposal != proposal:
        raise NonP12ChallengeReplayError(
            "capability-gap admission/proposal replay mismatch")
    if (derivation.reason != "CAPABILITY_GAP" or not admission.admitted or
            proposal.operation != "ADD" or
            proposal.production_runtime_eligible is not False):
        raise NonP12ChallengeReplayError(
            "capability-gap report has invalid evolution boundary")
    if _sha256(source) != source_digest:
        raise NonP12ChallengeReplayError("source database changed during replay")
    return {
        "version": NON_P12_CHALLENGE_REPLAY_VERSION,
        "kind": "CAPABILITY_GAP",
        "campaign_id": campaign_id,
        "source_db_sha256": source_digest,
        "selected_gap_id": selected.gap_id,
        "derivation_receipt_id": derivation.receipt_id,
        "admission_receipt_id": admission.receipt_id,
        "proposal_id": proposal.proposal_id,
        "admitted": admission.admitted,
        "canonical_memory_mutation": "none",
        "production_promotion_eligible": False,
    }


def replay_repeated_failure_challenge(report_path: Path | str) -> dict:
    """Replay a R3-9 REPEATED_FAILURE report without mutating any database."""
    report_path = Path(report_path).expanduser().resolve()
    report = _load(report_path)
    campaign_id = _boundary(report, version=REPEATED_FAILURE_CHALLENGE_VERSION)
    source, derived = _verify_databases(report)
    source_digest = _sha256(source)
    captured_ids = _capture_transition_ids(report)
    try:
        conn = db.connect_read_only(derived)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise NonP12ChallengeReplayError(
            f"challenge derived database cannot replay: {derived}") from exc
    try:
        _verify_counts(conn, report, (
            "tehm_transitions", "tehm_memory_events", "tehm_rule_status",
        ))
        repeated = detect_repeated_failures(
            conn, campaign_id=campaign_id, mechanism_family="HANDSHAKE_COMPLETION",
            min_independent_observations=2)
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise NonP12ChallengeReplayError(
            "repeated-failure diagnostic cannot replay") from exc
    finally:
        conn.close()

    repeated_payload = report.get("repeated_failure_receipt")
    try:
        typed = RepeatedFailureReceipt.from_dict(repeated_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise NonP12ChallengeReplayError(
            "repeated-failure receipt is invalid") from exc
    _assert_receipt_identity(repeated_payload, typed,
                             label="repeated-failure receipt")
    matching = [item for item in repeated
                if item.receipt_digest == typed.receipt_digest]
    if len(matching) != 1:
        raise NonP12ChallengeReplayError(
            "repeated-failure receipt does not replay from derived database")
    if not set(typed.failure_transition_ids) <= captured_ids:
        raise NonP12ChallengeReplayError(
            "repeated-failure evidence is outside training captures")
    derivation_payload = report.get("evolution_reason_derivation")
    admission_payload = report.get("evolution_admission")
    try:
        derivation = EvolutionReasonDerivationReceipt.from_dict(derivation_payload)
        admission = EvolutionAdmissionReceipt.from_dict(admission_payload)
        expected_derivation = derive_repeated_failure_reason(
            typed, campaign_id=campaign_id, case_id=derivation.case_id)
        if expected_derivation is None or \
                expected_derivation.receipt_digest != derivation.receipt_digest:
            raise NonP12ChallengeReplayError(
                "repeated-failure derivation does not replay")
        expected_admission = admit_evolution_reason(
            derivation, campaign_id=campaign_id,
            learner_eligible=typed.learner_eligible,
            repeated_failure=typed)
    except NonP12ChallengeReplayError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise NonP12ChallengeReplayError(
            "repeated-failure typed evidence cannot replay") from exc
    _assert_receipt_identity(derivation_payload, derivation,
                             label="evolution derivation")
    _assert_admission_identity(admission_payload, admission)
    if expected_admission != admission or derivation.reason != "REPEATED_FAILURE" or \
            not admission.admitted:
        raise NonP12ChallengeReplayError(
            "repeated-failure admission replay mismatch")
    independence = report.get("independence")
    if not isinstance(independence, Mapping) or \
            independence.get("independent_observation_count") != typed.independent_observation_count or \
            independence.get("all_oracles_complete") is not True:
        raise NonP12ChallengeReplayError(
            "repeated-failure independence summary disagrees with receipt")
    if _sha256(source) != source_digest:
        raise NonP12ChallengeReplayError("source database changed during replay")
    return {
        "version": NON_P12_CHALLENGE_REPLAY_VERSION,
        "kind": "REPEATED_FAILURE",
        "campaign_id": campaign_id,
        "source_db_sha256": source_digest,
        "repeated_failure_receipt_id": typed.receipt_id,
        "derivation_receipt_id": derivation.receipt_id,
        "admission_receipt_id": admission.receipt_id,
        "admitted": admission.admitted,
        "canonical_memory_mutation": "none",
        "production_promotion_eligible": False,
    }


__all__ = [
    "CAPABILITY_GAP_CHALLENGE_VERSION", "REPEATED_FAILURE_CHALLENGE_VERSION",
    "NON_P12_CHALLENGE_REPLAY_VERSION", "NonP12ChallengeReplayError",
    "replay_capability_gap_challenge", "replay_repeated_failure_challenge",
]
