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
