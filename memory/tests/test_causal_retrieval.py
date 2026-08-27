"""Causal path recall is isolated from production rule retrieval."""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts import MemoryQuery
from tehm.canonical.capture import capture
from tehm.causal import build_transition_causal_fragment, consolidate_causal_path
from tehm.retrieval.causal_recall import retrieve_causal_paths
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm_backend import TehmMemoryBackend


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def test_causal_recall_matches_profile_and_mechanism_only(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    matches = retrieve_causal_paths(conn, query)
    assert [match.path_id for match in matches] == [path.path_id]
    mismatch = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.sequential.reset_branch.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, mismatch) == []


def test_causal_recall_keeps_transformation_family_separate(tmp_tehm):
    """R0 may route by edit family without conflating it with mechanism family."""
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])

    metadata = MemoryQuery(query_plan={
        "mechanism_signature": {"transformation_family": "GUARD_STRENGTHEN"},
    })
    matches = retrieve_causal_paths(conn, metadata)
    assert [match.path_id for match in matches] == [path.path_id]

    wrong_edit = MemoryQuery(query_plan={
        "mechanism_signature": {"transformation_family": "RESET_RESTORE"},
    })
    assert retrieve_causal_paths(conn, wrong_edit) == []


def test_causal_recall_vetoes_detail_mechanism_mismatch(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    support = path.support
    assert support["mechanism_signatures"]
    exact = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "module": "req_ack_fsm", "source_state": "SEND",
            "target_state": "DONE", "guard": "ack",
        },
    })
    matches = retrieve_causal_paths(conn, exact)
    assert len(matches) == 1
    assert matches[0].mechanism_match is True
    assert {"module", "source_state", "target_state", "guard"} <= set(
        matches[0].matched_fields)

    mechanism_mismatch = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {
            "mechanism_family": "HANDSHAKE_COMPLETION",
            "module": "req_ack_fsm", "source_state": "SEND",
            "target_state": "DONE", "guard": "ready",
        },
    })
    assert retrieve_causal_paths(conn, mechanism_mismatch) == []

    required_effect_mismatch = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
        "required_effect": "effect_that_is_not_in_the_path",
    })
    assert retrieve_causal_paths(conn, required_effect_mismatch) == []

    prior_action = support["action_digests"][0]
    repeated_attempt = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
        "prior_action_digests": [prior_action],
    })
    assert retrieve_causal_paths(conn, repeated_attempt) == []


def test_backend_causal_paths_requires_evaluation_flag(tmp_tehm):
    conn, _, tmp_path = tmp_tehm
    backend = TehmMemoryBackend(db_path=tmp_path / "tehm.sqlite",
                                artifact_root=tmp_path / "artifacts")
    query = MemoryQuery(query_plan={})
    with pytest.raises(RuntimeError, match="evaluation-only"):
        backend.get_causal_paths(query)
    backend.close()


def test_causal_recall_skips_malformed_source_witness(tmp_tehm):
    conn, store, _ = tmp_tehm
    transition_id = capture(
        conn, store,
        build_rtl_execution_record(PROJECT, oracle=None, store=store)
    ).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    conn.execute(
        "UPDATE tehm_causal_paths SET source_transitions_json=? WHERE path_id=?",
        ("[", path.path_id))
    conn.commit()
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, query) == []
