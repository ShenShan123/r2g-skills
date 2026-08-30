"""Causal path recall is isolated from production rule retrieval."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import MemoryQuery
from tehm.canonical.capture import capture
from tehm.causal import build_transition_causal_fragment, consolidate_causal_path
from tehm.causal.matcher import match_causal_path
from tehm.causal.path_builder import causal_path_digest
from tehm.ids import stable_dumps
from tehm.retrieval.causal_recall import retrieve_causal_paths
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm_backend import TehmMemoryBackend


PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _rewrite_path_support(conn, path_id: str, **updates) -> dict:
    """Update a shadow support witness while preserving its row digest."""
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    support = json.loads(row["support_json"])
    support.update(updates)
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"],
        source_transition_ids=json.loads(row["source_transitions_json"]),
        node_ids=json.loads(row["ordered_nodes_json"]),
        edge_ids=json.loads(row["ordered_edges_json"]), support=support)
    conn.execute(
        "UPDATE tehm_causal_paths SET support_json=?, path_digest=? "
        "WHERE path_id=?", (stable_dumps(support), digest, path_id))
    conn.commit()
    return support


def _clone_quality_path(conn, path_id: str, *, utility_score: float,
                        risk_penalty: float) -> str:
    """Create a second immutable shadow lineage for ranking assertions."""
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    support = json.loads(row["support_json"])
    support.update(utility_score=utility_score, risk_penalty=risk_penalty)
    source_ids = json.loads(row["source_transitions_json"])
    node_ids = json.loads(row["ordered_nodes_json"])
    edge_ids = json.loads(row["ordered_edges_json"])
    digest = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"], source_transition_ids=source_ids,
        node_ids=node_ids, edge_ids=edge_ids, support=support)
    cloned_id = "causal_path_" + digest.split(":", 1)[1][:16]
    conn.execute(
        """INSERT INTO tehm_causal_paths
           (path_id, mechanism_family, compatibility_profile,
            ordered_nodes_json, ordered_edges_json, evidence_level,
            support_json, source_transitions_json, path_digest, status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cloned_id, row["mechanism_family"], row["compatibility_profile"],
         row["ordered_nodes_json"], row["ordered_edges_json"],
         row["evidence_level"], stable_dumps(support),
         row["source_transitions_json"], digest, row["status"],
         row["created_at"], row["updated_at"]))
    conn.commit()
    return cloned_id


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
    assert matches[0].quality_status == "NOT_ESTABLISHED"
    assert matches[0].utility_score == 0.5
    assert matches[0].risk_penalty == 0.5
    assert matches[0].evidence_support_score == 0.5
    assert matches[0].evidence_support_count == 1
    assert matches[0].evidence_lineage_count == 1
    assert matches[0].evidence_support_status == "NOT_ESTABLISHED"
    mismatch = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.sequential.reset_branch.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, mismatch) == []


@pytest.mark.parametrize(
    ("support", "reason"),
    [
        ("not-json", "malformed_support"),
        ({"mechanism_signatures": ["not-a-mapping"]},
         "malformed_mechanism_signatures"),
    ],
)
def test_causal_matcher_rejects_malformed_support_witnesses(support, reason):
    """Direct matcher callers cannot bypass path support typing."""
    path = {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "support_json": support,
    }
    match = match_causal_path(path, {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
    })
    assert match.eligible is False
    assert match.reason == reason


@pytest.mark.parametrize(
    ("query_plan", "reason"),
    [
        ({"mechanism_signature": []}, "malformed_query_mechanism_signature"),
        ({"causal_path_features": "not-a-map"},
         "malformed_query_causal_path_features"),
        ({"mechanism_family": []}, "malformed_query_mechanism_family"),
        ({"prior_action_digests": "not-a-list"},
         "malformed_query_prior_action_digests"),
    ],
)
def test_causal_matcher_rejects_malformed_query_plan(query_plan, reason):
    """A malformed query cannot silently become an unconstrained recall."""
    path = {
        "mechanism_family": "HANDSHAKE_COMPLETION",
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "support": {"mechanism_signatures": [{
            "mechanism_family": "HANDSHAKE_COMPLETION",
        }]},
    }
    match = match_causal_path(path, query_plan)
    assert match.eligible is False
    assert match.reason == reason


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


