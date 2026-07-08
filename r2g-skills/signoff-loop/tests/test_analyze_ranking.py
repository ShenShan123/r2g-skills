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
