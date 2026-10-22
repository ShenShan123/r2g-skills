"""P1c / D-R1: the restoration review's checks (writer ordering audit, exception checks)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tehm.restoration import audit_writer_version  # noqa: E402

_LOG = '''_log_iter() {  # writes the row
cfgp=os.path.join(proj,"constraints","config.mk")
       cumulative_config=json.dumps(cum,sort_keys=True),
}
'''


def test_the_ordering_audit_accepts_rollback_before_log():
    s = _LOG + '''fix_one() {
  while true; do
    if bad; then
      _memory_rollback
      _log_iter "$check" "$it" "$sid" "$before" "$before" "no_improvement"
      continue
    fi
    _log_iter "$check" "$it" "$sid" "$before" "$after" "applied"
  done
}
'''
    a = audit_writer_version(s)
    assert a["log_after_rollback"] and a["cumulative_at_log_time"] and len(a["log_calls"]) == 2


def test_the_ordering_audit_flags_a_rollback_after_the_log_in_the_same_iteration():
    s = _LOG + '''fix_one() {
  while true; do
    _log_iter "$check" "$it" "$sid" "$before" "$after" "applied"
    if regressed; then
      [[ -f "$cfg_backup" ]] && cp -f "$cfg_backup" "$PROJECT_DIR/constraints/config.mk"
    fi
  done
}
'''
    a = audit_writer_version(s)
    assert not a["log_after_rollback"] and a["violations"][0]["log_line"] < a["violations"][0]["rollback_line"]


def test_the_audit_requires_cumulative_read_at_log_time():
    assert not audit_writer_version('_log_iter "x"\n')["cumulative_at_log_time"]


def _proj(tmp_path, rows):
    import json
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 15\nexport PLACE_DENSITY_LB_ADDON = 0.2\n")
    (p / "backend" / "RUN_2026-10-05_10-00-00_1_ab").mkdir(parents=True)
    (p / "backend" / "RUN_2026-10-05_10-00-00_1_ab" / "stage_log.jsonl").write_text(json.dumps({"stage": "floorplan", "status": 0}) + "\n")
    return p, [(i, r) for i, r in enumerate(rows, 1)]


def _unit(row):
    return {"unit_id": "u", "raw_row": row, "outcome": {"verified": True, "canonical": "PASS"}}


def _frow(ts, delta, cum, **kw):
    import json
    r = {"ts": ts, "config_delta": delta, "cumulative_config": json.dumps(cum), "env_flags": "{}", "predicates": {},
         "from_stage": "floorplan", "fix_session_id": "s", "check": "drc", "strategy": "x"}
    r.update(kw)
    return r


AUDIT = [{"commit": "c", "date": "2026-10-01T00:00:00-07:00", "log_after_rollback": True, "cumulative_at_log_time": True}]


def test_a_restated_knob_is_recorded_and_an_action_that_changes_nothing_is_never_restored(tmp_path):
    from tehm.restoration import review_fix_signoff_unit
    row = _frow("2026-10-05T10:05:00-07:00", {"CORE_UTILIZATION": "20", "PLACE_DENSITY_LB_ADDON": "0.2"},
                {"CORE_UTILIZATION": "20", "PLACE_DENSITY_LB_ADDON": "0.2"})
    p, rows = _proj(tmp_path, [row])
    r = review_fix_signoff_unit(_unit(row), project_rows=rows, project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert r["restored"] and r["refs"]["noop_knobs"] == ["PLACE_DENSITY_LB_ADDON"]
    noop = _frow("2026-10-05T10:05:00-07:00", {"CORE_UTILIZATION": "15"}, {"CORE_UTILIZATION": "15"})
    r = review_fix_signoff_unit(_unit(noop), project_rows=[(1, noop)], project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert not r["restored"] and not r["checks"]["edit_changes_a_value"]


def test_a_partial_edit_or_a_missing_execution_is_not_restored(tmp_path):
    from tehm.restoration import review_fix_signoff_unit
    part = _frow("2026-10-05T10:05:00-07:00", {"CORE_UTILIZATION": "20", "X": "1"}, {"CORE_UTILIZATION": "20"})
    p, rows = _proj(tmp_path, [part])
    r = review_fix_signoff_unit(_unit(part), project_rows=rows, project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert not r["restored"] and not r["checks"]["edit_complete_in_logged_config"]
    early = _frow("2026-10-05T09:00:00-07:00", {"CORE_UTILIZATION": "20"}, {"CORE_UTILIZATION": "20"})
    r = review_fix_signoff_unit(_unit(early), project_rows=[(1, early)], project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert not r["restored"] and not r["checks"]["execution_located"]          # the only run started after it
    r = review_fix_signoff_unit(_unit(part), project_rows=rows, project_dir=p, source_ref={},
                                writer_audits=[{**AUDIT[0], "log_after_rollback": False}])
    assert not r["checks"]["writer_logs_after_rollback"] and not r["restored"]
    ok = _frow("2026-10-05T10:05:00-07:00", {"CORE_UTILIZATION": "20"}, {"CORE_UTILIZATION": "20"})
    r = review_fix_signoff_unit(_unit(ok), project_rows=[(1, ok)], project_dir=p, source_ref={},
                                writer_audits=[{**AUDIT[0], "date": "2026-10-06T00:00:00-07:00"}])
    assert not r["restored"] and not r["checks"]["writer_range_nonempty"]    # no audited version by its log time


def test_a_later_reversal_is_reported_separately_and_other_writers_are_ignored(tmp_path):
    from tehm.restoration import review_fix_signoff_unit
    row = _frow("2026-10-05T10:05:00-07:00", {"CORE_UTILIZATION": "20"}, {"CORE_UTILIZATION": "20"})
    later_same = _frow("2026-10-05T11:00:00-07:00", {"X": "1"}, {"CORE_UTILIZATION": "15", "X": "1"})
    later_other = {"ts": "2026-10-05T10:30:00-07:00", "cumulative_config": "{}", "strategy": "pin_perimeter_floor"}
    p, rows = _proj(tmp_path, [row, later_other])
    r = review_fix_signoff_unit(_unit(row), project_rows=rows, project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert r["restored"] and r["later_step_review"]["status"] == "no_later_rows" and r["final_retained_fix"] == "unknown"
    p, rows = _proj(tmp_path / "b", [row, later_same])
    r = review_fix_signoff_unit(_unit(row), project_rows=rows, project_dir=p, source_ref={}, writer_audits=AUDIT)
    assert r["restored"] and r["later_step_review"]["status"] == "reverted_by_a_later_step"
    assert r["final_retained_fix"] is False                                 # verified at its step, not retained


def test_a_trial_is_restored_only_from_its_own_project_after_the_action(tmp_path):
    import json
    from tehm.restoration import review_trial_unit
    t = tmp_path / "g_trial__01"
    (t / "constraints").mkdir(parents=True)
    (t / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 70\n")
    (t / "backend" / "RUN_2026-10-02_09-00-00_1_ab").mkdir(parents=True)
    (t / "backend" / "RUN_2026-10-02_09-00-00_1_ab" / "stage_log.jsonl").write_text(
        json.dumps({"stage": "floorplan", "status": 0}) + "\n")
    row = {"config_delta": {"CORE_UTILIZATION": "70"}, "stage_ok": True, "evidence_tier": "component_trial"}
    h = {"no_rollback": True}
    assert review_trial_unit(_unit(row), trial_project=t, harness_audit=h, source_ref={})["restored"]
    assert not review_trial_unit(_unit({**row, "config_delta": {"CORE_UTILIZATION": "60"}}), trial_project=t,
                                 harness_audit=h, source_ref={})["restored"]           # the project lacks the edit
    assert not review_trial_unit(_unit({**row, "stage_ok": False}), trial_project=t, harness_audit=h,
                                 source_ref={})["restored"]                              # the run contradicts the row
    assert not review_trial_unit(_unit(row), trial_project=None, harness_audit=h, source_ref={})["restored"]
    assert not review_trial_unit(_unit(row), trial_project=t, harness_audit={"no_rollback": False},
                                 source_ref={})["restored"]


def test_an_engineer_loop_retry_run_is_found_before_the_retrys_own_signoff_rows(tmp_path):
    import json
    from tehm.restoration import review_engineer_loop_unit
    p = tmp_path / "ab"
    (p / "backend" / "RUN_2026-10-03_15-52-17_1_ab").mkdir(parents=True)
    (p / "backend" / "RUN_2026-10-03_15-52-17_1_ab" / "stage_log.jsonl").write_text(
        json.dumps({"stage": "synth", "status": 0}) + "\n")
    delta = {"CORE_UTILIZATION": {"before": "89", "after": "50"}}
    rec = {"ts": "2026-10-03T16:32:08-07:00", "before_status": "fail", "effect_fingerprint": "f",
           "config_delta": json.dumps(delta), "cumulative_config": json.dumps({"CORE_UTILIZATION": "50"})}
    signoff = {"ts": "2026-10-03T16:32:05-07:00", "strategy": "none", "env_flags": "{}", "predicates": {}}
    audits = [{"commit": "c", "date": "2026-10-01T00:00:00-07:00", "ok": True}]
    r = review_engineer_loop_unit(_unit(rec), project_rows=[(1, signoff), (2, rec)], project_dir=p, source_ref={},
                                  executor_audits=audits)
    assert r["restored"], r["checks"]
    assert not review_engineer_loop_unit(_unit(rec), project_rows=[(1, signoff), (2, rec)], project_dir=p,
                                         source_ref={}, executor_audits=[{**audits[0], "ok": False}])["restored"]
    back = {**rec, "ts": "2026-10-03T17:00:00-07:00", "config_delta": "{}",
            "cumulative_config": json.dumps({"CORE_UTILIZATION": "89"})}
    r = review_engineer_loop_unit(_unit(rec), project_rows=[(1, signoff), (2, rec), (3, back)], project_dir=p,
                                  source_ref={}, executor_audits=audits)
    assert r["later_step_review"]["status"] == "reverted_by_a_later_step" and r["final_retained_fix"] is False


def test_the_engineer_loop_audit_flags_a_restore_or_a_snapshot_taken_after_the_retry():
    from tehm.restoration import audit_engineer_loop_version
    good = '''def recover(entry):
    entry["_r2g_config_effect"] = _config_effect(before, _config_snapshot(entry))
    result = process_one(led, entry, conn, _resized=True)
    _record_resize_fix(entry, cleared=(result == "clean"))
'''
    assert audit_engineer_loop_version(good)["ok"]
    restore = good.replace("    _record_resize_fix", "    _restore_structured_delta_before(dst, delta)\n    _record_resize_fix")
    assert not audit_engineer_loop_version(restore)["ok"]
    late = '''def recover(entry):
    result = process_one(led, entry, conn, _resized=True)
    entry["_r2g_config_effect"] = _config_effect(before, _config_snapshot(entry))
    _record_resize_fix(entry, cleared=(result == "clean"))
'''
    assert not audit_engineer_loop_version(late)["ok"]


def test_an_llm_row_is_restored_only_from_a_unique_record_whose_final_config_keeps_the_edit(tmp_path):
    from tehm.restoration import review_llm_unit
    run = tmp_path / "backend" / "RUN_x"
    run.mkdir(parents=True)
    row = {"config_delta": {"CORE_UTILIZATION": "45"}, "config_before": {"CORE_UTILIZATION": "66"}, "llm": True}
    m = {"record": "r.json", "iteration": {}, "final_config": "export CORE_UTILIZATION = 66\nexport CORE_UTILIZATION = 45\n",
         "judged_run": str(run), "date": None}
    h = [{"commit": "c", "date": "2026-10-01T00:00:00-07:00", "ok": True}]
    assert review_llm_unit(_unit(row), matches=[m], harness_audits=h, source_ref={})["restored"]
    assert not review_llm_unit(_unit(row), matches=[m, {**m, "record": "s.json"}], harness_audits=h,
                               source_ref={})["restored"]                                  # ambiguous
    gone = {**m, "final_config": "export CORE_UTILIZATION = 66\n"}
    assert not review_llm_unit(_unit(row), matches=[gone], harness_audits=h, source_ref={})["restored"]
    assert not review_llm_unit(_unit(row), matches=[m], harness_audits=[{**h[0], "ok": False}],
                               source_ref={})["restored"]
