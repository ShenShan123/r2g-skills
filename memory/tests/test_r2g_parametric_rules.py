"""R2G memory redesign B1-B4 (memory/evaluation/r2g_memory_redesign_contract_20261001.md).

B1  only a measured recheck of an applied fix is a complete oracle;
B2  every knob is kept, with a per-knob delta, so a rule transfers across designs;
B3  shared situation fields become hard preconditions the symbolic filter evaluates;
B4  FAIL-derived rules, empty edits and unfilled knobs never become strategies.
"""
from __future__ import annotations

from pathlib import Path

import runtime_router
import tehm_backend
from contracts import MemoryQuery, RepairContext
from tehm.activation.instantiate import instantiate_rewrite
from tehm.adapters.r2g_evidence import (build_execution_records, measured_outcome,
                                        normalize_knob_edits)
from tehm.crystallization.anti_unify import AntiUnifyConfig, anti_unify_rewrites
from tehm.crystallization.role_normalize import normalize_rewrite
from tehm.crystallization.synthesize_skill import synthesize_skill
from tehm.retrieval.result import APPLICABLE, INAPPLICABLE, UNRESOLVED
from tehm.retrieval.symbolic_filter import apply_symbolic_filter

SIT = {"v": "sit-v1", "check": "drc", "violation_class": "li.3", "error_code": None,
       "timeout": False, "platform": "sky130hs", "die_mode": "auto",
       "util_band": "le12", "count_band": "1-5"}


def _row(**kw):
    row = {"fix_session_id": "s", "iter": 1, "check": "drc", "strategy": "density_relief",
           "violation_class": "li.3", "from_stage": "floorplan", "before": 4, "after": 0,
           "verdict": "cleared", "config_delta": {"CORE_UTILIZATION": "8"},
           "config_before": {"CORE_UTILIZATION": "12"}, "situation": dict(SIT)}
    row.update(kw)
    return row


def _transition(row, design, tid):
    ev = {"project": Path("/nonexistent") / design, "config": {"DESIGN_NAME": design,
          "PLATFORM": "sky130hs"}, "reports": {}, "stage_log": [], "fix_log": [row]}
    rec = build_execution_records(ev)[0]
    return {"transition_id": tid, "action": rec.action,
            "observation_delta": rec.observation_delta, "verifier": rec.verification,
            "outcome": "PASS", "domain": "flow.signoff"}


# -- B1 ------------------------------------------------------------------------

def test_measured_outcome_matrix():
    assert measured_outcome(_row())["verdict"] == "PASS"
    part = measured_outcome(_row(after=2, verdict="applied"))
    assert part["verdict"] == "PASS" and part["original_failure"] == "PRESENT"
    assert measured_outcome(_row(after=4, verdict="no_improvement"))["verdict"] == "FAIL"
    reg = measured_outcome(_row(after=6, verdict="regression"))
    assert reg["verdict"] == "FAIL" and reg["target_regression"] is True
    # a measured global regression is FAIL even when the target improved
    assert measured_outcome(_row(after=2, verdict="regression",
                                 global_regressions=["route:0->32"]))["verdict"] == "FAIL"
    # unmeasured iterations stay non-crystallisable
    for bad in (_row(verdict="stop_residual", strategy="none"),
                _row(verdict="apply_failed"), _row(verdict="rerun_failed_rc2"),
                _row(before=None, after=None)):
        assert measured_outcome(bad) is None


def test_adapter_marks_only_measured_rows_oracle_complete():
    ok = _transition(_row(), "a", "t1")
    assert ok["verifier"]["oracle_complete"] is True and ok["verifier"]["verdict"] == "PASS"
    assert ok["action"]["payload"]["situation"] == SIT
    bad = _transition(_row(verdict="rerun_failed_rc2", after=None), "a", "t2")
    assert bad["verifier"]["oracle_complete"] is False
    reg = _transition(_row(after=6, verdict="regression"), "a", "t3")
    assert reg["verifier"]["target_regression"] is True
    assert "drc_target_regression:4->6" in reg["observation_delta"]["created_regressions"]


