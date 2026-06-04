"""Unit tests for the heuristics payoff A/B harness (eval_heuristics.py).

Since real flows take hours, these drive the harness against FIXTURES:
 - emit: a fake project + fixture heuristics.json (mirrors
   test_suggest_config_integration's _make_fake_project) — asserts the two arms
   differ ONLY in the learned knob(s) + the EVAL_ARM marker.
 - summarize: synthetic arm dirs with controlled costs/signoff JSON — asserts
   cost math, win/regression classification, knob_diff, cost_metric forward-compat.
 - determinism: eval_summary.json is byte-reproducible from the same jsonl.
 - quality predicate reuses knowledge_db.is_success semantics.
"""
from __future__ import annotations

import json
from pathlib import Path

import eval_heuristics
import suggest_config


# --------------------------------------------------------------------------- #
# emit
# --------------------------------------------------------------------------- #
def _write_eval_set(tmp_path: Path) -> Path:
    p = tmp_path / "eval_set.json"
    p.write_text(json.dumps({
        "version": 1,
        "pairs": [
            {"design_name": "aes_core", "platform": "nangate45",
             "family": "aes_xcrypt", "note": "crypto"},
        ],
    }))
    return p


def _make_fake_project(root: Path, design: str = "aes_core") -> Path:
    """A materialized project so emit reads real synth stats (medium/crypto)."""
    project = root / design
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "synth").mkdir()
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design}\n"
        "export PLATFORM = nangate45\n"
    )
    (project / "rtl" / f"{design}.v").write_text(
        f"module {design}(input clk); endmodule\n"
    )
    (project / "synth" / "synth.log").write_text("Number of cells: 12412\n")
    return project


def _write_aes_heuristics(tmp_knowledge_dir: Path) -> Path:
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 7, "success_count": 7, "success_rate": 1.0,
                        "core_utilization": {"min_safe": 14, "max_safe": 16,
                                             "median": 15},
                    },
                },
            },
        },
    }))
    return heur_path


def test_emit_arms_differ_only_in_learned_knob(tmp_path, tmp_knowledge_dir,
                                               monkeypatch):
    projects_root = tmp_path / "projects"
    _make_fake_project(projects_root)
    heur_path = _write_aes_heuristics(tmp_knowledge_dir)
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH",
                        tmp_knowledge_dir / "families.json")

    eval_set = _write_eval_set(tmp_path)
    out_dir = tmp_path / "arms"

    import argparse
    eval_heuristics.cmd_emit(argparse.Namespace(
        eval_set=str(eval_set), out_dir=str(out_dir),
        projects_root=str(projects_root)))

    naive_cfg = suggest_config.parse_config_mk(
        out_dir / "aes_core_naive" / "constraints" / "config.mk")
    learned_cfg = suggest_config.parse_config_mk(
        out_dir / "aes_core_learned" / "constraints" / "config.mk")

    # EVAL_ARM marker present + correct in each.
    assert naive_cfg["EVAL_ARM"] == "naive"
    assert learned_cfg["EVAL_ARM"] == "learned"

    # Naive arm = params_by_size medium=25 clamped by crypto to 25.
    assert naive_cfg["CORE_UTILIZATION"] == "25"
    # Learned arm = median 15 (survives crypto clamp min(15,25)).
    assert learned_cfg["CORE_UTILIZATION"] == "15"

    # The ONLY non-marker knob that differs is CORE_UTILIZATION.
    differing = {k for k in set(naive_cfg) | set(learned_cfg)
                 if naive_cfg.get(k) != learned_cfg.get(k)}
    assert differing == {"CORE_UTILIZATION", "EVAL_ARM"}

    # learned_source provenance recorded in the plan: naive None, learned set.
    plan = json.loads((out_dir / "eval_plan.json").read_text())
    assert plan["cost_metric"] == "wall_clock_s"
    d0 = plan["designs"][0]
    assert d0["naive_learned_source"] is None
    assert d0["learned_learned_source"] == "aes_xcrypt/nangate45"
    # knob_diff values are normalized to strings in BOTH emit (eval_plan.json)
    # and summarize (eval_results.jsonl) so an operator sees consistent types.
    assert d0["knob_diff"] == {"CORE_UTILIZATION": {"naive": "25", "learned": "15"}}