def test_causal_recall_quality_reranks_shadow_paths(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    _rewrite_path_support(conn, path.path_id, utility_score=0.2,
                          risk_penalty=0.8)
    high_id = _clone_quality_path(conn, path.path_id, utility_score=0.9,
                                  risk_penalty=0.1)
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    matches = retrieve_causal_paths(conn, query)
    assert [match.path_id for match in matches] == [high_id, path.path_id]
    assert matches[0].quality_status == "ESTABLISHED"
    assert matches[0].mechanism_score == matches[1].mechanism_score
    assert matches[0].score > matches[1].score
    assert matches[0].score == pytest.approx(
        matches[0].mechanism_score * 0.9 * 0.5 * 0.9)


def test_causal_recall_skips_malformed_quality_claim(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    _rewrite_path_support(conn, path.path_id, utility_score=0.9,
                          risk_penalty="not-a-number")
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, query) == []


def test_causal_recall_derives_quality_from_canonical_transition(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.observation_delta["utility_verdict"] = "PARETO_SAFE"
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    matches = retrieve_causal_paths(conn, query)
    assert [match.path_id for match in matches] == [path.path_id]
    match = matches[0]
    assert match.quality_status == "ESTABLISHED"
    assert match.quality_source == "canonical_transition"
    assert match.quality_evidence_transition_ids == (transition_id,)
    assert match.utility_score == 1.0
    assert match.risk_penalty == 0.0
    assert match.evidence_support_score == 0.5
    assert match.score == pytest.approx(match.mechanism_score * 0.5)
    assert match.quality_reason == "canonical_utility_verdict_bound"


def test_causal_recall_establishes_independent_lineage_support(tmp_tehm):
    conn, store, _ = tmp_tehm
    first = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    first_id = capture(conn, store, first).transition_id

    second = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    second.record_id = "rtl:req_ack_fsm:lineage-b"
    second.lineage_id = "lineage-b"
    second.episode = dict(second.episode or {})
    second.episode.update(
        episode_id="rtl_ep:req_ack_fsm:lineage-b", lineage_id="lineage-b")
    # Lineage is intentionally excluded from state identity.  Add a harmless
    # source witness so this is a distinct canonical transition/run.
    second.before["config"]["lineage_witness"] = "lineage-b"
    second.after["config"]["lineage_witness"] = "lineage-b"
    second_id = capture(conn, store, second).transition_id
    assert second_id != first_id

    first_fragment = build_transition_causal_fragment(conn, first_id)
    second_fragment = build_transition_causal_fragment(conn, second_id)
    path = consolidate_causal_path(conn, [first_fragment, second_fragment])
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    matches = retrieve_causal_paths(conn, query)
    assert [match.path_id for match in matches] == [path.path_id]
    match = matches[0]
    assert match.evidence_support_count == 2
    assert match.evidence_lineage_count == 2
    assert match.evidence_support_score == 1.0
    assert match.evidence_support_status == "ESTABLISHED"
    assert match.evidence_support_reason == "independent_lineage_support_bound"
    assert match.score == pytest.approx(match.mechanism_score * 0.5 * 0.5)


def test_causal_recall_rejects_inconsistent_fragment_support_count(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    path = consolidate_causal_path(conn, [fragment])
    _rewrite_path_support(conn, path.path_id, fragment_count=2)
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, query) == []


def test_causal_recall_skips_malformed_canonical_quality_witness(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.observation_delta["utility_verdict"] = "PARETO_SAFE"
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    consolidate_causal_path(conn, [fragment])
    conn.execute(
        "UPDATE tehm_transitions SET observation_delta_json=? "
        "WHERE transition_id=?", ("{", transition_id))
    conn.commit()
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, query) == []


def test_causal_recall_skips_tampered_canonical_quality_payload(tmp_tehm):
    conn, store, _ = tmp_tehm
    record = build_rtl_execution_record(PROJECT, oracle=None, store=store)
    record.observation_delta["utility_verdict"] = "PARETO_SAFE"
    transition_id = capture(conn, store, record).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    consolidate_causal_path(conn, [fragment])
    delta = dict(record.observation_delta)
    delta["utility_verdict"] = "HARMFUL"
    conn.execute(
        "UPDATE tehm_transitions SET observation_delta_json=? "
        "WHERE transition_id=?", (stable_dumps(delta), transition_id))
    conn.commit()
    query = MemoryQuery(query_plan={
        "compatibility_profile": "rtl.fsm.single_guard.v1",
        "mechanism_signature": {"mechanism_family": "HANDSHAKE_COMPLETION"},
    })
    assert retrieve_causal_paths(conn, query) == []
