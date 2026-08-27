"""Typed retrieval (design doc 9, 26 Phase 7, test list 27.1).

Stage 0 query planning -> Stage 1 high-recall -> Stage 2 symbolic filter ->
Stage 3 transparent rerank. The symbolic veto is NEVER overridden by the ranker
(design doc 9.5); only admissible (PROVISIONAL_VALID / VALIDATED) rules are
retrieved (honesty H6).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import RepairContext
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.lifecycle.rule_status import enter_shadow, set_status
from tehm.retrieval.index import build_index
from tehm.retrieval.pipeline import retrieve
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.recall import high_recall
from tehm.retrieval.rerank import rerank
from tehm.retrieval.result import APPLICABLE, INAPPLICABLE, UNRESOLVED
from tehm.retrieval.symbolic_filter import apply_symbolic_filter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _capture_and_crystallize(tmp_tehm, sample_record_dict, n: int = 3):
    conn, store, _ = tmp_tehm
    base = json.loads(json.dumps(sample_record_dict))
    for i in range(n):
        rec = json.loads(json.dumps(base))
        rec["record_id"] = f"rt_{i}"
        rec["lineage_id"] = f"lineage_{i}"
        rec["design_id"] = f"design_{i}"
        rec["episode"] = {"episode_id": f"ep_rt_{i}", "lineage_id": f"lineage_{i}",
                          "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
        rec["action"]["payload"]["config_edits"] = {"PLACE_DENSITY_LB_ADDON": f"0.1{i + 4}"}
        rec["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
        rec["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i + 4}"
        rec["observation_delta"]["first_divergence"]["before"] = 10 + i
        capture(conn, store, ExecutionRecord.from_dict(rec))
    crystallize_all(conn)
    return conn


def _grant_runtime_authority(conn):
    for row in conn.execute("SELECT rule_id FROM tehm_rules"):
        enter_shadow(conn, rule_id=row["rule_id"], target_scope="drc")
        set_status(conn, rule_id=row["rule_id"], target_scope="drc",
                   status="promoted")


# -- Stage 0 -----------------------------------------------------------------

def test_plan_query_embeds_repair_state():
    ctx = RepairContext(design_id="d1", platform="sky130hd", check="drc",
                        reports={"drc": {"status": "violations"}})
    query = plan_query(ctx)
    assert query.query_plan["check"] == "drc"
    assert query.query_plan["diagnostic_view"] == "high"
    assert query.query_plan["procedural_view"] == "high"
    assert query.context_ref == "d1"


def test_plan_query_no_evidence_is_medium():
    query = plan_query(RepairContext())
    assert query.query_plan["diagnostic_view"] == "medium"


# -- Stage 1 / index -----------------------------------------------------------

def test_index_loads_only_admissible_rules(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    index = build_index(conn)
    assert len(index) == 1
    rule = index.get(next(iter(index.rules)))
    assert rule["validity_status"] in ("VALIDATED", "PROVISIONAL_VALID")
    assert rule["transformation_family"] == "ANTENNA_DIODE_REPAIR"
    assert "drc" in index.by_check
    assert rule["source_episodes"]  # populated from tehm_rule_sources
    assert rule["hard_preconditions"] == []
    assert rule["context_predicates"] == {}


def test_index_rejects_tampered_rule_definition(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    rule_id = conn.execute("SELECT rule_id FROM tehm_rules LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE tehm_rules SET before_pattern_json=? WHERE rule_id=?",
        (json.dumps({"target_check": "lvs"}), rule_id))
    conn.commit()

    index = build_index(conn)
    assert index.get(rule_id) is None
    assert rule_id in index.rejected
    assert "content digest mismatch" in index.rejected[rule_id]


def test_index_never_defaults_malformed_hard_preconditions_to_empty(
        tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    rule_id = conn.execute("SELECT rule_id FROM tehm_rules LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE tehm_rules SET hard_preconditions_json=? WHERE rule_id=?",
        (json.dumps({"requires": "drc"}), rule_id))
    conn.commit()

    index = build_index(conn)
    assert index.get(rule_id) is None
    assert "hard_preconditions must decode to list" in index.rejected[rule_id]


def test_context_profile_is_loaded_and_enforced(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    rule_id = conn.execute("SELECT rule_id FROM tehm_rules LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE tehm_rules SET context_profile_json=? WHERE rule_id=?",
        (json.dumps({"compatibility_profile": "rtl.test.v1"}), rule_id))
    conn.commit()

    rule = build_index(conn).get(rule_id)
    assert rule["context_predicates"]["compatibility_profile"] == "rtl.test.v1"
    assert apply_symbolic_filter(
        rule, plan_query(RepairContext(check="drc",
                                       compatibility_profile="rtl.test.v1"))) == APPLICABLE
    assert apply_symbolic_filter(
        rule, plan_query(RepairContext(check="drc",
                                       compatibility_profile="rtl.other.v1"))) == INAPPLICABLE


def test_invalid_rules_require_explicit_evaluation_opt_in(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    rule_id = conn.execute("SELECT rule_id FROM tehm_rules LIMIT 1").fetchone()[0]
    conn.execute("UPDATE tehm_rules SET validity_status='REJECT_DEGENERATE' WHERE rule_id=?",
                 (rule_id,))
    conn.commit()
    assert build_index(conn).get(rule_id) is None
    assert build_index(conn, require_validity=False).get(rule_id) is not None


def test_high_recall_matches_check(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    index = build_index(conn)
    query = plan_query(RepairContext(check="drc"))
    candidates = high_recall(index, query, limit=5)
    assert candidates
    assert candidates[0].similarity == 1.0
    assert "check" in candidates[0].matched_keys


# -- Stage 2 -----------------------------------------------------------------

def test_symbolic_filter_applicable_inapplicable_unresolved():
    rule = {"before_pattern": {"target_check": "drc"}, "hard_preconditions": []}
    assert apply_symbolic_filter(rule, plan_query(RepairContext(check="drc"))) == APPLICABLE
    assert apply_symbolic_filter(rule, plan_query(RepairContext(check="lvs"))) == INAPPLICABLE
    assert apply_symbolic_filter(rule, plan_query(RepairContext())) == UNRESOLVED


def test_symbolic_filter_hole_check_matches_any():
    rule = {"before_pattern": {"target_check": "$H0"}, "hard_preconditions": []}
    assert apply_symbolic_filter(rule, plan_query(RepairContext(check="lvs"))) == APPLICABLE
    assert apply_symbolic_filter(rule, plan_query(RepairContext())) == UNRESOLVED


def test_symbolic_filter_hard_preconditions_unknown_never_pass():
    rule = {"before_pattern": {"target_check": "drc"},
            "hard_preconditions": ["is_fsm_state($SRC)"]}  # v1: no evaluator
    assert apply_symbolic_filter(rule, plan_query(RepairContext(check="drc"))) == UNRESOLVED


# -- Stage 3 -----------------------------------------------------------------

def _scored_rule(rule_id, sim, status, *, utility=None, confidence=None, risk=None):
    return (
        {"rule_id": rule_id,
         "transformation_family": "ANTENNA_DIODE_REPAIR",
         "before_pattern": {"target_check": "drc"},
         "utility": utility or {"activations": 0},
         "confidence": confidence or {"rule": None},
         "risk_profile": risk or [],
         "source_episodes": ["e1"]},
        sim, status,
    )


def test_rerank_vetoes_inapplicable_even_when_high_similarity():
    results = rerank([
        _scored_rule("r_low", 1.0, INAPPLICABLE),   # vetoed despite sim 1.0
        _scored_rule("r_good", 0.8, APPLICABLE),
    ], limit=5)
    assert [r.rule_id for r in results] == ["r_good"]


def test_rerank_transparent_multiplicative_score():
    results = rerank([
        _scored_rule("r1", 1.0, APPLICABLE,
                     utility={"activations": 4, "positive": 2, "neutral": 2},
                     confidence={"rule": 0.8}),
    ], limit=5)
    r = results[0]
    # utility = (2 + 0.5*2)/4 = 0.75; conf = 0.8; risk = 0 -> score = 1.0*0.75*0.8
    assert r.score == pytest.approx(0.6)


def test_rerank_unresolved_downweighted_not_dropped():
    results = rerank([
        _scored_rule("r_unres", 0.9, UNRESOLVED),
    ], limit=5)
    assert [r.rule_id for r in results] == ["r_unres"]
    # down-weighted: score = 0.9 * 0.5(util) * 0.5(conf) * 0.5(unresolved)
    assert results[0].score == pytest.approx(0.9 * 0.5 * 0.5 * 0.5)


def test_rerank_risk_penalty():
    results = rerank([
        _scored_rule("r_risky", 1.0, APPLICABLE,
                     risk=[{"risk": "CREATED_REGRESSION", "support": 1}]),
    ], limit=5)
    assert results[0].risk_penalty == 0.25
    assert results[0].score == pytest.approx(0.5 * 0.5 * 0.75)


# -- pipeline + backend ---------------------------------------------------------

def test_retrieve_excludes_valid_but_unpromoted_rule(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    receipt = retrieve(conn, RepairContext(check="drc"), limit=5)
    assert receipt.candidates_retrieved == 0
    assert receipt.results == []


def test_retrieve_returns_applicable_rule(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    _grant_runtime_authority(conn)
    receipt = retrieve(conn, RepairContext(check="drc"), limit=5)
    assert receipt.candidates_retrieved == 1
    assert receipt.applicable == 1
    assert receipt.inapplicable == 0
    assert receipt.results
    assert receipt.results[0].applicability_status == APPLICABLE
    assert receipt.results[0].transformation_family == "ANTENNA_DIODE_REPAIR"


def test_retrieve_vetoes_wrong_check(tmp_tehm, sample_record_dict):
    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    _grant_runtime_authority(conn)
    receipt = retrieve(conn, RepairContext(check="lvs"), limit=5)
    assert receipt.inapplicable == 1
    assert receipt.results == []          # symbolic veto not overridden


def test_backend_retrieve_returns_candidates(tmp_tehm, sample_record_dict):
    import os
    os.environ["TEHM_DB"] = str(tmp_tehm[2] / "tehm.sqlite")
    os.environ["TEHM_ARTIFACTS_ROOT"] = str(tmp_tehm[2] / "artifacts")
    from tehm_backend import TehmMemoryBackend

    conn = _capture_and_crystallize(tmp_tehm, sample_record_dict)
    _grant_runtime_authority(conn)
    backend = TehmMemoryBackend(db_path=tmp_tehm[2] / "tehm.sqlite",
                                artifact_root=tmp_tehm[2] / "artifacts")
    candidates = backend.retrieve(backend.build_query(RepairContext(check="drc")),
                                  limit=5)
    assert candidates
    assert candidates[0].source == "tehm_rule"
    assert candidates[0].payload["applicability_status"] == APPLICABLE
    proposal = backend.propose_activation(
        candidates[0], RepairContext(
            check="drc", cfg={"DESIGN_NAME": "heldout"},
            reports={"drc": {"status": "violations"}}))
    assert proposal is not None
    assert proposal.applicability_status == APPLICABLE
    assert proposal.binding["action"]["domain"]
    assert proposal.activation_id.startswith("activation_")
    backend.close()
