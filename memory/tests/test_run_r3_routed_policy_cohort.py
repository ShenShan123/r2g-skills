"""Regression coverage for the routed-policy producer's source modes."""
from __future__ import annotations

from scripts.run_r3_routed_policy_cohort import _route, run


def test_consider_route_allocates_one_selected_memory_slot():
    route = _route("req_ack_bug3", "CONSIDER")

    assert route.decision == "CONSIDER"
    assert route.memory_budget == 1
    assert route.no_memory_budget == 1
    assert route.selected_asset_ids == ("r3-guard-strengthen",)
    assert route.no_skill_reason is None


def test_consider_producer_keeps_buggy_source_and_executes_causal_memory(tmp_path):
    summary = run(
        tmp_path / "r3-consider",
        ["req_ack_bug3"],
        routing_decision="CONSIDER",
    )

    assert summary["source_mode"] == "buggy_selected_memory"
    assert summary["selected_memory_arms"] == 3
    assert summary["outcome_counts"]["NO_MEMORY"]["FAIL"] == 1
    assert summary["outcome_counts"]["CAUSAL_NO_SKILL"]["PASS"] == 1
