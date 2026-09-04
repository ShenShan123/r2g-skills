#!/usr/bin/env python3
"""Build a replayable state-shift proposal from a frozen TEHM event log.

The event log is the provenance authority for state-shift receipts and learner
eligibility.  This command only binds caller-supplied, independently produced
oracle outcomes to those events; it never derives an evolution reason from a
PASS/FAIL result and never mutates canonical memory, lifecycle, authority, or
production runtime state.  An optional P13 plan is emitted through the typed
conversion boundary and remains shadow-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.db import connect_read_only  # noqa: E402
from tehm.evolution import (  # noqa: E402
    StateShiftEvolutionError,
    load_state_shift_observations,
    propose_repeated_state_shift_from_events,
    propose_repeated_state_shift_from_paired_receipts,
    state_shift_proposal_to_localized_plan,
)
from tehm.evaluation import PairedCandidateExecutionReceipt  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402


MANIFEST_VERSION = "state-shift-evolution-manifest-v1"
REPORT_VERSION = "state-shift-evolution-proposal-report-v1"
PAIRED_RECEIPTS_VERSION = "p12-paired-receipts-map-v1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset({
    "fix", "gold", "gold_patch", "repaired_rtl", "heldout_answer",
    "repair_result", "candidate_patch", "oracle_payload",
})


class StateShiftProposalScriptError(ValueError):
    """Input evidence cannot safely be bound to a state-shift proposal."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(value: Mapping) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(dict(value)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StateShiftProposalScriptError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise StateShiftProposalScriptError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StateShiftProposalScriptError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StateShiftProposalScriptError(f"{name} must be a sequence")
    result = tuple(_text(item, name) for item in value)
    if not allow_empty and not result:
        raise StateShiftProposalScriptError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise StateShiftProposalScriptError(f"{name} must not contain duplicates")
    return result


def _outcomes(value: object, name: str) -> tuple[str, ...]:
    # Keep outcome labels explicit in the manifest.  The typed proposal API
    # performs the final domain/length check after event replay.
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StateShiftProposalScriptError(f"{name} must be a sequence")
    result = tuple(_text(item, name).upper() for item in value)
    if not result:
        raise StateShiftProposalScriptError(f"{name} must not be empty")
    return result


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_KEYS or _contains_forbidden(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _digest_text(value: object, name: str) -> str:
    value = _text(value, name)
    if _DIGEST_RE.fullmatch(value) is None:
        raise StateShiftProposalScriptError(f"{name} must be a sha256 digest")
    return value


def _frozen_snapshot(path: Path) -> None:
    # connect_read_only() uses immutable=1.  Refusing live WAL sidecars avoids
    # silently reading a stale main file while the event log is still moving.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            raise StateShiftProposalScriptError(
                f"source DB is not a frozen snapshot; remove sidecar {sidecar}")


def _paired_receipts(path: Path, transition_ids: Sequence[str]) -> dict[str, PairedCandidateExecutionReceipt]:
    payload = _load_json(path, "P12 paired receipts map")
    if payload.get("version") != PAIRED_RECEIPTS_VERSION:
        raise StateShiftProposalScriptError("P12 paired receipts map version mismatch")
    raw = payload.get("receipts")
    if not isinstance(raw, Mapping) or set(raw) != set(transition_ids):
        raise StateShiftProposalScriptError(
            "P12 paired receipts must cover exactly all transition IDs")
    result: dict[str, PairedCandidateExecutionReceipt] = {}
    for transition_id in transition_ids:
        try:
            result[transition_id] = PairedCandidateExecutionReceipt.from_dict(
                raw[transition_id])
        except (TypeError, ValueError) as exc:
            raise StateShiftProposalScriptError(
                f"P12 paired receipt for {transition_id} is invalid") from exc
    return result


def _validate_manifest_source(payload: Mapping, manifest_path: Path,
                              source_path: Path, source_digest: str) -> None:
    declared = payload.get("source_db")
    if declared is not None:
        declared_path = Path(_text(declared, "source_db")).expanduser()
        if not declared_path.is_absolute():
            declared_path = manifest_path.parent / declared_path
        if declared_path.resolve() != source_path:
            raise StateShiftProposalScriptError(
                "manifest source_db does not match --source-db")
    expected = payload.get("source_db_sha256")
    if expected is not None and _digest_text(expected, "source_db_sha256") != source_digest:
        raise StateShiftProposalScriptError("source_db_sha256 does not match source DB")


