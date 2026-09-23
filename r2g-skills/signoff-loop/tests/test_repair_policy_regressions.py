"""Regression tests derived from the 2026-08 Experiment-2 failure logs."""
from __future__ import annotations

import json
from pathlib import Path

import diagnose_signoff_fix as dsf
import knowledge_db
import recipe_lifecycle
import suggest_config


def test_project_synth_stats_resolves_current_orfs_layout(tmp_path: Path):
    project = tmp_path / "project"
    old = project / "backend" / "RUN_001" / "reports_orfs"
    new = project / "backend" / "RUN_002" / "reports_orfs"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "synth_stat.txt").write_text(
        "100 1.0E+03 100 1.0E+03 cells\n", encoding="utf-8")
    (new / "synth_stat.txt").write_text(
        "3358 3.78E+04 3358 3.78E+04 cells\n"
        "4210 5.10E+04 4210 5.10E+04 wires\n", encoding="utf-8")
    (old / "synth_stat.txt").touch()
    (new / "synth_stat.txt").touch()

    stats = suggest_config.parse_project_synth_stats(project)

    assert stats["cell_count"] == 3358
    assert stats["wire_count"] == 4210


def test_action_policy_blocks_footprint_change_but_keeps_allowed_pin_action(
        tmp_path: Path, monkeypatch):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "r2g-repair-action-policy-1.0",
        "allowed_numeric_knobs": {},
        "allowed_string_knobs": {
            "PLACE_PINS_ARGS": ["-exclude right:*", "-exclude left:*"]},
        "allowed_sdc_edits": {},
    }), encoding="utf-8")
    monkeypatch.setenv("R2G_REPAIR_ACTION_POLICY_FILE", str(policy_path))
    plan = {"status": "fail", "strategies": [
        {"id": "density_relief", "config_edits": {"CORE_UTILIZATION": "17"}},
        {"id": "pin_side_rebalance",
         "config_edits": {"PLACE_PINS_ARGS": "-exclude right:*"}},
    ]}

    dsf._apply_repair_action_policy(plan)

    assert [s["id"] for s in plan["strategies"]] == ["pin_side_rebalance"]
    assert plan["action_policy_gate_ok"] is True
    assert plan["action_policy_rejections"] == [{
        "strategy": "density_relief",
        "reason": "config knob CORE_UTILIZATION is not allowed by the repair action policy",
    }]


def test_timing_trial_policy_allows_only_registered_setup_margin(
        tmp_path: Path, monkeypatch):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "r2g-repair-action-policy-1.0",
        "allowed_numeric_knobs": {
            "SETUP_SLACK_MARGIN": {"minimum": 0.2, "maximum": 0.2}},
        "allowed_string_knobs": {},
        "allowed_sdc_edits": {},
    }), encoding="utf-8")
    monkeypatch.setenv("R2G_REPAIR_ACTION_POLICY_FILE", str(policy_path))
    plan = {"status": "minor", "strategies": [
        {"id": "setup_slack_margin",
         "config_edits": {"SETUP_SLACK_MARGIN": "0.2"}, "sdc_edits": {}},
        {"id": "utilization_reduce",
         "config_edits": {"CORE_UTILIZATION": "20"}, "sdc_edits": {}},
        {"id": "period_relax", "config_edits": {},
         "sdc_edits": {"CLOCK_PERIOD": "11.0"}},
    ]}

    dsf._apply_repair_action_policy(plan)

    assert [s["id"] for s in plan["strategies"]] == ["setup_slack_margin"]
    assert {r["strategy"] for r in plan["action_policy_rejections"]} == {
        "utilization_reduce", "period_relax"}


