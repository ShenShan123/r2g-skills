"""Chain log v1 (T2 design draft §8d, user 2026-10-17): every fix-log row must name the run it
acted on, the run it produced, its own action id and the session's previous action, and the
re-check bound to the run that was graded, so the ann-v2 verifier binds new episodes from
explicit links instead of searching (failure-patterns "History annotator cited a row field
that no on-disk row has").

The stub reflow calls the REAL run_orfs.sh helper `_r2g_record_action`, so the action -> run
link under test is the one production writes. The fixture is the regression-rollback one:
the re-check must be captured BEFORE the rollback restores the reports and the run pointer.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fix_signoff_regression_rollback as RR  # noqa: E402

SKILL = Path(__file__).resolve().parents[1]
RUN_ORFS = SKILL / "scripts" / "flow" / "run_orfs.sh"


def _harness(tmp_path: Path, proj: Path) -> dict:
    env = RR._regressing_harness(tmp_path, proj)
    runner = Path(env["R2G_RUN_ORFS"])
    runner.write_text(runner.read_text() + (
        f'\nR2G_SOURCE_ONLY=1 source "{RUN_ORFS}"\n'
        f'_r2g_record_action "{proj}/backend/{RR.REJECTED}" "{RR.REJECTED}"\n'))
    return env


def _rows(proj: Path) -> list:
    return [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]


def test_rolled_back_step_names_pre_and_post_runs_and_the_graded_check(tmp_path):
    proj = RR._project(tmp_path)
    RR._run(proj, _harness(tmp_path, proj))
    rows = _rows(proj)
    step = next(r for r in rows if r["strategy"] == "density_relief")
    sid = step["fix_session_id"]
    assert step["chain_version"] == 1
    assert step["action_id"] == f"{sid}#1" and step["parent_action"] is None
    assert step["pre_run_tag"] == RR.ACCEPTED                     # the active run when the step began
    assert step["post_run_tag"] == RR.REJECTED                    # named by the run itself
    rec = json.loads((proj / "backend" / RR.REJECTED / "r2g_action.json").read_text())
    assert rec["action_id"] == step["action_id"] and rec["run_tag"] == RR.REJECTED
    sc = step["step_check"]                                       # captured before the rollback
    assert sc["check"] == "drc" and sc["report_run_tag"] == RR.REJECTED and sc["graded_post_run"] is True
    assert sc["count"] == "1" and len(sc["report_sha256"]) == 64
    assert step["rolled_back"] is True and step["rollback_reason"] == "regression"
    # the rollback restored the pointer: the NEXT action starts from the accepted run
    assert json.loads((proj / "backend" / ".r2g_signoff_run").read_text())["run_tag"] == RR.ACCEPTED


def test_the_next_row_chains_to_its_parent_and_starts_from_the_restored_run(tmp_path):
    proj = RR._project(tmp_path)
    env = _harness(tmp_path, proj)
    subprocess.run(["bash", str(RR.FIX_SIGNOFF), str(proj), "sky130hs", "--check", "drc", "--max-iters", "2"],
                   env=env, check=False, capture_output=True, text=True)
    rows = _rows(proj)
    step, stop = rows[0], rows[1]
    assert stop["verdict"].startswith("stop_") and stop["fix_session_id"] == step["fix_session_id"]
    assert stop["action_id"] == step["action_id"][:-1] + "2" and stop["parent_action"] == step["action_id"]
    assert stop["pre_run_tag"] == RR.ACCEPTED and stop["post_run_tag"] is None and stop["step_check"] is None


def test_without_an_action_record_the_post_run_is_null_never_guessed(tmp_path):
    proj = RR._project(tmp_path)
    RR._run(proj, RR._regressing_harness(tmp_path, proj))         # stub writes no r2g_action.json
    step = next(r for r in _rows(proj) if r["strategy"] == "density_relief")
    assert step["post_run_tag"] is None and step["pre_run_tag"] == RR.ACCEPTED
    assert "graded_post_run" not in step["step_check"]


def test_run_orfs_records_the_launching_action_only_when_given(tmp_path):
    def call(env_extra):
        d = tmp_path / ("run_" + str(len(list(tmp_path.iterdir()))))
        d.mkdir()
        env = {"R2G_SOURCE_ONLY": "1", "PATH": "/usr/bin:/bin", **env_extra}
        subprocess.run(["bash", "-c", f'source "{RUN_ORFS}"; _r2g_record_action "{d}" RUN_X'],
                       env=env, check=True, capture_output=True, text=True)
        return d / "r2g_action.json"
    assert not call({}).exists()
    f = call({"R2G_ACTION_ID": 'sess#3 "quoted"'})
    assert json.loads(f.read_text())["action_id"] == 'sess#3 "quoted"'
