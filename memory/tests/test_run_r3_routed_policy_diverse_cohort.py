"""Tests for the explicit, manifest-free diverse routed-policy producer."""

from scripts.run_r3_routed_policy_diverse_cohort import _SPECS, _candidate, _route


def test_diverse_specs_are_typed_and_route_consider():
    assert len(_SPECS) == 14
    for fixture, spec in _SPECS.items():
        route = _route(fixture)
        candidate = _candidate(fixture, spec)
        assert route.decision == "CONSIDER"
        assert route.selected_asset_ids == (f"r3-diverse-asset-{fixture}",)
        assert candidate.concrete_action["domain"] == spec["domain"]
        assert candidate.concrete_action["transformation_family"] == spec["family"]
        assert candidate.provenance["evaluation_only"] is True