def test_emit_missing_project_marks_cell_count_unknown(tmp_path, tmp_knowledge_dir,
                                                       monkeypatch):
    """No materialized project: emit still produces config.mk from heuristics,
    cell_count marked unknown, and does NOT crash."""
    heur_path = _write_aes_heuristics(tmp_knowledge_dir)
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH",
                        tmp_knowledge_dir / "families.json")

    eval_set = _write_eval_set(tmp_path)
    out_dir = tmp_path / "arms"

    import argparse
    rc = eval_heuristics.cmd_emit(argparse.Namespace(
        eval_set=str(eval_set), out_dir=str(out_dir), projects_root=None))
    assert rc == 0

    plan = json.loads((out_dir / "eval_plan.json").read_text())
    d0 = plan["designs"][0]
    assert d0["cell_count_known"] is False
    assert d0["cell_count"] is None
    # config.mk still written for both arms.
    assert (out_dir / "aes_core_naive" / "constraints" / "config.mk").exists()
    assert (out_dir / "aes_core_learned" / "constraints" / "config.mk").exists()


def test_recommend_stub_temp_dir_cleaned_up(tmp_path, tmp_knowledge_dir,
                                            monkeypatch):
    """The synthesized stub project (no --projects-root) must be torn down in
    finally — no leaked /tmp/evalstub_* dirs per emit invocation."""
    heur_path = _write_aes_heuristics(tmp_knowledge_dir)
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH",
                        tmp_knowledge_dir / "families.json")

    # Redirect tempfile into a private dir so we can observe what's left behind.
    tmproot = tmp_path / "tmproot"
    tmproot.mkdir()
    monkeypatch.setattr(eval_heuristics.tempfile, "tempdir", str(tmproot))

    rec = eval_heuristics._recommend_for_pair(
        {"design_name": "aes_core", "platform": "nangate45"},
        use_learned=True, projects_root=None)
    # The recommendation was still computed correctly...
    assert rec["learned_source"] == "aes_xcrypt/nangate45"
    # ...and no evalstub_* dir survived.
    leaked = [p for p in tmproot.glob("evalstub_*")]
    assert leaked == [], f"leaked stub dirs: {leaked}"


