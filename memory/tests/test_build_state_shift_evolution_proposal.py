"""The state-shift proposal command is read-only and provenance-bound."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_state_shift_evolution_proposal import (
    MANIFEST_VERSION,
    StateShiftProposalScriptError,
    build_state_shift_evolution_proposal,
)
from tehm.evolution import append_state_shift_observation
from tehm.knowledge import MechanismKnowledge
from tehm.state import build_support_envelope, evaluate_state_shift


def _receipts():
    knowledge = MechanismKnowledge(
        knowledge_id="script-shift-k", version=1,
        mechanism_family="HANDSHAKE_COMPLETION",
        compatibility_profile="rtl.fsm.single_guard.v1",
        antecedent={"failure": "completion_not_observed"},
        intervention={"family": "GUARD_RESTORE"},
        mediated_effects=({"effect": "legal_transition"},),
        expected_outcome={"outcome": "PASS"},
        positive_applicability=({
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1",
        },),
        negative_applicability=(), preserved_obligations=("target_trace_pass",),
        known_failure_modes=(), causal_path_ids=("script-shift-path",),
        evidence_level="L2_CONTROLLED_INTERVENTION",
        support_lineages=("script-shift-lineage",),
    )
    envelope = build_support_envelope(knowledge, (), ({
        "transition_id": "support-transition", "split": "training",
        "learner_eligible": True, "verdict": "PASS", "oracle_complete": True,
        "platform": "sky130",
    },))
    return tuple(evaluate_state_shift(
        {"mechanism_family": "HANDSHAKE_COMPLETION",
         "compatibility_profile": "rtl.fsm.single_guard.v1",
         "platform": "asap7"},
        {"resolution_id": resolution}, knowledge, envelope)
        for resolution in ("script-resolution-a", "script-resolution-b"))


def _frozen_audit_snapshot(tmp_path: Path):
    from tehm import db

    path = tmp_path / "state-shift.sqlite"
    conn = db.connect(path)
    db.ensure_schema(conn)
    receipts = _receipts()
    events = tuple(
        append_state_shift_observation(
            conn, receipt, transition_id=transition_id, campaign_id="audit",
            learner_eligible=False, created_at=f"2026-09-01T00:00:0{index}Z")
        for index, (receipt, transition_id) in enumerate(
            zip(receipts, ("script-transition-a", "script-transition-b")))
    )
    # Immutable snapshots must not have a live WAL sidecar.  Checkpoint before
    # closing so the command cannot accidentally read a stale main database.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    return path, receipts, events


def _manifest(path: Path, receipts, events, **overrides) -> Path:
    payload = {
        "version": MANIFEST_VERSION,
        "source_db": path.name,
        "campaign_id": "audit",
        "knowledge_object_id": receipts[0].knowledge_object_id,
        "transition_ids": [event.source_id for event in events],
        "no_memory_outcomes": ["PASS", "PASS"],
        "historical_memory_outcomes": ["PASS", "FAIL"],
        "evidence_refs": [
            ref for event, receipt in zip(events, receipts)
            for ref in (event.event_digest, receipt.receipt_id)
        ],
    }
    payload.update(overrides)
    manifest = path.parent / "proposal-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return manifest


def test_command_replays_audit_snapshot_without_mutation(tmp_path):
    path, receipts, events = _frozen_audit_snapshot(tmp_path)
    manifest = _manifest(path, receipts, events)
    output = tmp_path / "proposal-report.json"
    plan_output = tmp_path / "retain-plan.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    report = build_state_shift_evolution_proposal(
        path, manifest, output=output, plan_output=plan_output)

    assert report["proposal"]["operation"] == "RETAIN"
    assert report["proposal"]["evolution_reason"] == "NOT_LEARNER_ELIGIBLE"
    assert report["proposal"]["learner_eligible"] is False
    assert report["plan"]["update_target"] == "UPDATE_NONE"
    assert json.loads(plan_output.read_text())["operation"] == "RETAIN"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report["canonical_memory_mutation"] == "none"
    assert report["production_runtime_imported"] is False


def test_command_requires_event_and_receipt_witnesses(tmp_path):
    path, receipts, events = _frozen_audit_snapshot(tmp_path)
    manifest = _manifest(path, receipts, events, evidence_refs=["manual-ref"])
    with pytest.raises(StateShiftProposalScriptError, match="witness events and receipts"):
        build_state_shift_evolution_proposal(
            path, manifest, output=tmp_path / "report.json")


def test_command_rejects_gold_or_repair_fields(tmp_path):
    path, receipts, events = _frozen_audit_snapshot(tmp_path)
    manifest = _manifest(path, receipts, events, gold_patch="forbidden")
    with pytest.raises(StateShiftProposalScriptError, match="gold or repair"):
        build_state_shift_evolution_proposal(
            path, manifest, output=tmp_path / "report.json")


def test_command_rejects_output_collision_with_source(tmp_path):
    path, receipts, events = _frozen_audit_snapshot(tmp_path)
    manifest = _manifest(path, receipts, events)
    with pytest.raises(StateShiftProposalScriptError, match="separate"):
        build_state_shift_evolution_proposal(path, manifest, output=path)
