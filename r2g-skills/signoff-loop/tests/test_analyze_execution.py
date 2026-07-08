"""Tests for analyze_execution.py: structured fix proposals from failed runs."""
from __future__ import annotations

import json
from pathlib import Path

import analyze_execution


def _make_failed_project(tmp_path, name="failed_run", fail_stage="floorplan",
                          diagnosis_issues=None):
    """Create a project directory that represents a failed ORFS run."""
    project = tmp_path / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "backend").mkdir(parents=True)

    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = test_design\n"
        "export PLATFORM = nangate45\n"
        "export CORE_UTILIZATION = 45\n"
        "export PLACE_DENSITY_LB_ADDON = 0.05\n"
    )
    (project / "reports" / "ppa.json").write_text(json.dumps({
        "summary": {"timing": {}, "power": {}, "area": {}},
        "geometry": {"instance_count": 50000},
    }))
    (project / "reports" / "diagnosis.json").write_text(json.dumps({
        "issues": diagnosis_issues or [
            {"kind": "placement_utilization_overflow", "stage": "floorplan",
             "summary": "Utilization exceeds 100% target"},
        ],
    }))
    (project / "reports" / "timing_check.json").write_text(
        json.dumps({"tier": "clean"}))
    stages = [
        {"stage": "synth", "status": "pass", "elapsed_s": 60},
        {"stage": fail_stage, "status": "fail", "elapsed_s": 30},
    ]
    (project / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")
    return project


def test_produces_fix_proposals_for_utilization_overflow(tmp_path):
    """A utilization overflow failure should propose reducing CORE_UTILIZATION."""
    project = _make_failed_project(tmp_path)
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## Placement Utilization Overflow\n\n"
        "**Symptoms:**\n- Utilization exceeds target\n\n"
        "**Action:**\n- Reduce CORE_UTILIZATION by 30-50%\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert result["status"] == "fail"
    assert result["fail_stage"] == "floorplan"
    assert len(result["proposals"]) >= 1
    proposal = result["proposals"][0]
    assert proposal["parameter"] == "CORE_UTILIZATION"
    assert proposal["current"] == "45"
    assert int(proposal["suggested"]) < 45
    assert proposal["confidence"] in ("high", "medium", "low")


def test_produces_density_fix_for_placement_divergence(tmp_path):
    """A placement divergence failure should propose raising PLACE_DENSITY_LB_ADDON."""
    project = _make_failed_project(
        tmp_path, fail_stage="place",
        diagnosis_issues=[
            {"kind": "placement_divergence", "stage": "place",
             "summary": "NesterovSolve overflow oscillates without convergence"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## Placement Divergence (NesterovSolve Non-Convergence)\n\n"
        "**Symptoms:**\n- NesterovSolve overflow oscillates\n\n"
        "**Action:**\n- Raise PLACE_DENSITY_LB_ADDON to at least 0.20\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert len(result["proposals"]) >= 1
    density_proposals = [p for p in result["proposals"]
                          if p["parameter"] == "PLACE_DENSITY_LB_ADDON"]
    assert len(density_proposals) >= 1
    assert float(density_proposals[0]["suggested"]) >= 0.20


def test_returns_no_proposals_for_unknown_failure(tmp_path):
    """An unrecognized failure kind should still return a result, just with no proposals."""
    project = _make_failed_project(
        tmp_path, fail_stage="synth",
        diagnosis_issues=[
            {"kind": "mystery_error_xyz", "stage": "synth",
             "summary": "Something completely unknown happened"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text("# Failure Patterns\n")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert result["status"] == "fail"
    assert isinstance(result["proposals"], list)
    assert len(result["similar_failures"]) == 0


def test_includes_similar_failures_in_output(tmp_path):
    """Similar failure search results should be included for agent context."""
    project = _make_failed_project(
        tmp_path,
        diagnosis_issues=[
            {"kind": "pdn-0179", "stage": "floorplan",
             "summary": "PDN-0179: Unable to repair all channels"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## PDN Grid Error\n\n"
        "**Symptoms:**\n- PDN-0179 unable to repair channels\n\n"
        "**Action:**\n"
        "- Increase DIE_AREA\n"
        "- Reduce CORE_UTILIZATION\n"
        "- Remove SYNTH_HIERARCHICAL\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert len(result["similar_failures"]) >= 1
    assert result["similar_failures"][0]["id"] == "PDN Grid Error"