def test_fixed_task_policy_keeps_synthesis_recipe_but_blocks_area_and_clock(
        tmp_path: Path, monkeypatch):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "r2g-repair-action-policy-1.0",
        "allowed_numeric_knobs": {},
        "allowed_string_knobs": {
            "ABC_AREA": ["0", "1"],
            "SYNTH_HIERARCHICAL": ["0", "1"],
        },
        "allowed_sdc_edits": {},
    }), encoding="utf-8")
    monkeypatch.setenv("R2G_REPAIR_ACTION_POLICY_FILE", str(policy_path))
    plan = dsf.build_plan(
        {}, {},
        {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20", "ABC_AREA": "1"},
        check="timing",
        tcheck={"tier": "severe", "wns_ns": -3.0, "clock_period_ns": 10.0},
        route={"status": "clean", "total_violations": 0},
    )

    dsf._apply_repair_action_policy(plan)

    assert [s["id"] for s in plan["strategies"]] == [
        "hierarchical_synthesis_mapping",
        "backend_aware_synth_retune",
    ]
    assert {r["strategy"] for r in plan["action_policy_rejections"]} == {
        "abc_overdrive_8ns",
        "early_place_timing_repair",
        "hierarchical_place_timing_repair",
        "period_relax",
        "utilization_reduce",
    }


def test_configured_but_unreadable_action_policy_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("R2G_REPAIR_ACTION_POLICY_FILE", str(tmp_path / "missing.json"))
    plan = {"status": "fail", "strategies": [
        {"id": "density_relief", "config_edits": {"CORE_UTILIZATION": "17"}},
    ]}

    dsf._apply_repair_action_policy(plan)

    assert plan["strategies"] == []
    assert plan["action_policy_gate_ok"] is False
    assert "unreadable repair action policy" in plan["residual_reason"]


def test_malformed_action_policy_bounds_fail_closed(tmp_path: Path, monkeypatch):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "r2g-repair-action-policy-1.0",
        "allowed_numeric_knobs": {
            "CORE_UTILIZATION": {"minimum": 30, "maximum": 10}},
        "allowed_string_knobs": {},
        "allowed_sdc_edits": {},
    }), encoding="utf-8")
    monkeypatch.setenv("R2G_REPAIR_ACTION_POLICY_FILE", str(policy_path))
    plan = {"status": "fail", "strategies": [
        {"id": "density_relief", "config_edits": {"CORE_UTILIZATION": "17"}},
    ]}

    dsf._apply_repair_action_policy(plan)

    assert plan["strategies"] == []
    assert plan["action_policy_gate_ok"] is False
    assert "inverted" in plan["residual_reason"]