def test_knob_edit_shapes_normalize():
    assert normalize_knob_edits({"CORE_UTILIZATION": "8"}, {"CORE_UTILIZATION": "12"}) == \
        {"CORE_UTILIZATION": {"before": "12", "after": "8"}}
    nested = {"CORE_UTILIZATION": {"before": "70", "after": None},
              "DIE_AREA": {"before": None, "after": "0 0 302 302"}}
    assert normalize_knob_edits(nested)["DIE_AREA"]["after"] == "0 0 302 302"


# -- B2 + B3 crystallisation ---------------------------------------------------

def _rule(rows_designs):
    trans = [_transition(r, d, f"t{i}") for i, (r, d) in enumerate(rows_designs)]
    rewrites = [normalize_rewrite(t, effect_key="k", episode_id=t["transition_id"])
                for t in trans]
    result = anti_unify_rewrites(rewrites, AntiUnifyConfig(min_group_size=2))
    return synthesize_skill(result, domain="flow.signoff",
                            transformation_family="DENSITY_RELIEF",
                            action_domain="signoff.REPAIR_ACTION", obligations=(),
                            source_episodes=[t["transition_id"] for t in trans])


def test_multi_knob_delta_rule_transfers_and_keeps_shared_situation():
    a = _row()                                                     # 12 -> 8
    b = _row(config_delta={"CORE_UTILIZATION": "13"},              # 17 -> 13
             config_before={"CORE_UTILIZATION": "17"},
             situation={**SIT, "util_band": "13-30"})
    rule = _rule([(a, "des"), (b, "sha")])
    after = rule["after_pattern"]
    assert after["rewrite.knob.CORE_UTILIZATION.abs"].startswith("$H")   # 8 vs 13
    assert after["rewrite.knob.CORE_UTILIZATION.delta"] == "-4"         # shared
    pre = set(rule["hard_preconditions"])
    assert {"situation.violation_class==li.3", "situation.platform==sky130hs"} <= pre
    assert not any(x.startswith("situation.util_band") for x in pre)     # differed
    # instantiation applies the delta to THIS design's current value
    ctx = RepairContext(check="drc", cfg={"CORE_UTILIZATION": "40"}, situation=SIT)
    action = instantiate_rewrite(rule, None, ctx)
    assert action["payload"]["config_edits"] == {"CORE_UTILIZATION": "36"}
    # without the knob in the current config the edit is unresolved, not guessed
    action2 = instantiate_rewrite(rule, None, RepairContext(check="drc", cfg={}))
    assert action2["payload"]["unresolved_knobs"] == ["CORE_UTILIZATION"]


def test_differing_knob_values_and_unknown_before_stay_unfilled():
    a = _row(config_before={})                                     # before unknown
    b = _row(config_delta={"CORE_UTILIZATION": "13"}, config_before={})
    rule = _rule([(a, "des"), (b, "sha")])
    action = instantiate_rewrite(rule, None, RepairContext(
        check="drc", cfg={"CORE_UTILIZATION": "40"}))
    assert action["payload"]["unresolved_knobs"] == ["CORE_UTILIZATION"]


def test_rules_without_situation_keep_empty_preconditions():
    a, b = _row(situation=None), _row(situation=None, config_delta={"CORE_UTILIZATION": "13"},
                                      config_before={"CORE_UTILIZATION": "17"})
    assert _rule([(a, "des"), (b, "sha")])["hard_preconditions"] == []


# -- B3 filter -----------------------------------------------------------------

def _q(situation):
    plan = {"check": "drc"}
    if situation is not None:
        plan["situation"] = situation
    return MemoryQuery(query_plan=plan)


