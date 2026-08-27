"""Transition completeness (design doc honesty H1, test list 27.1).

Every captured transition must carry: source state, action, target state, and a
verifier snapshot. The honesty gate fails when any field is missing or when the
FK to tehm_states dangles.
"""
from __future__ import annotations

import copy
import json

import pytest

from tehm import honesty
from tehm.canonical.capture import ExecutionRecord, capture


def _capture(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    record = ExecutionRecord.from_dict(sample_record_dict)
    return capture(conn, store, record)


def test_capture_produces_complete_transition(tmp_tehm, sample_record_dict):
    receipt = _capture(tmp_tehm, sample_record_dict)
    conn, _, _ = tmp_tehm
    row = conn.execute(
        "SELECT * FROM tehm_transitions WHERE transition_id=?", (receipt.transition_id,)
    ).fetchone()
    assert row is not None
    assert row["source_state_id"]
    assert row["target_state_id"]
    assert row["action_json"]
    assert row["verifier_json"]
    assert row["outcome"] == "PASS"


def test_capture_preserves_expanded_full_oracle_receipt(tmp_tehm, sample_record_dict):
    record = copy.deepcopy(sample_record_dict)
    record["verification"]["full_oracle"] = {
        "before": {"complete": True, "checks": {"strict_signoff": True}},
        "after": {"complete": True, "checks": {"strict_signoff": True}},
    }
    receipt = _capture(tmp_tehm, record)
    conn, _, _ = tmp_tehm
    row = conn.execute(
        "SELECT verifier_json FROM tehm_transitions WHERE transition_id=?",
        (receipt.transition_id,),
    ).fetchone()
    persisted = json.loads(row["verifier_json"])
    assert persisted["full_oracle"]["before"]["complete"] is True
    assert persisted["full_oracle"]["after"]["checks"]["strict_signoff"] is True


def test_h1_gate_green(tmp_tehm, sample_record_dict):
    _capture(tmp_tehm, sample_record_dict)
    conn, _, _ = tmp_tehm
    ok, detail = honesty.h1_transition_completeness(conn)
    assert ok, detail


def test_h1_gate_detects_missing_verifier(tmp_tehm, sample_record_dict):
    receipt = _capture(tmp_tehm, sample_record_dict)
    conn, _, _ = tmp_tehm
    conn.execute(
        "UPDATE tehm_transitions SET verifier_json='' WHERE transition_id=?",
        (receipt.transition_id,))
    conn.commit()
    ok, detail = honesty.h1_transition_completeness(conn)
    assert not ok
    assert "missing-action-or-verifier" in detail


def test_h1_gate_detects_dangling_state(tmp_tehm, sample_record_dict):
    receipt = _capture(tmp_tehm, sample_record_dict)
    conn, _, _ = tmp_tehm
    # Point the transition at a state id that does not exist.
    conn.execute(
        "UPDATE tehm_transitions SET source_state_id='state_deadbeef' "
        "WHERE transition_id=?", (receipt.transition_id,))
    conn.commit()
    ok, detail = honesty.h1_transition_completeness(conn)
    assert not ok
    assert "dangling-state" in detail


def test_invalid_record_rejected():
    with pytest.raises(Exception):
        ExecutionRecord.from_dict({"domain": "flow.signoff"})
