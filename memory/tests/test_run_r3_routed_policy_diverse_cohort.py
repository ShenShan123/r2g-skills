"""Tests for the explicit, manifest-free diverse routed-policy producer."""

import pytest

from scripts.run_r3_routed_policy_diverse_cohort import (
    _SPECS, _candidate, _cohort_tag, _route,
)


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


def test_tagged_cohort_namespace_is_content_and_identity_disjoint():
    fixture = "p3_obligation_recovery"
    base_route = _route(fixture)
    tagged_route = _route(fixture, "mir35a")
    base_candidate = _candidate(fixture, _SPECS[fixture])
    tagged_candidate = _candidate(fixture, _SPECS[fixture], "mir35a")
    assert base_route.routing_receipt_id != tagged_route.routing_receipt_id
    assert base_candidate.candidate_digest != tagged_candidate.candidate_digest
    assert _cohort_tag("mir35a") == "mir35a"
    with pytest.raises(ValueError, match="cohort_tag"):
        _cohort_tag("not/one")