def test_symbolic_filter_evaluates_situation_preconditions():
    rule = {"before_pattern": {"target_check": "drc"},
            "hard_preconditions": ["situation.util_band==le12"]}
    assert apply_symbolic_filter(rule, _q(SIT)) == APPLICABLE
    assert apply_symbolic_filter(rule, _q({**SIT, "util_band": "gt60"})) == INAPPLICABLE
    assert apply_symbolic_filter(rule, _q(None)) == UNRESOLVED
    assert apply_symbolic_filter(rule, _q({k: v for k, v in SIT.items()
                                           if k != "util_band"})) == UNRESOLVED
    odd = {"before_pattern": {"target_check": "drc"},
           "hard_preconditions": ["design_type==crypto"]}
    assert apply_symbolic_filter(odd, _q(SIT)) == UNRESOLVED


def test_query_plan_and_context_digest_unchanged_without_situation():
    from tehm.retrieval.query_planner import plan_query
    ctx = RepairContext(check="drc")
    assert "situation" not in plan_query(ctx).query_plan
    assert "situation" not in ctx.to_dict()


# -- B4 ------------------------------------------------------------------------

def test_fail_rules_are_not_repairs():
    assert tehm_backend.is_repair_rule({"domain": "flow.signoff",
                                        "after_pattern": {"verification.verdict": "PASS"}})
    assert not tehm_backend.is_repair_rule({"domain": "flow.signoff",
                                            "after_pattern": {"verification.verdict": "FAIL"}})
    assert tehm_backend.is_repair_rule({"domain": "rtl", "after_pattern": {}})


def test_router_rejects_empty_and_unfilled_strategies():
    assert runtime_router._reject_reason({"config_edits": {}}, {}) == "empty config_edits"
    assert "unfilled" in runtime_router._reject_reason(
        {"config_edits": {}}, {"unresolved_knobs": ["CORE_UTILIZATION"]})
    assert "hole" in runtime_router._reject_reason({"config_edits": {"X": "$H1"}}, {})
    assert runtime_router._reject_reason({"config_edits": {"X": "1"}}, {}) is None


def test_scalar_category_snapshots_do_not_break_capture(tmp_path):
    """fix_signoff.sh logs a route abort with before_categories {"total_violations": null};
    capture must keep only per-rule (dict) categories (Phase D1 crash, 2026-10-01)."""
    import json
    from tehm_backend import TehmMemoryBackend
    proj = tmp_path / "chacha"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = chacha\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 17\n")
    row = {"fix_session_id": "s", "iter": 1, "check": "orfs_stage", "strategy": "route_relief",
           "violation_class": "route", "from_stage": "route", "before": 1, "after": 1,
           "verdict": "no_improvement", "config_delta": {"CORE_UTILIZATION": "12"},
           "before_categories": json.dumps({"total_violations": None})}
    (proj / "reports" / "fix_log.jsonl").write_text(json.dumps(row) + "\n")
    b = TehmMemoryBackend(db_path=tmp_path / "t.sqlite", artifact_root=tmp_path / "a")
    try:
        assert len(b.ingest_project(proj)) == 1
    finally:
        b.close()


def test_numeric_spellings_do_not_hole():
    """'0.2' vs '0.20' is one setting (Phase D1: a spurious hole made a rule unexecutable)."""
    a = _row(config_delta={"ROUTING_LAYER_ADJUSTMENT": "0.2"},
             config_before={"ROUTING_LAYER_ADJUSTMENT": "0"})
    b = _row(config_delta={"ROUTING_LAYER_ADJUSTMENT": "0.20"},
             config_before={"ROUTING_LAYER_ADJUSTMENT": "0.0"})
    rule = _rule([(a, "des"), (b, "sha")])
    assert rule["after_pattern"]["rewrite.knob.ROUTING_LAYER_ADJUSTMENT.abs"] == "0.2"


# -- Phase D amendment D-A1 ------------------------------------------------------

