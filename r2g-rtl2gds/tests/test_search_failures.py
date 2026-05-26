"""Tests for search_failures.py: BM25 search over failure patterns."""
from __future__ import annotations

import json
from pathlib import Path

import search_failures


def test_bm25_ranks_exact_match_highest():
    """A document containing the exact query terms should rank highest."""
    docs = [
        {"id": "routing", "text": "GRT-0116 global routing congestion overflow"},
        {"id": "placement", "text": "NesterovSolve placement divergence overflow"},
        {"id": "synthesis", "text": "Yosys syntax error unexpected token"},
    ]
    index = search_failures.BM25Index(docs)
    # "overflow" appears in both routing and placement; routing also matches GRT-0116 and congestion
    results = index.search("GRT-0116 routing congestion overflow")
    assert results[0]["id"] == "routing"
    assert len(results) >= 2
    assert results[0]["score"] > results[1]["score"]


def test_bm25_returns_empty_for_no_match():
    docs = [{"id": "a", "text": "alpha beta gamma"}]
    index = search_failures.BM25Index(docs)
    results = index.search("zzz_nonexistent_term")
    assert len(results) == 0 or results[0]["score"] == 0.0


def test_parse_failure_patterns_md(tmp_path):
    """Parses failure-patterns.md sections into searchable documents."""
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## Routing Congestion (GRT-0116)\n\n"
        "**Symptoms:**\n"
        "- Global routing finished with congestion\n\n"
        "**Action:**\n"
        "- Reduce CORE_UTILIZATION\n\n"
        "## Placement Divergence\n\n"
        "**Symptoms:**\n"
        "- NesterovSolve overflow oscillates\n\n"
        "**Action:**\n"
        "- Raise PLACE_DENSITY_LB_ADDON\n"
    )
    docs = search_failures.parse_failure_patterns(md)
    assert len(docs) == 2
    assert docs[0]["id"] == "Routing Congestion (GRT-0116)"
    assert "GRT-0116" in docs[0]["text"]
    assert "Reduce CORE_UTILIZATION" in docs[0]["text"]


def test_parse_failure_candidates_json(tmp_path):
    """Parses failure_candidates.json into searchable documents."""
    fc = tmp_path / "failure_candidates.json"
    fc.write_text(json.dumps({
        "candidates": [
            {
                "signature": "pdn-0179",
                "occurrences": 5,
                "designs": ["black_parrot", "swerv_wrapper"],
                "stages": ["floorplan"],
                "sample_detail": "Unable to repair all channels.",
            },
        ],
    }))
    docs = search_failures.parse_failure_candidates(fc)
    assert len(docs) == 1
    assert docs[0]["id"] == "mined:pdn-0179"
    assert "floorplan" in docs[0]["text"]
    assert "Unable to repair" in docs[0]["text"]


def test_search_end_to_end(tmp_path):
    """Full pipeline: parse sources -> build index -> search."""
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## PDN Grid Error\n\n"
        "**Symptoms:**\n"
        "- PDN-0179 unable to repair channels\n"
        "- Insufficient width for straps\n\n"
        "**Action:**\n"
        "- Increase DIE_AREA or reduce CORE_UTILIZATION\n"
    )
    fc = tmp_path / "failure_candidates.json"
    fc.write_text(json.dumps({"candidates": []}))

    results = search_failures.search(
        query="PDN-0179 unable to repair",
        patterns_path=md,
        candidates_path=fc,
        top_k=3,
    )
    assert len(results) >= 1
    assert results[0]["id"] == "PDN Grid Error"
    assert results[0]["score"] > 0
