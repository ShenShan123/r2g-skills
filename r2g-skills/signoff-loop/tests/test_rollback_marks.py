"""P1b / D2: fix_signoff.sh marks every fix-log row rolled_back / rollback_reason at the rollback sites,
and a rollback restores the config from immediately before THAT step (never a stale backup)."""
import json
import os
import stat
import subprocess
from pathlib import Path

FIX_SIGNOFF = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "fix_signoff.sh"


def _stub(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _route_project(tmp_path, steps):
    """steps: [(strategy, rerun_from)]; run_orfs fails for tehm_fail*, every step leaves route at 5."""
    return _route_project_with(tmp_path, tmp_path / "proj", steps, None)


def _route_project_with(tmp_path, proj, steps, run_orfs_body):
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text("export DESIGN_NAME = demo\nexport CORE_UTILIZATION = 11\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "fail", "total_violations": 5}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    lines = "\n".join(f'    {s}) rf="{rf}";;' for s, rf in steps)
    queue = " ".join(s for s, _ in steps)
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  for s in {queue}; do grep -qx "$s" {state}/tried 2>/dev/null && continue\n'
          f'    [[ "$s" == tehm_* && "$R2G_MEMORY_BACKEND" == none ]] && continue\n'
          f'    case "$s" in\n{lines}\n    esac\n'
          f'    echo "$s" >> {state}/tried; echo -e "$s\\t$rf\\troute"; exit 0; done; echo -e "STOP\\tresidual\\tnone"\n'
          f'elif [[ "$*" == *"--apply"* ]]; then s=$(tail -1 {state}/tried); echo "$s" > {state}/current\n'
          f'  echo "export EDIT_$s = 1" >> {proj}/constraints/config.mk\n'
          f'  echo "{{\\"applied\\":\\"$s\\",\\"config_edits\\":{{}}}}"; fi')
    _stub(bindir / "run_orfs.sh", run_orfs_body or f's=$(cat {state}/current); [[ "$s" == tehm_fail* ]] && exit 2; exit 0')
    _stub(bindir / "extract_route.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\nopen(sys.argv[2],"w").write(json.dumps({"status":"fail","total_violations":5}))\nPY')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("R2G_BUDGET", "R2G_LEDGER"))}
    env.update(R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"), R2G_MEMORY_BACKEND="tehm")
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "route", "--max-iters", "6"],
                       env=env, capture_output=True, text=True)
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines()]
    return r, proj, {x["strategy"]: x for x in rows}


def test_rollback_sites_mark_rows_and_other_rows_are_explicitly_not_rolled_back(tmp_path):
    r, proj, rows = _route_project(tmp_path, [("tehm_fail_a", "floorplan"), ("cat_b", "floorplan"),
                                              ("tehm_noimp", "floorplan")])
    assert rows["tehm_fail_a"]["rolled_back"] is True
    assert rows["tehm_fail_a"]["rollback_reason"] == "rerun_failed_rc2", r.stdout + r.stderr
    assert rows["cat_b"]["rolled_back"] is False and rows["cat_b"]["rollback_reason"] is None
    assert rows["tehm_noimp"]["rolled_back"] is True and rows["tehm_noimp"]["rollback_reason"] == "no_improvement"


def test_a_memory_step_without_a_rerun_rolls_back_to_the_config_just_before_it(tmp_path):
    # cat_a (with a rerun) keeps its edit; tehm_x has NO rerun and does not improve -> rolled back.
    # The rollback must restore the config after cat_a, not the stale pre-cat_a backup.
    r, proj, rows = _route_project(tmp_path, [("cat_a", "floorplan"), ("tehm_x", "")])
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert rows["tehm_x"]["rolled_back"] is True, r.stdout + r.stderr
    assert "EDIT_cat_a" in cfg and "EDIT_tehm_x" not in cfg


def _crash_project(tmp_path, make_run):
    """One catalogue DRC-free route step whose rerun exits 2; make_run: whether the rerun creates its run."""
    proj = tmp_path / "proj"
    old = proj / "backend" / "RUN_2026-01-01_00-00-00_old"
    old.mkdir(parents=True)
    (old / "stage_log.jsonl").write_text(json.dumps({"stage": "place", "status": 2}) + "\n")
    (old / "flow.log").write_text("[ERROR DPL-0036] Detailed placement failed.\n")   # the ORIGINAL failure
    os.utime(old, (1, 1))
    body = ('d="$1/backend/RUN_new"; mkdir -p "$d"; echo \'{"stage": "floorplan", "status": 2}\' > "$d/stage_log.jsonl"; '
            'echo "[ERROR PDN-0185] Insufficient width" > "$d/flow.log"; exit 2') if make_run else "exit 2"
    return proj, body


def test_a_failed_rerun_records_the_diagnosis_of_the_run_it_created(tmp_path):
    for make_run, expect in ((True, ("RUN_new", "PDN-0185")), (False, (None, None))):
        t = tmp_path / str(make_run)
        t.mkdir()
        proj, body = _crash_project(t, make_run)
        r, proj, rows = _route_project_with(t, proj, [("cat_x", "floorplan")], body)
        diag = rows["cat_x"]["rerun_failure"]
        assert (diag["rc"], diag["run"], diag["error_code"]) == (2, *expect), r.stdout + r.stderr
        if not make_run:                      # never inherits the previous run's DPL-0036
            assert diag["stale_run"] == "RUN_2026-01-01_00-00-00_old"
