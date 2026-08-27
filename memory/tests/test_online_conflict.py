"""Online novelty/conflict receipts remain shadow-only."""
from __future__ import annotations

from pathlib import Path

from tehm.canonical.capture import capture
from tehm.evolution import detect_conflicts, observe_transition
from tehm.rtl.rtl_evidence import build_rtl_execution_record


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def test_definition_conflict_emits_event_without_lifecycle_mutation(tmp_tehm):
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    first_id = capture(conn, store, first).transition_id
    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack:alternate"
    second.action["payload"]["add_condition"] = "ready"
    second_id = capture(conn, store, second).transition_id
    observe_transition(conn, first_id)
    receipt = observe_transition(conn, second_id)
    conflict = detect_conflicts(conn, second_id)
    assert conflict.has_conflict is True
    assert "DEFINITION_CONFLICT" in conflict.conflict_types
    assert any(event.event_type == "RULE_CONFLICT" for event in receipt.events)
    assert receipt.consolidation_operation == "SPLIT"
    assert receipt.consolidation_decision.rationale
    assert conn.execute("SELECT COUNT(*) FROM tehm_rule_status").fetchone()[0] == 0