def _edge_project(tmp_path: Path, boxes: list[tuple[float, float, float, float]]) -> Path:
    project = tmp_path / "project"
    result = project / "backend" / "RUN_001" / "results"
    result.mkdir(parents=True)
    (result / "6_final.def").write_text(
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\n", encoding="utf-8")
    drc_dir = project / "drc"
    drc_dir.mkdir()
    items = []
    for x0, y0, x1, y1 in boxes:
        items.append(
            "<item><category>'m3.2'</category><cell>top</cell><values>"
            f"<value>box: ({x0},{y0};{x1},{y1})</value>"
            "</values></item>")
    (drc_dir / "6_drc.lyrdb").write_text(
        "<report-database><items>" + "".join(items)
        + "</items></report-database>", encoding="utf-8")
    return project


def test_edge_localized_m32_emits_ab_gated_pin_strategy(tmp_path: Path):
    project = _edge_project(tmp_path, [
        (98.2, 20.0, 98.7, 20.2),
        (98.4, 40.0, 98.9, 40.2),
        (98.1, 60.0, 98.8, 60.2),
    ])
    drc = {"status": "fail", "categories": {"'m3.2'": {"count": 3}}}

    strategy = dsf._edge_localized_pin_strategy(
        project, drc, {"PLATFORM": "sky130hd"})

    assert strategy["id"] == "pin_side_rebalance"
    assert strategy["config_edits"] == {"PLACE_PINS_ARGS": "-exclude right:*"}
    assert strategy["requires_ab_promotion"] is True
    assert strategy["geometry_evidence"]["edge_fraction"] == 1.0


def test_edge_localized_strategy_rejects_ambiguous_corner_cluster(tmp_path: Path):
    project = _edge_project(tmp_path, [
        (98.2, 98.2, 98.7, 98.7),
        (98.4, 98.4, 98.9, 98.9),
    ])
    drc = {"status": "fail", "categories": {"'m3.2'": {"count": 2}}}

    assert dsf._edge_localized_pin_strategy(
        project, drc, {"PLATFORM": "sky130hd"}) is None


def test_ab_gated_strategy_becomes_live_only_after_promotion():
    strategy = {"id": "pin_side_rebalance", "auto_apply": True,
                "requires_ab_promotion": True}
    assert dsf._live_auto_strategy({"strategies": [strategy.copy()]}) is None
    promoted = {**strategy, "lifecycle_status": "promoted"}
    assert dsf._live_auto_strategy({"strategies": [promoted]})["id"] == strategy["id"]
    candidate = {**strategy, "lifecycle_status": "candidate"}
    assert dsf._live_auto_strategy({"strategies": [candidate]}) is None
    assert dsf._live_auto_strategy(
        {"strategies": [candidate]}, rank_first=strategy["id"])["id"] == strategy["id"]


def test_preregistered_candidate_attempt_executes_without_claiming_promotion():
    strategy = {
        "id": "early_place_timing_repair",
        "config_edits": {"ENABLE_PLACE_REPAIR_TIMING": "1"},
        "sdc_edits": {},
        "auto_apply": True,
        "requires_ab_promotion": True,
        "lifecycle_status": "candidate",
    }
    policy = {
        "candidate_attempt_policy": {
            "mode": "single_preregistered_attempt",
            "entries": [{
                "policy_id": "exp2-early-place-v1",
                "check": "timing",
                "platform": "sky130hd",
                "strategy": "early_place_timing_repair",
                "effect_fingerprint": {"ENABLE_PLACE_REPAIR_TIMING": "1"},
            }],
        },
    }
    authorization = dsf._candidate_attempt_authorized(
        policy, check="timing", platform="sky130hd", strategy=strategy,
        lifecycle_status="candidate")
    assert authorization["policy_id"] == "exp2-early-place-v1"
    live = {**strategy, "candidate_attempt_authorized": True}
    assert dsf._live_auto_strategy({"strategies": [live]})["id"] == strategy["id"]

    assert dsf._candidate_attempt_authorized(
        policy, check="timing", platform="sky130hd", strategy=strategy,
        lifecycle_status="shadow") is None
    assert dsf._candidate_attempt_authorized(
        policy, check="drc", platform="sky130hd", strategy=strategy,
        lifecycle_status="candidate") is None
    changed_effect = {
        **strategy, "config_edits": {"ENABLE_PLACE_REPAIR_TIMING": "0"},
    }
    assert dsf._candidate_attempt_authorized(
        policy, check="timing", platform="sky130hd", strategy=changed_effect,
        lifecycle_status="candidate") is None


def test_pin_side_scope_transfer_is_narrow_and_exact_status_wins(tmp_path: Path):
    conn = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn)
    key = {
        "symptom_id": "04d38c5a585fd332",
        "design_class": "*",
        "platform": "sky130hd",
        "strategy": "pin_side_rebalance",
    }
    recipe_lifecycle._set(conn, "promoted", "test:platform_geometric", **key)
    conn.commit()
    strategy = {
        "id": "pin_side_rebalance",
        "config_edits": {"PLACE_PINS_ARGS": "-exclude right:*"},
        "geometry_evidence": {
            "rule": "m3.2", "side": "right", "edge_count": 5,
            "edge_fraction": 1.0,
        },
    }

    assert dsf._lifecycle_status_with_scope_transfer(
        conn, recipe_lifecycle, symptom_id=key["symptom_id"],
        design_class="crypto/large", platform="sky130hd", strategy=strategy,
    ) == ("promoted", "platform_geometric")

    low_confidence = {
        **strategy,
        "geometry_evidence": {**strategy["geometry_evidence"], "edge_fraction": 0.79},
    }
    assert dsf._lifecycle_status_with_scope_transfer(
        conn, recipe_lifecycle, symptom_id=key["symptom_id"],
        design_class="crypto/large", platform="sky130hd", strategy=low_confidence,
    ) == (None, None)
    assert dsf._lifecycle_status_with_scope_transfer(
        conn, recipe_lifecycle, symptom_id=key["symptom_id"],
        design_class="crypto/large", platform="sky130hd",
        strategy={**strategy, "id": "unrelated_recipe"},
    ) == (None, None)

    exact = {**key, "design_class": "crypto/large"}
    recipe_lifecycle._set(conn, "shadow", "test:exact_veto", **exact)
    conn.commit()
    assert dsf._lifecycle_status_with_scope_transfer(
        conn, recipe_lifecycle, symptom_id=key["symptom_id"],
        design_class="crypto/large", platform="sky130hd", strategy=strategy,
    ) == ("shadow", "exact")
    conn.close()


def test_fixed_target_timing_candidates_are_visible_but_not_live():
    plan = dsf.build_plan(
        {}, {},
        {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "20", "ABC_AREA": "1"},
        check="timing",
        tcheck={"tier": "minor", "wns_ns": -0.27, "clock_period_ns": 10.0},
        route={"status": "clean", "total_violations": 0},
    )

    ids = [strategy["id"] for strategy in plan["strategies"]]
    assert ids == [
        "abc_overdrive_8ns",
        "early_place_timing_repair",
        "hierarchical_synthesis_mapping",
        "hierarchical_place_timing_repair",
        "utilization_reduce",
    ]
    for strategy in plan["strategies"]:
        if strategy["id"] != "utilization_reduce":
            assert strategy["requires_ab_promotion"] is True
    assert dsf._live_auto_strategy(plan) is not None

    fixed_task_plan = {
        "strategies": [strategy for strategy in plan["strategies"]
                       if strategy["id"] != "utilization_reduce"]
    }
    assert dsf._live_auto_strategy(fixed_task_plan) is None
