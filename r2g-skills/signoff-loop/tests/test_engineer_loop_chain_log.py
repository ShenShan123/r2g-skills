"""Chain log v1 for engineer-loop recoveries (T2 design draft §8d, 2026-10-17).

SOURCE-v1 recovery rows (resize/pin-perimeter/PDN/synth-memory/memory recoveries) named no
run, so no place abort in history could be bound to the run it produced. Now the retry runs
under an explicit action id that run_orfs records in the new run, and the row names the run
the recovery responded to and the run the retry produced.
"""
import json
import os
from pathlib import Path

import engineer_loop as EL


def _run(proj: Path, tag: str, stages, action=None, mtime=0):
    d = proj / "backend" / tag
    d.mkdir(parents=True)
    (d / "stage_log.jsonl").write_text("".join(json.dumps({"stage": s, "status": st}) + "\n" for s, st in stages))
    if action:
        (d / "r2g_action.json").write_text(json.dumps({"action_id": action, "run_tag": tag}))
    os.utime(d, (mtime, mtime))
    return d


def _entry(tmp_path):
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    _run(proj, "RUN_FAIL", [("synth", 0), ("floorplan", 0), ("place", 2)], mtime=1_700_000_000)
    return {"project_path": str(proj), "design": "d",
            "_r2g_config_effect": {"delta": {"CORE_UTILIZATION": {"before": "60", "after": "36"}}}}, proj


def test_a_recovery_row_names_its_pre_and_post_runs(tmp_path):
    entry, proj = _entry(tmp_path)
    assert "R2G_ACTION_ID" not in os.environ
    with EL._chain_action(entry) as chain:
        assert os.environ["R2G_ACTION_ID"] == chain["action_id"]       # inherited by run_orfs
        _run(proj, "RUN_RETRY", [("floorplan", 0), ("place", 0), ("cts", 0)], action=chain["action_id"],
             mtime=1_800_000_000)
        _run(proj, "RUN_NESTED", [("route", 0)], action="sess#1", mtime=1_900_000_000)  # a nested fix's run
    assert "R2G_ACTION_ID" not in os.environ                           # restored
    EL._record_resize_fix(entry, cleared=True, chain=chain)
    row = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[-1])
    assert row["chain_version"] == 1 and row["action_id"] == chain["action_id"]
    assert row["pre_run_tag"] == "RUN_FAIL"                            # diagnosed before the retry
    assert row["post_run_tag"] == "RUN_RETRY"                          # not the newest run
    assert row["rolled_back"] is False and row["rollback_reason"] is None
    assert row["step_check"] == {"check": "orfs_stage", "stage": "place", "graded_run_tag": "RUN_RETRY",
                                 "last_stage": "cts", "last_status": 0}


def test_two_runs_naming_one_action_leave_the_post_run_null(tmp_path):
    entry, proj = _entry(tmp_path)
    with EL._chain_action(entry) as chain:
        _run(proj, "RUN_R1", [("place", 2)], action=chain["action_id"])
        _run(proj, "RUN_R2", [("place", 0)], action=chain["action_id"])
    EL._record_resize_fix(entry, cleared=False, chain=chain)
    row = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[-1])
    assert row["post_run_tag"] is None and row["post_run_ambiguous"] == ["RUN_R1", "RUN_R2"]
    assert row["step_check"] is None


def test_an_outer_action_id_is_restored_after_a_nested_recovery(tmp_path):
    entry, _ = _entry(tmp_path)
    with EL._chain_action(entry) as outer:
        with EL._chain_action(entry) as inner:
            assert os.environ["R2G_ACTION_ID"] == inner["action_id"] != outer["action_id"]
        assert os.environ["R2G_ACTION_ID"] == outer["action_id"]
    assert "R2G_ACTION_ID" not in os.environ


def test_without_a_chain_the_row_is_unchanged(tmp_path):
    entry, proj = _entry(tmp_path)
    EL._record_resize_fix(entry, cleared=True)
    row = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[-1])
    assert "chain_version" not in row and "rolled_back" not in row
