"""analyze_execution.rank_proposals reorders backend-stage proposal ids by
learned recipes (Tier-3 fix_recipes[orfs][stage]); cold start preserves order."""
from __future__ import annotations

import json

import analyze_execution


def test_backend_proposals_reranked_by_history(tmp_knowledge_dir, monkeypatch):
    # build a heuristics.json with a winning recipe for ('orfs','place')
    h = tmp_knowledge_dir / "heuristics.json"
    h.write_text(json.dumps({"families": {"aes": {"platforms": {"nangate45": {
        "fix_recipes": {"orfs": {"place": {"strategies": {
            "place_density_relief": {"attempts": 6, "successes": 5, "failures": 1}},
            "n_sessions": 6}}}}}}}}))
    ranked = analyze_execution.rank_proposals(
        ["util_reduce", "place_density_relief"],
        family="aes", platform="nangate45", stage="place", heuristics_path=h)
    assert ranked[0] == "place_density_relief"


def test_cold_start_preserves_proposal_order(tmp_knowledge_dir):
    # No fix_recipes for this family/platform/stage -> static order preserved.
    h = tmp_knowledge_dir / "heuristics.json"
    h.write_text(json.dumps({"families": {}}))
    ids = ["util_reduce", "place_density_relief", "pdn_relief"]
    ranked = analyze_execution.rank_proposals(
        ids, family="aes", platform="nangate45", stage="place", heuristics_path=h)
    assert ranked == ids


def test_missing_heuristics_file_preserves_order(tmp_knowledge_dir):
    # heuristics.json absent entirely -> cold start, order preserved.
    h = tmp_knowledge_dir / "does_not_exist.json"
    ids = ["util_reduce", "place_density_relief"]
    ranked = analyze_execution.rank_proposals(
        ids, family="aes", platform="nangate45", stage="place", heuristics_path=h)
    assert ranked == ids


def test_canonical_orfs_stage_key_is_consumed(tmp_knowledge_dir):
    """Recipes live under the CANONICAL fix_recipes['orfs_stage'][stage] family
    (ingest keys check='orfs_stage'). Before failure-patterns #43 the ranker read
    the wrong 'orfs' family, so these 91 stage recipes were unreachable."""
    h = tmp_knowledge_dir / "heuristics.json"
    h.write_text(json.dumps({"families": {"aes": {"platforms": {"nangate45": {
        "fix_recipes": {"orfs_stage": {"place": {"strategies": {
            "core_util_relief": {"attempts": 6, "successes": 5, "failures": 1}},
            "n_sessions": 6}}}}}}}}))
    ranked = analyze_execution.rank_proposals(
        ["util_reduce", "core_util_relief"],
        family="aes", platform="nangate45", stage="place", heuristics_path=h)
    assert ranked[0] == "core_util_relief"


def test_analyze_surfaces_learned_stage_ranking(tmp_path, monkeypatch):
    """analyze() attaches the learned orfs_stage recipe ranking for the fail_stage
    so the operator review queue actually CONSUMES the recipes (failure-patterns
    #43 — before wiring, these recipes had no reader)."""
    import knowledge_db
    # Point the heuristics/candidates lookup at a temp knowledge dir.
    kd = tmp_path / "knowledge"
    kd.mkdir()
    (kd / "families.json").write_text(json.dumps({}))
    # Family resolves from the project-dir basename via infer_family -> "aes".
    (kd / "heuristics.json").write_text(json.dumps({"families": {"aes": {
        "platforms": {"nangate45": {"fix_recipes": {"orfs_stage": {"route": {
            "strategies": {"route_relief": {"attempts": 8, "successes": 7,
                                            "failures": 1}},
            "n_sessions": 8}}}}}}}}))
    monkeypatch.setattr(knowledge_db, "DEFAULT_KNOWLEDGE_DIR", kd)
    monkeypatch.setattr(analyze_execution, "_CANDIDATES_PATH",
                        kd / "failure_candidates.json")

    project = tmp_path / "aes"
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "backend").mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = aes\nexport PLATFORM = nangate45\n")
    (project / "backend" / "stage_log.jsonl").write_text(
        json.dumps({"stage": "synth", "status": 0}) + "\n"
        + json.dumps({"stage": "route", "status": 1}) + "\n")

    result = analyze_execution.analyze(project)
    assert result["status"] == "fail" and result["fail_stage"] == "route"
    ranked = result["learned_stage_ranking"]
    assert ranked and ranked[0]["strategy"] == "route_relief"