def test_witness_median_fills_a_holed_delta():
    """Verified fixes in one situation disagree on the value (85->45 and 85->55):
    instantiation takes the median verified delta, marked witness_selected."""
    a = _row(config_delta={"CORE_UTILIZATION": "45"}, config_before={"CORE_UTILIZATION": "85"})
    b = _row(config_delta={"CORE_UTILIZATION": "55"}, config_before={"CORE_UTILIZATION": "85"})
    rule = _rule([(a, "des"), (b, "sha")])
    after = rule["after_pattern"]
    dhole = after["rewrite.knob.CORE_UTILIZATION.delta"]
    assert dhole.startswith("$H")
    wit = {h: [subs[h] for subs in rule["provenance"]["source_substitutions"].values() if h in subs]
           for h in rule["provenance"]["hole_constraints"]}
    ctx = RepairContext(check="drc", cfg={"CORE_UTILIZATION": "80"}, situation=SIT)
    payload = instantiate_rewrite({**rule, "hole_witnesses": wit}, None, ctx)["payload"]
    assert payload["config_edits"] == {"CORE_UTILIZATION": "45"}          # 80 + median(-40,-30)
    assert payload["witness_selected"] == ["CORE_UTILIZATION"]
    # without witnesses (pre-D-A1 behaviour) the knob stays unresolved
    assert instantiate_rewrite(rule, None, ctx)["payload"]["unresolved_knobs"] == ["CORE_UTILIZATION"]


def test_non_numeric_witnesses_stay_unresolved():
    a = _row(config_delta={"MAX_ROUTING_LAYER": "met4"}, config_before={})
    b = _row(config_delta={"MAX_ROUTING_LAYER": "met5"}, config_before={})
    rule = _rule([(a, "des"), (b, "sha")])
    wit = {h: ["met4", "met5"] for h in rule["provenance"]["hole_constraints"]}
    payload = instantiate_rewrite({**rule, "hole_witnesses": wit}, None,
                                  RepairContext(check="drc", cfg={}))["payload"]
    assert payload["unresolved_knobs"] == ["MAX_ROUTING_LAYER"]


def test_unset_is_an_explicit_executable_value():
    from contracts import CONFIG_UNSET
    nested = {"CORE_UTILIZATION": {"before": None, "after": "40"},
              "DIE_AREA": {"before": "0 0 200 200", "after": None}}
    a, b = _row(config_delta=nested), _row(config_delta=nested)
    rule = _rule([(a, "des"), (b, "sha")])
    assert rule["after_pattern"]["rewrite.knob.DIE_AREA.abs"] == CONFIG_UNSET
    payload = instantiate_rewrite(rule, None, RepairContext(check="drc", cfg={}))["payload"]
    assert payload["config_edits"] == {"CORE_UTILIZATION": "40", "DIE_AREA": CONFIG_UNSET}


def test_memory_applied_fix_is_attributed_to_its_rule_family():
    t = _transition(_row(strategy="tehm_cand_0123", strategy_family="DENSITY_RELIEF",
                         memory_rule="rule_x", memory_trial=True), "a", "t1")
    assert t["action"]["transformation_family"] == "DENSITY_RELIEF"
    assert t["action"]["payload"]["strategy"] == "density_relief"
    assert t["action"]["payload"]["memory_rule"] == "rule_x"
    assert t["action"]["payload"]["memory_trial"] is True