# --------------------------------------------------------------------------- #
# summarize fixtures
# --------------------------------------------------------------------------- #
def _make_arm(arms_dir: Path, design: str, arm: str, *,
              cu: str = "25",
              stage_costs: dict[str, float],
              drc_status="clean", drc_violations=0,
              lvs_status="clean", lvs_mismatch_count=0,
              lvs_mismatch_class=None,
              rcx_status="complete",
              finish=True,
              cpu=False,
              die_area=48400.0, power=0.0143) -> Path:
    """Build one synthetic arm dir with reports + a stage_log."""
    d = arms_dir / f"{design}_{arm}"
    (d / "constraints").mkdir(parents=True)
    (d / "reports").mkdir(parents=True)
    run = d / "backend" / "RUN_stub"
    run.mkdir(parents=True)

    (d / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {design}\n"
        "export PLATFORM = nangate45\n"
        f"export EVAL_ARM = {arm}\n"
        f"export CORE_UTILIZATION = {cu}\n"
    )

    (d / "reports" / "ppa.json").write_text(json.dumps({
        "summary": {"power": {"total_power_w": power}},
        "geometry": {"die_area_um2": die_area},
    }))
    (d / "reports" / "drc.json").write_text(json.dumps({
        "status": drc_status, "total_violations": drc_violations}))
    lvs = {"status": lvs_status, "mismatch_count": lvs_mismatch_count}
    if lvs_mismatch_class is not None:
        lvs["mismatch_class"] = lvs_mismatch_class
    (d / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (d / "reports" / "rcx.json").write_text(json.dumps({"status": rcx_status}))

    # stage_log: every stage passes; finish included iff finish=True.
    lines = []
    stages = list(stage_costs.items())
    for name, cost in stages:
        if name == "finish" and not finish:
            continue
        entry = {"stage": name, "status": "pass"}
        if cpu:
            entry["cpu_s"] = cost
            entry["elapsed_s"] = cost * 2  # wall > cpu, prove cpu is preferred
        else:
            entry["elapsed_s"] = cost
        lines.append(json.dumps(entry))
    if not finish:
        # record an explicit non-pass finish so finish_reached is False
        lines.append(json.dumps({"stage": "finish", "status": "fail",
                                 "elapsed_s": 1.0}))
    (run / "stage_log.jsonl").write_text("\n".join(lines) + "\n")
    return d


def _summarize(arms_dir: Path, out_dir: Path, reaggregate_only=False) -> dict:
    import argparse
    eval_heuristics.cmd_summarize(argparse.Namespace(
        arms_dir=str(arms_dir), out_dir=str(out_dir),
        reaggregate_only=reaggregate_only))
    return json.loads((out_dir / "eval_summary.json").read_text())


def test_summarize_win_when_cheaper_and_signoff_held(tmp_path):
    arms = tmp_path / "arms"
    arms.mkdir()
    # naive: total 1000; learned: total 800 (20% cheaper), both signoff-clean.
    _make_arm(arms, "foo", "naive", cu="30",
              stage_costs={"synth": 100, "place": 400, "route": 400, "finish": 100})
    _make_arm(arms, "foo", "learned", cu="15",
              stage_costs={"synth": 100, "place": 300, "route": 300, "finish": 100})

    out = tmp_path / "out"
    summary = _summarize(arms, out)

    assert summary["cost_metric"] == "wall_clock_s"
    assert summary["n_designs"] == 1
    assert summary["n_wins"] == 1
    assert summary["n_regressions"] == 0
    d0 = summary["designs"][0]
    assert d0["classification"] == "win"
    # (1000 - 800)/1000*100 == 20.0
    assert abs(d0["cost_delta_pct"] - 20.0) < 1e-9
    assert d0["knob_diff"] == {"CORE_UTILIZATION": {"naive": "30", "learned": "15"}}
    assert summary["median_cost_delta_pct_wins"] == 20.0


def test_summarize_regression_when_cheaper_but_signoff_worse(tmp_path):
    arms = tmp_path / "arms"
    arms.mkdir()
    # learned cheaper (800 vs 1000) BUT its LVS failed -> signoff regressed.
    _make_arm(arms, "bar", "naive", cu="30",
              stage_costs={"synth": 100, "place": 400, "route": 400, "finish": 100})
    _make_arm(arms, "bar", "learned", cu="15",
              stage_costs={"synth": 100, "place": 300, "route": 300, "finish": 100},
              lvs_status="fail", lvs_mismatch_count=4)

    out = tmp_path / "out"
    summary = _summarize(arms, out)

    assert summary["n_wins"] == 0
    assert summary["n_regressions"] == 1
    d0 = summary["designs"][0]
    assert d0["classification"] == "regression"
    assert d0["cost_delta_pct"] > 0  # cheaper
    assert d0["naive"]["signoff_ok"] is True
    assert d0["learned"]["signoff_ok"] is False


def test_summarize_inconclusive_when_both_fail_and_cheaper(tmp_path):
    """Honesty guard: both arms fail signoff and learned is cheaper -> the
    learned config produced cheaper-but-UNUSABLE output. That is NOT a win and
    NOT a regression (there was no usable baseline to break): inconclusive."""
    arms = tmp_path / "arms"
    arms.mkdir()
    # naive fails LVS; learned ALSO fails LVS but is cheaper (800 vs 1000).
    _make_arm(arms, "noway", "naive", cu="30",
              stage_costs={"synth": 100, "place": 400, "route": 400, "finish": 100},
              lvs_status="fail", lvs_mismatch_count=2)
    _make_arm(arms, "noway", "learned", cu="15",
              stage_costs={"synth": 100, "place": 300, "route": 300, "finish": 100},
              lvs_status="fail", lvs_mismatch_count=5)

    out = tmp_path / "out"
    summary = _summarize(arms, out)
    d0 = summary["designs"][0]
    assert d0["naive"]["signoff_ok"] is False
    assert d0["learned"]["signoff_ok"] is False
    assert d0["cost_delta_pct"] > 0  # learned IS cheaper
    assert d0["classification"] == "inconclusive"   # the new guard
    # An inconclusive is neither a win nor a regression.
    assert summary["n_wins"] == 0
    assert summary["n_regressions"] == 0
    assert summary["n_inconclusive"] == 1


def test_summarize_no_change_when_not_cheaper(tmp_path):
    arms = tmp_path / "arms"
    arms.mkdir()
    # learned MORE expensive (1200 vs 1000), signoff held -> not a win.
    _make_arm(arms, "baz", "naive", cu="30",
              stage_costs={"synth": 100, "place": 400, "route": 400, "finish": 100})
    _make_arm(arms, "baz", "learned", cu="15",
              stage_costs={"synth": 200, "place": 500, "route": 400, "finish": 100})

    out = tmp_path / "out"
    summary = _summarize(arms, out)
    d0 = summary["designs"][0]
    assert d0["classification"] == "no_change"
    assert d0["cost_delta_pct"] < 0
    assert summary["n_wins"] == 0


def test_cost_metric_wall_when_only_elapsed_s(tmp_path):
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "w", "naive", stage_costs={"synth": 10, "finish": 5})
    _make_arm(arms, "w", "learned", stage_costs={"synth": 8, "finish": 5})
    out = tmp_path / "out"
    summary = _summarize(arms, out)
    assert summary["cost_metric"] == "wall_clock_s"
    assert summary["designs"][0]["cost_metric"] == "wall_clock_s"