def build_state_shift_evolution_proposal(
        source_db: Path | str, manifest: Path | str, *,
        output: Path | str, plan_output: Path | str | None = None,
        paired_receipts: Path | str | None = None) -> dict:
    """Replay one frozen event-log snapshot and emit proposal evidence."""
    source_path = Path(source_db).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    plan_path = (Path(plan_output).expanduser().resolve()
                 if plan_output is not None else None)
    if not source_path.is_file():
        raise StateShiftProposalScriptError(f"source DB is not a file: {source_path}")
    paired_path = (Path(paired_receipts).expanduser().resolve()
                   if paired_receipts is not None else None)
    if output_path in {source_path, manifest_path}:
        raise StateShiftProposalScriptError(
            "proposal inputs/outputs must be separate from source DB and manifest")
    if plan_path is not None and plan_path in {
            source_path, manifest_path, output_path}:
        raise StateShiftProposalScriptError(
            "proposal inputs/outputs must be separate from source DB and manifest")
    if paired_path is not None and paired_path in {
            source_path, manifest_path, output_path, plan_path}:
        raise StateShiftProposalScriptError(
            "proposal inputs/outputs must be separate from source DB and manifest")
    payload = _load_json(manifest_path, "state-shift evolution manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise StateShiftProposalScriptError("state-shift evolution manifest version mismatch")
    if _contains_forbidden(payload):
        raise StateShiftProposalScriptError(
            "state-shift evolution manifest contains gold or repair fields")
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    knowledge_object_id = _text(payload.get("knowledge_object_id"), "knowledge_object_id")
    transition_ids = _strings(payload.get("transition_ids"), "transition_ids")
    if paired_path is not None and (
            "no_memory_outcomes" in payload or
            "historical_memory_outcomes" in payload):
        raise StateShiftProposalScriptError(
            "paired receipts mode cannot mix hand-written outcome fields")
    no_memory_outcomes = None
    historical_memory_outcomes = None
    if paired_path is None:
        no_memory_outcomes = _outcomes(
            payload.get("no_memory_outcomes"), "no_memory_outcomes")
        historical_memory_outcomes = _outcomes(
            payload.get("historical_memory_outcomes"), "historical_memory_outcomes")
    evidence_refs = _strings(payload.get("evidence_refs"), "evidence_refs")
    raw_min = payload.get("min_repeats", 2)
    if type(raw_min) is not int or raw_min < 2:
        raise StateShiftProposalScriptError("min_repeats must be an integer >= 2")
    requested_operation = payload.get("requested_operation")
    if requested_operation is not None:
        requested_operation = _text(requested_operation, "requested_operation")
    partitions = _strings(
        payload.get("partition_evidence_refs", ()),
        "partition_evidence_refs", allow_empty=True)
    if paired_path is not None and not paired_path.is_file():
        raise StateShiftProposalScriptError(
            f"P12 paired receipts map is not a file: {paired_path}")
    source_digest = _sha256(source_path)
    _validate_manifest_source(payload, manifest_path, source_path, source_digest)
    _frozen_snapshot(source_path)

    try:
        conn = connect_read_only(source_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StateShiftProposalScriptError(str(exc)) from exc
    try:
        if paired_path is None:
            proposal = propose_repeated_state_shift_from_events(
                conn, campaign_id=campaign_id,
                knowledge_object_id=knowledge_object_id,
                transition_ids=transition_ids,
                no_memory_outcomes=no_memory_outcomes,
                historical_memory_outcomes=historical_memory_outcomes,
                evidence_refs=evidence_refs, min_repeats=raw_min,
                requested_operation=requested_operation,
                partition_evidence_refs=partitions,
            )
        else:
            try:
                observations = load_state_shift_observations(
                    conn, campaign_id=campaign_id,
                    knowledge_object_id=knowledge_object_id)
                by_transition = {event.source_id: (event, receipt)
                                 for event, receipt in observations}
                selected = []
                missing = []
                for transition_id in transition_ids:
                    item = by_transition.get(transition_id)
                    if item is None:
                        missing.append(transition_id)
                    else:
                        selected.append(item)
                if missing:
                    raise StateShiftProposalScriptError(
                        "state-shift event map is missing transitions: "
                        + ",".join(missing))
                eligibility = {event.learner_eligible for event, _receipt in selected}
                if len(eligibility) != 1:
                    raise StateShiftProposalScriptError(
                        "paired proposal cannot mix learner and audit observations")
                paired = _paired_receipts(paired_path, transition_ids)
                paired_observations = [
                    (receipt, paired[transition_id])
                    for transition_id, (_event, receipt) in zip(transition_ids, selected)
                ]
                paired_refs = set(evidence_refs)
                paired_refs.update(
                    ref for event, receipt in selected
                    for ref in (event.event_digest, receipt.receipt_id))
                for transition_id in transition_ids:
                    pair = paired[transition_id]
                    paired_refs.update({
                        pair.receipt_digest, pair.routing_receipt_id,
                        pair.arm_receipts["NO_MEMORY"].execution_digest,
                        pair.arm_receipts["ALWAYS_MEMORY"].execution_digest,
                    })
                proposal = propose_repeated_state_shift_from_paired_receipts(
                    paired_observations, knowledge_object_id=knowledge_object_id,
                    transition_ids=transition_ids, evidence_refs=tuple(sorted(paired_refs)),
                    learner_eligible=next(iter(eligibility)), min_repeats=raw_min,
                    requested_operation=requested_operation,
                    partition_evidence_refs=partitions,
                )
            except StateShiftProposalScriptError:
                raise
            except (StateShiftEvolutionError, TypeError, ValueError) as exc:
                raise StateShiftProposalScriptError(str(exc)) from exc
    except (StateShiftEvolutionError, TypeError, ValueError) as exc:
        raise StateShiftProposalScriptError(str(exc)) from exc
    finally:
        conn.close()
    after_digest = _sha256(source_path)
    if source_digest != after_digest:
        raise StateShiftProposalScriptError(
            "source DB changed while replaying read-only proposal")

    plan = None
    p12_trigger_digest = payload.get("p12_trigger_digest")
    if p12_trigger_digest is not None:
        p12_trigger_digest = _digest_text(p12_trigger_digest, "p12_trigger_digest")
    emit_plan = plan_path is not None or p12_trigger_digest is not None
    if emit_plan:
        priority = payload.get("priority", "P1_HIGH")
        value_score = payload.get("value_score", 1.0)
        try:
            plan_obj = state_shift_proposal_to_localized_plan(
                proposal, campaign_id=campaign_id, priority=priority,
                value_score=value_score, p12_trigger_digest=p12_trigger_digest)
        except (StateShiftEvolutionError, TypeError, ValueError) as exc:
            raise StateShiftProposalScriptError(str(exc)) from exc
        plan = {**plan_obj.to_dict(), "plan_digest": plan_obj.plan_digest}
        if plan_path is not None:
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")

    proposal_payload = {
        **proposal.to_dict(),
        "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.proposal_digest,
    }
    report = {
        "version": REPORT_VERSION,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": _digest(payload),
        "source_db": str(source_path),
        "source_db_sha256": source_digest,
        "paired_receipts": ({
            "path": str(paired_path), "sha256": _sha256(paired_path),
            "version": PAIRED_RECEIPTS_VERSION,
        } if paired_path is not None else None),
        "outcome_source": ("typed_paired_receipts" if paired_path is not None
                           else "explicit_manifest"),
        "campaign_id": campaign_id,
        "knowledge_object_id": knowledge_object_id,
        "transition_ids": list(transition_ids),
        "proposal": proposal_payload,
        "plan": plan,
        "plan_output": str(plan_path) if plan_path is not None else None,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
        "shadow_update_policy": "isolated_staging_only",
    }
    report["report_digest"] = _digest(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True,
                        help="frozen TEHM SQLite snapshot")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-output", type=Path,
                        help="optional serialized LocalizedUpdatePlan")
    parser.add_argument("--paired-receipts", type=Path,
                        help="optional transition-keyed P12 paired receipt map")
    args = parser.parse_args(argv)
    try:
        report = build_state_shift_evolution_proposal(
            args.source_db, args.manifest, output=args.output,
            plan_output=args.plan_output, paired_receipts=args.paired_receipts)
    except (OSError, StateShiftProposalScriptError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "proposal_id": report["proposal"]["proposal_id"],
        "operation": report["proposal"]["operation"],
        "learner_eligible": report["proposal"]["learner_eligible"],
        "plan_output": report["plan_output"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