def test_stage_fix_is_measured_by_stage_completion():
    """fix_signoff logs a route fix with before=null and after '0' on a clear; the
    stage-completion oracle (card B1) must make it measured, or memory can never learn
    from its own successful route fixes (Phase D2 smoke 2)."""
    route = dict(check="orfs_stage", violation_class="route", strategy="route_relief",
                 config_delta={}, config_before={})
    ok = measured_outcome(_row(**route, before=None, after="0", verdict="cleared"))
    assert ok["verdict"] == "PASS" and ok["before"] == 1 and ok["after"] == 0
    bad = measured_outcome(_row(**route, before=None, after=None, verdict="rerun_failed_rc124"))
    assert bad["verdict"] == "FAIL"
    # a DRC re-run that failed is still unmeasured (no DRC count was produced)
    assert measured_outcome(_row(after=None, verdict="rerun_failed_rc2")) is None
    t = _transition(_row(**route, before=None, after="0", verdict="cleared"), "a", "t1")
    assert t["verifier"]["oracle_complete"] is True
    assert t["observation_delta"]["failing_tests"] == {"before": 1, "after": 0}


# -- Phase F unblockers (memory's default since Phase H) -------------------------------

def _flw_rows():
    sit = {**SIT, "check": "orfs_stage", "violation_class": "place", "error_code": "FLW-0024",
           "util_band": "gt60", "count_band": None}
    mk = lambda after: _row(check="orfs_stage", violation_class="place", strategy="llm_edit:x",
                            config_delta={"CORE_UTILIZATION": after, "PLACE_DENSITY_LB_ADDON": "0.10"},
                            config_before={"CORE_UTILIZATION": "85", "PLACE_DENSITY_LB_ADDON": "0.2"},
                            situation=sit, before=1, after=0, verdict="cleared")
    return [(mk("45"), "des"), (mk("45"), "chacha"), (mk("55"), "sha")]


def _audit(flag):
    from tehm.crystallization.validity import ValidityConfig, audit_rule
    rows = _flw_rows()
    trans = [_transition(r, d, f"t{i}") for i, (r, d) in enumerate(rows)]
    for t, (_, d) in zip(trans, rows):
        t["lineage_id"] = d
    rule = _rule(rows)
    return audit_rule(rule, source_transitions=trans, config=ValidityConfig(value_tolerant=flag)).status


def test_v4_numeric_tolerance_unblocks_the_d2_flw_case():
    """D2: util 45, 45, 55 all verified PASS -> V4 UNSTABLE without value tolerance; with it
    (the default) a mismatch that is only on a numeric knob value is accepted."""
    assert _audit(flag=False) == "UNSTABLE_CANDIDATE"
    assert _audit(flag=True) == "VALIDATED"


def test_categorical_hole_takes_the_mode_or_drops_on_tie():
    a = _row(config_delta={"CORE_UTILIZATION": "45", "MIN_ROUTING_LAYER": "met1"},
             config_before={"CORE_UTILIZATION": "17"})
    b = _row(config_delta={"CORE_UTILIZATION": "45", "MIN_ROUTING_LAYER": "met2"},
             config_before={"CORE_UTILIZATION": "8"})
    rule = _rule([(a, "x"), (b, "y")])
    hole = rule["after_pattern"]["rewrite.knob.MIN_ROUTING_LAYER.abs"]
    ctx = RepairContext(check="drc", cfg={"CORE_UTILIZATION": "12"})
    tie = {**rule, "hole_witnesses": {hole: ["met1", "met2"]}}
    p = instantiate_rewrite(tie, None, ctx)["payload"]
    assert p["config_edits"] == {"CORE_UTILIZATION": "45"} and p["dropped_knobs"] == ["MIN_ROUTING_LAYER"]
    assert "unresolved_knobs" not in p
    mode = {**rule, "hole_witnesses": {hole: ["met2", "met1", "met2"]}}
    p = instantiate_rewrite(mode, None, ctx)["payload"]
    assert p["config_edits"]["MIN_ROUTING_LAYER"] == "met2" and p["categorical_selected"] == ["MIN_ROUTING_LAYER"]


def test_graded_severity_rides_the_transition():
    t = _transition(_row(severity_before=6.0, severity_after="2"), "a", "t1")
    assert t["action"]["payload"]["graded_severity"] == {"before": 6.0, "after": 2.0}
    assert "graded_severity" not in _transition(_row(), "a", "t2")["action"]["payload"]
