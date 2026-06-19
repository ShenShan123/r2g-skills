"""Tests for query_knowledge.py."""
from __future__ import annotations

import json

import query_knowledge


def _write(tmp_knowledge_dir, payload: dict):
    (tmp_knowledge_dir / "heuristics.json").write_text(json.dumps(payload))


def test_get_family_heuristics_hit(tmp_knowledge_dir):
    _write(tmp_knowledge_dir, {
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10, "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 30, "median": 25},
                        "place_density_lb_addon": {"min_safe": 0.15, "median": 0.20},
                    },
                },
            },
        },
    })
    result = query_knowledge.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is not None
    assert result["core_utilization"]["median"] == 25
    assert result["sample_size"] == 10


def test_get_family_heuristics_miss(tmp_knowledge_dir):
    _write(tmp_knowledge_dir, {"families": {}})
    result = query_knowledge.get_family_heuristics(
        "nonexistent", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None


def test_get_family_heuristics_no_file(tmp_knowledge_dir):
    result = query_knowledge.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None


def test_get_deterioration_and_closing_period(tmp_path):
    import query_knowledge, json as _json
    h = tmp_path / "heuristics.json"
    h.write_text(_json.dumps({"families": {"alu": {"platforms": {"nangate45": {
        "closing_period": {"min": 7.4, "median": 8.5, "n": 4},
        "slack_deterioration": {"d_fp_pl": {"ns_p90": 0.3, "pct_p90": 0.03},
                                "d_pl_fin": {"ns_p90": 0.2, "pct_p90": 0.02},
                                "n": 4},
    }}}}}), encoding="utf-8")
    assert query_knowledge.get_closing_period("alu", "nangate45", heuristics_path=h)["min"] == 7.4
    assert query_knowledge.get_deterioration("alu", "nangate45", heuristics_path=h)["n"] == 4
    assert query_knowledge.get_deterioration("nope", "nangate45", heuristics_path=h) is None
