"""Joint rewrite anti-unification (design doc 6.6, 23.2, 23.3; test list 27.1).

Invariants under test:
  * deterministic output (same input -> identical result)
  * shared hole namespace (before/after holes never collide)
  * crystallization-time witnesses complete (every source -> every hole)
  * one hole per slot path (absorption), concrete stays concrete when shared
  * merge trace retained
"""
from __future__ import annotations

import pytest

from tehm.crystallization.anti_unify import (
    AntiUnifyConfig,
    anti_unify_rewrites,
)
from tehm.crystallization.role_normalize import RoleNormalizedRewrite


def _rw(transition_id, *, knob, value, check="drc", rerun="place",
        verdict="PASS", oracle="REGRESSION", episode_id=None, lineage=None):
    return RoleNormalizedRewrite(
        effect_key="effect_x",
        domain="flow.signoff",
        action_domain="signoff.REPAIR_ACTION",
        transformation_family="ANTENNA_DIODE_REPAIR",
        slots=(
            ("match.target_check", check),
            ("match.knob", knob),
            ("rewrite.value", value),
            ("execution.rerun_from", rerun),
            ("execution.recheck", check),
            ("verification.verdict", verdict),
            ("verification.oracle_type", oracle),
        ),
        obligations=("TARGET_FAILURE_REMOVED",),
        outcome="PASS",
        episode_id=episode_id or transition_id,
        transition_id=transition_id,
        lineage_id=lineage,
    )


def _holes(pattern: dict) -> set:
    return {v for v in pattern.values() if isinstance(v, str) and v.startswith("$H")}


def test_identical_rewrites_stay_concrete():
    r = anti_unify_rewrites([
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
    ])
    assert _holes(r.before_pattern) == set()
    assert _holes(r.after_pattern) == set()
    assert r.before_pattern["knob"] == "PLACE_DENSITY_LB_ADDON"
    assert r.after_pattern["rewrite.value"] == "0.14"


def test_different_value_creates_one_hole():
    r = anti_unify_rewrites([
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="PLACE_DENSITY_LB_ADDON", value="0.16"),
    ])
    # knob is shared -> concrete; value differs -> exactly one hole
    assert r.before_pattern["knob"] == "PLACE_DENSITY_LB_ADDON"
    holes = _holes(r.after_pattern)
    assert holes == {"$H0"}
    assert r.after_pattern["rewrite.value"] == "$H0"


def test_different_knob_holed_and_before_after_namespace_shared():
    r = anti_unify_rewrites([
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="ROUTE_DENSITY_LAYER_ADDON", value="0.10"),
    ])
    # knob (before/match) and value (after/rewrite) become DIFFERENT holes —
    # before/after share one hole namespace but never collide.
    before_holes = _holes(r.before_pattern)
    after_holes = _holes(r.after_pattern)
    assert before_holes == {"$H0"}
    assert after_holes == {"$H1"}
    assert before_holes.isdisjoint(after_holes)


def test_substitution_witnesses_complete():
    examples = [
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="ROUTE_DENSITY_LAYER_ADDON", value="0.10"),
        _rw("t3", knob="CORE_UTILIZATION", value="14"),
    ]
    r = anti_unify_rewrites(examples)
    holes = _holes(r.before_pattern) | _holes(r.after_pattern)
    assert holes  # at least one hole
    for source in ("t1", "t2", "t3"):
        subs = r.source_substitutions[source]
        for hole in holes:
            assert hole in subs, f"{source} missing witness for {hole}"
    assert r.source_substitutions["t1"]["$H0"] == "PLACE_DENSITY_LB_ADDON"
    assert r.source_substitutions["t3"]["$H1"] == "14"


def test_absorption_one_hole_per_slot():
    """Three different knobs -> the knob path keeps ONE hole with 3 witnesses."""
    r = anti_unify_rewrites([
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="ROUTE_DENSITY_LAYER_ADDON", value="0.10"),
        _rw("t3", knob="CORE_UTILIZATION", value="14"),
    ])
    assert len(_holes(r.before_pattern)) == 1
    knob_hole = next(iter(_holes(r.before_pattern)))
    witnesses = sorted({r.source_substitutions[s][knob_hole]
                        for s in ("t1", "t2", "t3")})
    assert witnesses == ["CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON",
                         "ROUTE_DENSITY_LAYER_ADDON"]
    # hole constraints record the same observed set
    constraint = r.hole_constraints[knob_hole]
    assert sorted(constraint["observed_values"]) == witnesses


def test_merge_trace_retained():
    r = anti_unify_rewrites([
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="ROUTE_DENSITY_LAYER_ADDON", value="0.10"),
    ])
    assert len(r.merge_trace) == 1
    step = r.merge_trace[0]
    assert step.cost == 2
    assert sorted(step.pair) == ["t1", "t2"]
    assert len(step.created_holes) == 2


def test_deterministic():
    examples = [
        _rw("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _rw("t2", knob="ROUTE_DENSITY_LAYER_ADDON", value="0.10"),
    ]
    r1 = anti_unify_rewrites(examples, AntiUnifyConfig())
    r2 = anti_unify_rewrites(examples, AntiUnifyConfig())
    assert r1.to_dict() == r2.to_dict()


def test_insufficient_examples_raises():
    with pytest.raises(ValueError, match="needs >= 2 examples"):
        anti_unify_rewrites([_rw("t1", knob="K", value="v")])


def test_algorithm_version_stamped():
    r = anti_unify_rewrites([
        _rw("t1", knob="K", value="1"),
        _rw("t2", knob="K", value="2"),
    ])
    assert r.algorithm_version.startswith("joint-au-")
