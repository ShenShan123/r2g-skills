"""Rule validity audit (design doc 7, 24.1, 26 Phase 6, test list 27.1).

Ordered V2 -> V1 -> V3 -> V4:
  * degenerate rules (wildcard collapse / instance memorization) rejected by V2
  * V1 replays ONLY the crystallization-time witnesses (no binding re-search)
  * V3 reports the support profile (lineage diversity gates cross-lineage claims)
  * V4 is N/A for n < 3 (never a FAIL); leave-one-out decides VALIDATED vs UNSTABLE
"""
from __future__ import annotations

from tehm.crystallization.anti_unify import AntiUnifyConfig, anti_unify_rewrites
from tehm.crystallization.role_normalize import normalize_rewrite
from tehm.crystallization.synthesize_skill import synthesize_skill
from tehm.crystallization.validity import (
    ADMISSIBLE_FOR_LIFECYCLE,
    ValidityConfig,
    audit_rule,
)


def _transition(tid, *, knob, value, check="drc", rerun="place",
                verdict="PASS", oracle="REGRESSION", lineage="lineage_a",
                created_regressions=None) -> dict:
    return {
        "transition_id": tid,
        "source_state_id": f"state_{tid}",
        "lineage_id": lineage,
        "action": {
            "domain": "signoff.REPAIR_ACTION",
            "transformation_family": "ANTENNA_DIODE_REPAIR",
            "payload": {"config_edits": {knob: value}, "rerun_from": rerun,
                        "recheck": check, "dependency_cone_changed": True},
        },
        "observation_delta": {
            "original_failure": "REMOVED",
            "first_divergence": {"before": 10, "after": None},
            "failing_tests": {"before": 1, "after": 0},
            "created_regressions": created_regressions or [],
            "newly_observed_failures": [],
        },
        "verifier": {"verdict": verdict, "oracle_type": oracle,
                     "scope": f"signoff:{check}", "confidence_tier": "R",
                     "obligation_coverage": 1.0, "evidence_refs": []},
        "outcome": "PASS",
        "provenance": {},
    }


def _make_rule(transitions: list[dict]) -> dict:
    rewrites = [normalize_rewrite(t) for t in transitions]
    result = anti_unify_rewrites(rewrites, AntiUnifyConfig())
    return synthesize_skill(
        result, domain="flow.signoff",
        transformation_family=transitions[0]["action"]["transformation_family"],
        obligations=tuple(sorted({o for r in rewrites for o in r.obligations})),
        source_episodes=sorted({r.episode_id for r in rewrites}))


def test_validated_rule():
    ts = [_transition(f"t{i}", knob="PLACE_DENSITY_LB_ADDON", value=f"0.1{i}",
                      lineage=f"lineage_{i}") for i in range(3)]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "VALIDATED"
    names = [g.name for g in result.gates]
    assert names == ["V2", "V1", "V3", "V4"]
    assert all(g.ok for g in result.gates)


def test_provisional_when_n_lt_3():
    ts = [_transition(f"t{i}", knob="K", value=f"v{i}", lineage=f"lineage_{i}")
          for i in range(2)]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "PROVISIONAL_VALID"
    v4 = result.gates[-1]
    assert v4.name == "V4"
    assert v4.ok is None            # N/A, NOT a failure (design doc 7.5)
    assert v4.detail["status"] == "N/A"


def test_wildcard_collapse_rejected():
    """All slots holed -> over-abstraction -> V2 REJECT_DEGENERATE."""
    ts = [
        _transition("t1", knob="K1", value="v1", check="drc", rerun="place",
                    verdict="PASS", oracle="REGRESSION"),
        _transition("t2", knob="K2", value="v2", check="lvs", rerun="route",
                    verdict="FAIL", oracle="TARGET_TEST"),
    ]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "REJECT_DEGENERATE"
    assert result.gates[0].name == "V2"
    assert "wildcard_collapse" in result.gates[0].detail["flags"]
    # V1 was never consulted (honesty H5: V2 before V1)
    assert len(result.gates) == 1


def test_instance_memorization_rejected():
    """Two identical episodes -> no abstraction -> V2 REJECT_DEGENERATE."""
    ts = [
        _transition("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
        _transition("t2", knob="PLACE_DENSITY_LB_ADDON", value="0.14"),
    ]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "REJECT_DEGENERATE"
    assert "instance_memorization" in result.gates[0].detail["flags"]


def test_concrete_repeat_with_support_three_passes_v2():
    """0 holes with support >= 3 is a strong repeated pattern, not memorization."""
    ts = [
        _transition("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.14", lineage="l1"),
        _transition("t2", knob="PLACE_DENSITY_LB_ADDON", value="0.14", lineage="l2"),
        _transition("t3", knob="PLACE_DENSITY_LB_ADDON", value="0.14", lineage="l3"),
    ]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.gates[0].ok is True
    assert "concrete_repeat" in result.gates[0].detail["flags"]


def test_v1_detects_tampered_witness():
    ts = [_transition(f"t{i}", knob="PLACE_DENSITY_LB_ADDON", value=f"0.1{i}",
                      lineage=f"lineage_{i}") for i in range(3)]
    rule = _make_rule(ts)
    # Tamper one source's witness: replay must fail (no re-binding allowed).
    hole = next(h for h in rule["provenance"]["source_substitutions"]["t0"])
    rule["provenance"]["source_substitutions"]["t0"][hole] = "TAMPERED"
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "REJECT_UNFAITHFUL"
    assert result.gates[1].name == "V1"
    assert not result.gates[1].ok


def test_v3_insufficient_support():
    ts = [_transition(f"t{i}", knob="PLACE_DENSITY_LB_ADDON", value=f"0.1{i}")
          for i in range(2)]
    rule = _make_rule(ts)
    cfg = ValidityConfig(min_group_size=3)   # raise the bar above support=2
    result = audit_rule(rule, source_transitions=ts, config=cfg)
    assert result.status == "INSTANCE_MEMORY"
    assert result.gates[2].name == "V3"
    assert not result.gates[2].ok


def test_v4_unstable_when_held_out_does_not_fit():
    """t2 uses a different rerun stage; leaving it out makes r_{-t2} concrete
    'place', which cannot explain 'route' -> UNSTABLE_CANDIDATE."""
    ts = [
        _transition("t0", knob="PLACE_DENSITY_LB_ADDON", value="0.14", rerun="place", lineage="l0"),
        _transition("t1", knob="PLACE_DENSITY_LB_ADDON", value="0.16", rerun="place", lineage="l1"),
        _transition("t2", knob="PLACE_DENSITY_LB_ADDON", value="0.18", rerun="route", lineage="l2"),
    ]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    assert result.status == "UNSTABLE_CANDIDATE"
    v4 = result.gates[-1]
    assert v4.name == "V4"
    assert not v4.ok
    assert "t2" in v4.detail["failures"]


def test_v3_cross_lineage_flag():
    ts = [_transition(f"t{i}", knob="K", value=f"v{i}", lineage=f"lineage_{i}")
          for i in range(3)]
    rule = _make_rule(ts)
    result = audit_rule(rule, source_transitions=ts)
    v3 = result.gates[2]
    assert v3.detail["unique_lineages"] == 3
    assert v3.detail["cross_lineage"] is True


def test_lifecycle_admissible_statuses():
    assert set(ADMISSIBLE_FOR_LIFECYCLE) == {"PROVISIONAL_VALID", "VALIDATED"}