def test_cost_metric_cpu_when_cpu_s_present(tmp_path):
    arms = tmp_path / "arms"
    arms.mkdir()
    # cpu=True -> entries carry cpu_s; harness must PREFER cpu_s and report cpu_s.
    _make_arm(arms, "c", "naive", stage_costs={"synth": 10, "finish": 5}, cpu=True)
    _make_arm(arms, "c", "learned", stage_costs={"synth": 8, "finish": 5}, cpu=True)
    out = tmp_path / "out"
    summary = _summarize(arms, out)
    assert summary["cost_metric"] == "cpu_s"
    d0 = summary["designs"][0]
    assert d0["cost_metric"] == "cpu_s"
    # cpu_s preferred over the (2x larger) elapsed_s: naive total == 10+5 == 15.
    assert d0["naive"]["total_cost"] == 15.0
    # cost_delta_pct from cpu: (15 - 13)/15*100
    assert abs(d0["cost_delta_pct"] - (2 / 15 * 100)) < 1e-9


def test_quality_lvs_incomplete_not_signoff_ok(tmp_path):
    """An lvs 'incomplete' arm is NOT signoff_ok (reuses is_success)."""
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "q", "naive", stage_costs={"synth": 10, "finish": 5})
    _make_arm(arms, "q", "learned", stage_costs={"synth": 8, "finish": 5},
              lvs_status="incomplete")
    out = tmp_path / "out"
    summary = _summarize(arms, out)
    d0 = summary["designs"][0]
    assert d0["naive"]["signoff_ok"] is True
    assert d0["learned"]["signoff_ok"] is False


def test_quality_symmetric_matcher_is_signoff_ok(tmp_path):
    """A symmetric_matcher LVS fail IS signoff_ok (KLayout limitation)."""
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "s", "naive", stage_costs={"synth": 10, "finish": 5})
    _make_arm(arms, "s", "learned", stage_costs={"synth": 8, "finish": 5},
              lvs_status="fail", lvs_mismatch_count=2,
              lvs_mismatch_class="symmetric_matcher")
    out = tmp_path / "out"
    summary = _summarize(arms, out)
    d0 = summary["designs"][0]
    assert d0["learned"]["signoff_ok"] is True
    # cheaper AND signoff held -> win (symmetric_matcher counts as held).
    assert d0["classification"] == "win"


def test_results_jsonl_idempotent_replace(tmp_path):
    """Re-running summarize REPLACES a design's line, never duplicates."""
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "idem", "naive", stage_costs={"synth": 100, "finish": 100})
    _make_arm(arms, "idem", "learned", stage_costs={"synth": 80, "finish": 100})
    out = tmp_path / "out"
    _summarize(arms, out)
    _summarize(arms, out)  # second run

    lines = [l for l in (out / "eval_results.jsonl").read_text().splitlines() if l]
    assert len(lines) == 1  # not duplicated


def test_eval_summary_deterministic_reaggregate(tmp_path):
    """eval_summary.json is byte-reproducible from the same eval_results.jsonl."""
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "a", "naive", stage_costs={"synth": 100, "finish": 100})
    _make_arm(arms, "a", "learned", stage_costs={"synth": 80, "finish": 100})
    _make_arm(arms, "b", "naive", stage_costs={"synth": 200, "finish": 100})
    _make_arm(arms, "b", "learned", stage_costs={"synth": 150, "finish": 100})

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    _summarize(arms, out1)
    _summarize(arms, out2)
    assert (out1 / "eval_summary.json").read_bytes() == \
           (out2 / "eval_summary.json").read_bytes()

    # And a pure reaggregate from the SAME jsonl matches the live summary byte-for-byte.
    summary_live = (out1 / "eval_summary.json").read_bytes()
    reagg = eval_heuristics.reaggregate(out1 / "eval_results.jsonl")
    reagg_bytes = (json.dumps(reagg, indent=2, sort_keys=True) + "\n").encode()
    assert reagg_bytes == summary_live


def test_summarize_reaggregate_only_path(tmp_path):
    """--reaggregate-only recomputes summary from jsonl WITHOUT reading arms."""
    arms = tmp_path / "arms"
    arms.mkdir()
    _make_arm(arms, "r", "naive", stage_costs={"synth": 100, "finish": 100})
    _make_arm(arms, "r", "learned", stage_costs={"synth": 80, "finish": 100})
    out = tmp_path / "out"
    _summarize(arms, out)

    # Delete arms dir entirely; reaggregate-only must still produce the summary.
    import shutil
    shutil.rmtree(arms)
    summary = _summarize(arms, out, reaggregate_only=True)
    assert summary["n_designs"] == 1
    assert summary["designs"][0]["design_name"] == "r"
