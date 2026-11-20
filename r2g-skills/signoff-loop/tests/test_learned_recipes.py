"""Learned-recipe accumulation + reuse (knowledge/learned_recipes.py, 2026-11-08)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import diagnose_signoff_fix as d
import learned_recipes as lr
import symptom

MOD = Path(__file__).resolve().parents[1] / "scripts" / "reports" / "diagnose_signoff_fix.py"
HOOK = "# split port feedthrough nets\nforeach n $ft { odb::dbNet_create $block $n }\n"


# ---------------------------------------------------------------- pure helpers
def test_frontend_class_maps_first_error():
    log = "info\nERROR: Can not open file `font.hex` for \\$readmemh.\nERROR: later\n"
    assert lr.frontend_class(log) == "missing_mem_init_file"
    assert lr.frontend_class("ERROR: Re-definition of module `\\uart'!") == "module_redefinition"
    assert lr.frontend_class("ERROR: Found 3 problems in 'check -assert'.") == "check_assert_problems"
    assert lr.frontend_class("all good\n") is None
    assert lr.frontend_class("ERROR: something new") == "other"


def test_stop_symptom_uses_last_nonclean_row_then_synth():
    rows = [{"check": "drc", "verdict": "stop_clean", "violation_class": None},
            {"check": "lvs", "verdict": "stop_mismatch", "violation_class": "top_pin_mismatch", "predicates": {}}]
    sig, sid = lr.stop_symptom(rows)
    assert sig == {"check": "lvs", "class": "top_pin_mismatch", "predicates": {}}
    assert sid == symptom.symptom_id(sig)
    sig2, _ = lr.stop_symptom([], "ERROR: Re-definition of module `x'")
    assert sig2["check"] == "synth" and sig2["class"] == "module_redefinition"
    assert lr.stop_symptom([], "") is None


def _files(**kw):
    return {k.replace("__", "/"): v.encode() for k, v in kw.items()}


def test_config_delta_factors_out_plumbing_and_captures_hooks():
    r2g = {"DESIGN_NAME": "t", "VERILOG_FILES": "/old/ws/rtl/a.v /old/ws/rtl/b.v", "SDC_FILE": "/old/ws/constraints/c.sdc",
           "PDN_TCL": "/old/ws/constraints/pdn.tcl", "CORE_UTILIZATION": "30", "VERILOG_INCLUDE_DIRS": "/old/ws/rtl"}
    llm = {"DESIGN_NAME": "t", "VERILOG_FILES": "/new/p/rtl/a.v /new/p/rtl/b.v", "SDC_FILE": "/new/p/constraints/c.sdc",
           "PDN_TCL": "/new/p/constraints/pdn.tcl", "CORE_UTILIZATION": "20", "VERILOG_INCLUDE_DIRS": "/new/p/rtl",
           "POST_GLOBAL_PLACE_TCL": "/new/p/hooks/ft.tcl"}
    rf = {"constraints/c.sdc": b"sdc", "constraints/pdn.tcl": b"pdn"}
    lf = {"constraints/c.sdc": b"sdc", "constraints/pdn.tcl": b"pdn", "hooks/ft.tcl": HOOK.encode()}
    out = lr.config_delta(r2g, llm, rf, lf)
    assert out["rejected"] == []
    assert out["edits"] == {"CORE_UTILIZATION": "20", "POST_GLOBAL_PLACE_TCL": "@asset:ft.tcl"}
    assert out["assets"] == {"ft.tcl": HOOK}
    assert set(out["ignored"]) == {"VERILOG_FILES", "SDC_FILE", "PDN_TCL", "VERILOG_INCLUDE_DIRS"}


def test_config_delta_rejects_task_changes():
    base = {"DESIGN_NAME": "t", "VERILOG_FILES": "/o/rtl/a.v", "SDC_FILE": "/o/constraints/c.sdc"}
    rf = {"constraints/c.sdc": b"sdc", "rtl/a.v": b"m"}
    assert "sdc_changed" in lr.config_delta(base, {**base, "SDC_FILE": "/n/constraints/c.sdc"}, rf,
                                            {"constraints/c.sdc": b"OTHER"})["rejected"]
    assert "source_set_changed" in lr.config_delta(base, {**base, "VERILOG_FILES": "/n/rtl/a.v /n/rtl/extra.v"}, rf, rf)["rejected"]
    assert "top_module_changed" in lr.config_delta(base, {**base, "DESIGN_NAME": "t_top"}, rf, rf)["rejected"]
    assert "frozen_key_changed:PLATFORM" in lr.config_delta({**base, "PLATFORM": "a"}, {**base, "PLATFORM": "b"}, rf, rf)["rejected"]
    assert any(r.startswith("unresolvable_path") for r in
               lr.config_delta(base, {**base, "PRE_CTS_TCL": "/n/hooks/missing.tcl"}, rf, rf)["rejected"])


def test_rerun_stage_is_earliest_affected():
    assert lr.rerun_stage(["POST_GLOBAL_PLACE_TCL"]) == "place"
    assert lr.rerun_stage(["POST_GLOBAL_PLACE_TCL", "CORE_UTILIZATION"]) == "floorplan"
    assert lr.rerun_stage(["ABC_AREA"]) == "synth"
    assert lr.rerun_stage(["SOMETHING_UNKNOWN"]) == "synth"      # unknown -> full rerun
    assert lr.rerun_stage(["LVS_MAX_DEPTH"]) is None               # signoff-only: recheck, no rerun


def _attempt(**kw):
    sig = {"check": "lvs", "class": "top_pin_mismatch", "predicates": {}}
    a = {"case_id": "gf180_C1", "platform": "gf180", "outcome": "dual_clean", "rtl_changed": [],
         "symptom": (sig, symptom.symptom_id(sig)), "summary": "buffered feedthroughs",
         "delta": {"edits": {"POST_GLOBAL_PLACE_TCL": "@asset:ft.tcl"}, "assets": {"ft.tcl": HOOK},
                   "rejected": [], "ignored": {}}}
    a.update(kw); return a


def test_distill_only_verified_reusable_successes():
    ok = lr.distill(_attempt())["recipe"]
    assert ok["strategy"]["rerun_from"] == "place" and ok["strategy"]["recheck"] == "lvs"
    assert lr.distill(_attempt(outcome="pending_semantic_review"))["rejected"].startswith("not_a_verified_success")
    assert lr.distill(_attempt(outcome="not_dual_clean"))["rejected"].startswith("not_a_verified_success")
    assert lr.distill(_attempt(rtl_changed=["rtl/a.v"]))["rejected"] == "rtl_edit"
    assert lr.distill(_attempt(symptom=None))["rejected"] == "unknown_r2g_symptom"
    empty = {"edits": {}, "assets": {}, "rejected": [], "ignored": {"VERILOG_FILES": "x"}}
    assert lr.distill(_attempt(delta=empty))["rejected"] == "no_reusable_delta"
    # the id is content-addressed: the same fix from two donors is ONE recipe
    assert lr.distill(_attempt(case_id="gf180_C2"))["recipe"]["recipe_id"] == ok["recipe_id"]


# ---------------------------------------------------------------- store
def _db(tmp_path):
    c = sqlite3.connect(tmp_path / "k.sqlite"); lr.ensure_schema(c); return c


def test_record_retrieve_dedup_and_unit_exclusion(tmp_path):
    c = _db(tmp_path); rec = lr.distill(_attempt())["recipe"]
    assert lr.record(c, rec, "gf180_C1", "repoA", {"cost": 1}) is True
    assert lr.record(c, rec, "gf180_C2", "repoB", {"cost": 2}) is False        # same fix, more evidence
    got = lr.retrieve(c, rec["symptom_id"], "gf180")
    assert len(got) == 1 and got[0]["evidence_units"] == 2
    # validated = >= 2 independent units: excluding one donor's unit leaves a single, unvalidated donor
    assert lr.retrieve(c, rec["symptom_id"], "gf180", exclude_units={"repoA"}) == []
    assert lr.retrieve(c, rec["symptom_id"], "gf180", exclude_units={"repoA"}, min_units=1)[0]["evidence_units"] == 1
    assert lr.retrieve(c, rec["symptom_id"], "gf180", exclude_units={"repoA", "repoB"}, min_units=1) == []  # no self-reuse
    assert lr.retrieve(c, rec["symptom_id"], "nangate45") == []                                  # platform-scoped
    assert lr.retrieve(c, "0" * 16, "gf180") == []                                               # symptom-scoped


def test_negative_evidence_stops_offering(tmp_path):
    c = _db(tmp_path); rec = lr.distill(_attempt())["recipe"]
    lr.record(c, rec, "gf180_C1", "repoA", {}); lr.record(c, rec, "gf180_C2", "repoB", {})
    assert lr.retrieve(c, rec["symptom_id"], "gf180")[0]["evidence_units"] == 2
    for k in range(2):
        lr.record_use(c, rec["recipe_id"], "gf180_F%d" % k, None, False, {})
    assert lr.retrieve(c, rec["symptom_id"], "gf180")                      # 2 failures vs 2 donor units: kept
    lr.record_use(c, rec["recipe_id"], "gf180_F2", None, False, {})
    assert lr.retrieve(c, rec["symptom_id"], "gf180") == []                # 3 failures > 2 units: withdrawn
    lr.record_use(c, rec["recipe_id"], "gf180_S0", None, True, {})
    assert lr.retrieve(c, rec["symptom_id"], "gf180")[0]["reuse_success"] == 1   # 3 vs 2 + 1: back


def test_store_without_table_offers_nothing(tmp_path):
    c = sqlite3.connect(tmp_path / "empty.sqlite")
    assert lr.retrieve(c, "x", "gf180") == []


# ---------------------------------------------------------------- plan injection
def test_inject_only_when_no_live_strategy_and_after_builtins(tmp_path):
    c = _db(tmp_path); rec = lr.distill(_attempt())["recipe"]; lr.record(c, rec, "gf180_C1", "repoA", {})
    proj = tmp_path / "proj"; proj.mkdir()
    assert lr.inject({"strategies": []}, proj, platform="gf180", sid=rec["symptom_id"], conn=c,
                     live_pick=lambda p: None)["strategies"] == []          # one donor: not validated yet
    lr.record(c, rec, "gf180_C2", "repoB", {})
    builtin = {"id": "builtin", "auto_apply": True, "recheck": "lvs"}
    plan = lr.inject({"strategies": [builtin]}, proj, platform="gf180", sid=rec["symptom_id"], conn=c,
                     live_pick=lambda p: builtin)
    assert [s["id"] for s in plan["strategies"]] == ["builtin"]            # never displaces a live one
    plan = lr.inject({"strategies": [dict(builtin, auto_apply=False)], "residual_reason": "x"}, proj,
                     platform="gf180", sid=rec["symptom_id"], conn=c, live_pick=lambda p: None)
    st = plan["strategies"][-1]
    assert st["id"] == "learned:" + rec["recipe_id"] and st["candidate_attempt_authorized"] is True
    assert plan["residual_reason"] is None
    hook = Path(st["config_edits"]["POST_GLOBAL_PLACE_TCL"])
    assert hook.parent == proj / "hooks" / "learned" and hook.read_text() == HOOK
    assert d._lifecycle_allows_auto_apply(plan, st) is True                 # rides the lifecycle gate
    # a policy refusal is recorded, not applied
    plan = lr.inject({"strategies": []}, proj, platform="gf180", sid=rec["symptom_id"], conn=c,
                     live_pick=lambda p: None, policy_failure=lambda s: "not allowed")
    assert plan["strategies"] == [] and plan["learned_rejected"][0]["reason"] == "not allowed"
    # an already-tried learned recipe is excluded
    plan = lr.inject({"strategies": []}, proj, platform="gf180", sid=rec["symptom_id"], conn=c,
                     live_pick=lambda p: None, exclude={"learned:" + rec["recipe_id"]})
    assert plan["strategies"] == []


# ---------------------------------------------------------------- end to end through the R2G CLI
def test_cli_next_then_apply_reuses_learned_recipe(tmp_path):
    """R2G stops on an LVS symptom it has no strategy for (symmetric-matcher residual on nangate45);
    with a learned recipe for that symptom, --next selects it and --apply writes its edit + hook."""
    lvs = {"status": "mismatch", "mismatch_class": "symmetric_matcher", "mismatch_count": 2}
    p = tmp_path / "proj"; (p / "reports").mkdir(parents=True); (p / "constraints").mkdir()
    (p / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (p / "constraints" / "config.mk").write_text("export DESIGN_NAME = t\nexport PLATFORM = nangate45\n")
    env = {**os.environ, "R2G_KNOWLEDGE_DB": str(tmp_path / "k.sqlite")}
    base = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs", "--next"],
                          capture_output=True, text=True, env=env).stdout.strip()
    assert base.startswith("STOP"), base                                     # R2G alone: no recipe
    vclass = d._current_vclass("lvs", {}, lvs, None)
    sig = symptom.canonical_signature("lvs", vclass, symptom.predicates_for("lvs", lvs))
    rec = lr.distill(_attempt(platform="nangate45", symptom=(sig, symptom.symptom_id(sig)),
                              delta={"edits": {"LVS_MAX_DEPTH": "64", "POST_DETAIL_ROUTE_TCL": "@asset:ft.tcl"},
                                     "assets": {"ft.tcl": HOOK}, "rejected": [], "ignored": {}}))["recipe"]
    c = sqlite3.connect(tmp_path / "k.sqlite"); lr.record(c, rec, "nangate45_C1", "repoA", {})
    lr.record(c, rec, "nangate45_C2", "repoB", {}); c.close()
    out = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs", "--next"],
                         capture_output=True, text=True, env=env).stdout.strip()
    sid, rerun, recheck = out.split("\t")
    assert sid == "learned:" + rec["recipe_id"] and rerun == "route" and recheck == "lvs"
    r = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs", "--apply", sid],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = (p / "constraints" / "config.mk").read_text()
    assert "export LVS_MAX_DEPTH = 64" in cfg and "hooks/learned/" in cfg
    # disabled -> back to R2G's own behaviour
    off = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs", "--next"],
                         capture_output=True, text=True, env={**env, "R2G_LEARNED_RECIPES": "0"}).stdout.strip()
    assert off.startswith("STOP")


def test_read_only_hooks_and_aliases_are_not_part_of_the_fix():
    probe = "set f [open lvs.rpt w]\nforeach n [[ord::get_db_block] getNets] { puts [$n getName] }\n"
    fix = "foreach n $nets { odb::dbNet_create $block x }\n"
    assert lr.hook_is_read_only(probe) is True                     # writing a report changes no design
    probe2 = "foreach n [[ord::get_db_block] getNets] { puts [$n getName] }\nreport_checks\n"
    assert lr.hook_is_read_only(probe2) is True
    assert lr.hook_is_read_only(fix) is False
    assert lr.hook_is_read_only("source other.tcl\n") is False     # can run anything: conservative
    r2g = {"DESIGN_NAME": "t"}
    llm = {"DESIGN_NAME": "t", "FT": "/p/hooks/ft.tcl", "POST_GLOBAL_PLACE_TCL": "$(FT)",
           "POST_FINAL_REPORT_TCL": "/p/hooks/probe.tcl"}
    out = lr.config_delta(r2g, llm, {}, {"hooks/ft.tcl": fix.encode(), "hooks/probe.tcl": probe2.encode()})
    assert out["edits"] == {"POST_GLOBAL_PLACE_TCL": "@asset:ft.tcl"}
    assert set(out["assets"]) == {"ft.tcl"} and "POST_FINAL_REPORT_TCL" in out["ignored"] and "FT" in out["ignored"]


def test_assess_rejects_unreasonable_or_design_specific_fixes():
    kv = {"ABC_AREA", "CORE_AREA", "DIE_AREA", "SKIP_INCREMENTAL_REPAIR", "POST_GLOBAL_PLACE_TCL",
          "PWR_NETS_VOLTAGES", "ENABLE_PLACE_REPAIR_TIMING", "IO_PLACER_V"}
    assert lr.assess({"ABC_AREA": "0"}, {}, known_vars=kv) == []
    assert lr.assess({"LVS_MAX_DEPTH": "64"}, {}, known_vars=kv) == ["unknown_variable:LVS_MAX_DEPTH"]
    assert lr.assess({"SKIP_INCREMENTAL_REPAIR": "1"}, {}, known_vars=kv) == ["suppresses_repair_or_check:SKIP_INCREMENTAL_REPAIR"]
    assert lr.assess({"ENABLE_PLACE_REPAIR_TIMING": "0"}, {}, known_vars=kv)[0].startswith("suppresses")
    assert lr.assess({"PWR_NETS_VOLTAGES": ""}, {}, known_vars=kv)[0].startswith("suppresses")
    assert lr.assess({"CORE_AREA": "1 1 9 9"}, {}, known_vars=kv) == ["design_specific_geometry:CORE_AREA"]
    assert lr.assess({k: "1" for k in ("A1", "A2", "A3", "A4", "A5")}, {})[-1] == "too_broad:5_edits"
    hook = {"ft.tcl": "set n [$block findNet uart_tx_reg]\nodb::dbNet_create $block x\n"}
    assert lr.assess({"POST_GLOBAL_PLACE_TCL": "@asset:ft.tcl"}, hook, known_vars=kv, design_tokens=["uart_tx_reg"]) \
        == ["design_specific_identifier:ft.tcl"]
    assert lr.assess({"POST_GLOBAL_PLACE_TCL": "@asset:x.tcl"}, {"x.tcl": "source /proj/a/b.tcl\n"}, known_vars=kv) \
        == ["hardcoded_path:x.tcl"]
    r = lr.distill(_attempt(known_vars=kv, delta={"edits": {"SKIP_INCREMENTAL_REPAIR": "1"}, "assets": {},
                                                  "rejected": [], "ignored": {}}))
    assert r["rejected"].startswith("suppresses_repair_or_check")


def test_names_in_comments_do_not_make_a_hook_design_specific():
    hook = {"ft.tcl": "# fixes uart_top's shorted ports, see /proj/x/log\nodb::dbNet_create $block ft ;# for uart_top\n"}
    assert lr.assess({"POST_GLOBAL_PLACE_TCL": "@asset:ft.tcl"}, hook, design_tokens=["uart_top"]) == []
    vmap = {"m.v": "// generated for uart_top from /data/x\nmodule \\$_ALDFF_PP_ (input C); /* uart_top */ endmodule\n"}
    assert lr.assess({"LATCH_MAP_FILE": "@asset:m.v"}, vmap, design_tokens=["uart_top"]) == []
