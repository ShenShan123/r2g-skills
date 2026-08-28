"""Runtime integration (design doc 20.7/20.8/28.4, additive seam).

``tehm_strategies_for`` adapts admissible TEHM rules into diagnose-compatible
strategy proposals marked ``source='tehm_rule'`` — the memory arm's authority,
prepended ahead of the shared cold-start catalog.
"""
from __future__ import annotations

import json

from contracts import RepairContext
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.integration.fix_consultation import tehm_strategies_for
from tehm.lifecycle.rule_status import enter_shadow, set_status
from tehm.retrieval.pipeline import retrieve
from runtime_router import signoff_strategies


def _crystallize_drc_rule(tmp_tehm, sample_record_dict) -> str:
    conn, store, _ = tmp_tehm
    base = json.loads(json.dumps(sample_record_dict))
    for i in range(3):
        rec = json.loads(json.dumps(base))
        rec["record_id"] = f"int_{i}"
        rec["lineage_id"] = f"lineage_{i}"
        rec["episode"] = {"episode_id": f"ep_int_{i}", "lineage_id": f"lineage_{i}",
                          "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
        rec["action"]["payload"]["config_edits"] = {"PLACE_DENSITY_LB_ADDON": f"0.1{i + 4}"}
        rec["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = "0.10"
        rec["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i + 4}"
        rec["observation_delta"]["first_divergence"]["before"] = 10 + i
        capture(conn, store, ExecutionRecord.from_dict(rec))
    rules = crystallize_all(conn)
    return rules[0]["rule_id"]


def _promote(conn, rule_id: str) -> None:
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="promoted")


def test_tehm_strategies_for_applicable_rule(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    _promote(conn, rule_id)
    strategies = tehm_strategies_for(
        conn, check="drc", design_id="heldout", platform="sky130hd",
        cfg={"DESIGN_NAME": "heldout"}, drc={"status": "violations",
                                             "total_violations": 7},
        lvs={"status": "clean"})
    assert strategies, "an applicable tehm rule must produce a strategy"
    strategy = strategies[0]
    assert strategy["source"] == "tehm_rule"     # attribution (28.4)
    assert strategy["rule_id"] == rule_id
    assert strategy["id"].startswith("tehm_")
    assert strategy["tehm_score"] > 0
    assert strategy["recheck"] == "drc"


def test_tehm_strategies_vetoed_for_wrong_check(tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    _promote(conn, rule_id)
    strategies = tehm_strategies_for(
        conn, check="lvs", design_id="heldout", platform="sky130hd",
        cfg={}, drc={"status": "clean"}, lvs={"status": "violations"})
    assert strategies == []      # the drc-only rule is symbolically vetoed


def test_tehm_strategies_empty_store(tmp_tehm):
    conn, _, _ = tmp_tehm
    strategies = tehm_strategies_for(
        conn, check="drc", design_id="d", platform="p", cfg={},
        drc={"status": "violations"}, lvs={})
    assert strategies == []      # no rules yet -> cold start (never fabricated)


def test_consultation_consistent_with_retrieval(tmp_tehm, sample_record_dict):
    """The consultation strategies are a subset of retrieval's applicable rules."""
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    _promote(conn, rule_id)
    receipt = retrieve(conn, RepairContext(check="drc", design_id="heldout"),
                       limit=5)
    strategies = tehm_strategies_for(
        conn, check="drc", design_id="heldout", platform=None, cfg={},
        drc={"status": "violations"}, lvs={})
    assert {s["rule_id"] for s in strategies} <= \
        {r.rule_id for r in receipt.results}


def test_consultation_rechecks_authority_after_retrieval(
        monkeypatch, tmp_tehm, sample_record_dict):
    conn, _, _ = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    _promote(conn, rule_id)

    # Simulate a lifecycle transition between receipt production and the
    # second rule-definition lookup.  A stale retrieval hit must not become a
    # strategy after the rule is demoted.
    import tehm.integration.fix_consultation as consultation
    real_retrieve = consultation.retrieve

    def retrieve_then_demote(connection, context, *, limit):
        receipt = real_retrieve(connection, context, limit=limit)
        set_status(connection, rule_id=rule_id, target_scope="drc",
                   status="demoted")
        return receipt

    monkeypatch.setattr(consultation, "retrieve", retrieve_then_demote)
    strategies = consultation.tehm_strategies_for(
        conn, check="drc", design_id="heldout", platform="sky130hd",
        cfg={"DESIGN_NAME": "heldout"},
        drc={"status": "violations", "total_violations": 7}, lvs={})
    assert strategies == []


def test_runtime_router_uses_tehm_backend(monkeypatch, tmp_tehm,
                                          sample_record_dict):
    conn, _, root = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    _promote(conn, rule_id)
    monkeypatch.setenv("R2G_MEMORY_BACKEND", "tehm")
    monkeypatch.setenv("TEHM_DB", str(root / "tehm.sqlite"))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(root / "artifacts"))
    strategies = signoff_strategies(
        project_dir=root, check="drc", design_id="heldout",
        platform="sky130hd", cfg={"DESIGN_NAME": "heldout"},
        reports={"drc": {"status": "violations"}})
    assert strategies
    assert strategies[0]["source"] == "tehm_rule"
    assert strategies[0]["activation_id"].startswith("activation_")


def test_backend_rebuild_enrolls_valid_rule_in_tehm_lifecycle(
        tmp_tehm, sample_record_dict):
    conn, _, root = tmp_tehm
    rule_id = _crystallize_drc_rule(tmp_tehm, sample_record_dict)
    from tehm_backend import TehmMemoryBackend
    backend = TehmMemoryBackend(
        db_path=root / "tehm.sqlite", artifact_root=root / "artifacts")
    report = backend.rebuild()
    assert report.ok
    row = conn.execute(
        "SELECT status, status_version FROM tehm_rule_status WHERE rule_id=?",
        (rule_id,)).fetchone()
    assert tuple(row) == ("candidate", 2)
    # Idempotent rebuild must not churn lifecycle versions.
    backend.rebuild()
    row2 = conn.execute(
        "SELECT status_version FROM tehm_rule_status WHERE rule_id=?", (rule_id,)
    ).fetchone()
    assert row2[0] == 2
    backend.close()


def test_backend_rebuild_keeps_unstable_rule_out_of_lifecycle(
        monkeypatch, tmp_tehm):
    conn, _, root = tmp_tehm
    import tehm.crystallization.build_rules as builder
    from tehm_backend import TehmMemoryBackend

    monkeypatch.setattr(builder, "crystallize_all", lambda _conn: [{
        "rule_id": "rule_unstable_audit_only",
        "validity_status": "UNSTABLE_CANDIDATE",
        "before_pattern": {"target_check": "route"},
    }])
    backend = TehmMemoryBackend(
        db_path=root / "tehm.sqlite", artifact_root=root / "artifacts")
    report = backend.rebuild()
    assert report.ok
    assert report.rebuilt["lifecycle_candidates_entered"] == 0
    assert report.rebuilt["lifecycle_inadmissible_skipped"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_status WHERE rule_id=?",
        ("rule_unstable_audit_only",)).fetchone()[0] == 0
    backend.close()
