"""UNKNOWN != FALSE (design doc honesty H3, invariant 23.3).

A missing observation yields UNKNOWN — never negative evidence. The honesty gate
re-extracts predicates from stored semantic views and fails on any fabricated
FALSE from an insufficient observation.
"""
from __future__ import annotations

from tehm import honesty
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.graph.local_design_graph import LocalDesignGraph, build_run_context_graph
from tehm.graph.predicates import (
    PredicateObservation,
    coverage,
    extract_predicates,
    support,
)


def test_empty_graph_returns_unknown_not_false():
    g = LocalDesignGraph()
    snap = extract_predicates(g)
    for name, obs in snap.observations.items():
        assert obs.value in ("TRUE", "FALSE", "UNKNOWN")
    assert snap.value_of("single_clock_domain") == "UNKNOWN"
    assert snap.value_of("drc_clean") == "UNKNOWN"


def test_insufficient_observation_never_false():
    """An 'unobserved' predicate must never be coerced to FALSE."""
    g = build_run_context_graph({}, {})
    snap = extract_predicates(g)
    for name, obs in snap.observations.items():
        if obs.coverage_scope == "insufficient_observation":
            assert obs.value != "FALSE", f"{name} fabricated a FALSE"


def test_failed_check_true_and_clean_false():
    g = build_run_context_graph(
        {"drc": {"status": "violations", "total_violations": 3}},
        {"PLATFORM": "sky130hd"},
    )
    snap = extract_predicates(g)
    assert snap.value_of("target_check_failed") == "TRUE"
    assert snap.value_of("drc_clean") == "FALSE"
    assert snap.value_of("lvs_clean") == "UNKNOWN"  # lvs never ran


def test_h3_gate_green_on_captured_store(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture(conn, store, ExecutionRecord.from_dict(sample_record_dict))
    ok, detail = honesty.h3_unknown_not_false(conn)
    assert ok, detail


def test_support_and_coverage_math():
    values = ["TRUE", "TRUE", "FALSE", "UNKNOWN"]
    assert support(values) == 2 / 3
    assert coverage(values) == 3 / 4
    assert support(["UNKNOWN", "UNKNOWN"]) is None
    assert coverage(["UNKNOWN", "UNKNOWN"]) == 0.0


def test_predicate_observation_validate():
    import pytest
    with pytest.raises(ValueError):
        PredicateObservation(value="MAYBE")
