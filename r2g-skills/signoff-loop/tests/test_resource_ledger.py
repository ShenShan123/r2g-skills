"""P1a shared total budget: resource_ledger.py and its run_orfs.sh / fix_signoff.sh hooks.

Protocol v1 section 7.2: one total cap covers memory AND catalogue actions; a final-acceptance
reserve cannot be spent by repair; missing usage is UNKNOWN (never 0) and a capped resource with
an UNKNOWN charge fails closed. Spec: memory/evaluation/r2g_P0_event_resource_ledger_spec_20261008.md.
"""
import json
import hashlib
import os
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
LEDGER = FLOW / "resource_ledger.py"
FIX_SIGNOFF = FLOW / "fix_signoff.sh"
RUN_ORFS = FLOW / "run_orfs.sh"


def _ledger(proj, *args, budget=None, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("R2G_BUDGET", "R2G_LEDGER"))}
    if budget is not None:
        env["R2G_BUDGET_FILE"] = str(budget)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(LEDGER), *args[:1], str(proj), *args[1:]],
                          capture_output=True, text=True, env=env)


def _budget(tmp_path, caps, reserve=None):
    p = tmp_path / "budget.json"
    p.write_text(json.dumps({"budget_id": "t", "caps": caps, "reserve": reserve or {}}))
    return p


def _events(proj):
    return [json.loads(l) for l in (proj / "reports" / "resource_ledger.jsonl").read_text().splitlines()]


def test_without_a_budget_the_ledger_only_records(tmp_path):
    for _ in range(5):
        assert _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1").returncode == 0
    s = json.loads(_ledger(tmp_path, "summary", "--json").stdout)
    assert s["spent"]["flows"] == 5 and s["refused"] == 0 and s["caps"] == {}


def test_phase_directory_alias_retains_old_request_and_token_charges(tmp_path):
    # Correction reruns must continue the same payment bank, not obtain a fresh
    # allowance just because the episode root and runtime version changed.
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    budget=_budget(tmp_path,{'llm_requests':3})
    data=json.loads(budget.read_text());data['combined_tokens_cap']=100
    budget.write_text(json.dumps(data))
    for resource,amount in (('llm_requests','2'),('tokens_in','60'),('tokens_out','10')):
        assert _ledger(old,'charge','--resource',resource,'--amount',amount,budget=budget).returncode==0
    (new/'reports').symlink_to(old/'reports',target_is_directory=True)
    assert _ledger(new,'admit','--resource','llm_requests','--amount','1',budget=budget).returncode==0
    assert _ledger(old,'admit','--resource','llm_requests','--amount','1',budget=budget).returncode==75
    assert _ledger(new,'admit','--resource','tokens_in','--amount','40',budget=budget).returncode==75
    assert _ledger(new,'admit','--resource','tokens_in','--amount','10',budget=budget).returncode==0
    a=json.loads(_ledger(old,'summary','--json',budget=budget).stdout)
    b=json.loads(_ledger(new,'summary','--json',budget=budget).stdout)
    assert a['spent']['llm_requests']==b['spent']['llm_requests']==3
    assert a['spent']['tokens_in']==b['spent']['tokens_in']==70
    assert (new/'reports/resource_ledger.jsonl').samefile(old/'reports/resource_ledger.jsonl')


def test_memory_and_catalogue_share_one_cap_and_the_reserve_is_kept_for_final_acceptance(tmp_path):
    b = _budget(tmp_path, {"flows": 3}, {"flows": 1})
    assert _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", "--component", "memory", budget=b).returncode == 0
    assert _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", "--component", "catalogue", budget=b).returncode == 0
    r = _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", "--component", "catalogue", budget=b)
    assert r.returncode == 75 and "BUDGET EXHAUSTED" in r.stderr          # would cut into the reserve
    fin = _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", "--purpose", "final_acceptance",
                  "--component", "final_acceptance", budget=b)
    assert fin.returncode == 0                                             # the reserve is for this
    assert _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", "--purpose", "final_acceptance",
                   budget=b).returncode == 75                              # cap itself is hard
    s = json.loads(_ledger(tmp_path, "summary", "--json", budget=b).stdout)
    assert s["spent"]["flows"] == 3 and s["refused"] == 2
    assert s["flows_by_component"] == {"memory": 1, "catalogue": 1, "final_acceptance": 1}


def test_unknown_usage_is_never_zero_and_fails_closed_under_a_cap(tmp_path):
    assert _ledger(tmp_path, "charge", "--resource", "tokens_in", "--amount", "UNKNOWN").returncode == 0
    s = json.loads(_ledger(tmp_path, "summary", "--json").stdout)
    assert s["spent"]["tokens_in"] == "UNKNOWN"
    b = _budget(tmp_path, {"tokens_in": 1000, "flows": 10})
    r = _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", budget=b)
    assert r.returncode == 75 and "UNKNOWN" in r.stderr


def test_wall_time_counts_from_the_attempts_first_event(tmp_path):
    (tmp_path / "reports").mkdir()
    old = {"event_seq": 1, "ts": time.time() - 100, "type": "attempt_start"}
    (tmp_path / "reports" / "resource_ledger.jsonl").write_text(json.dumps(old) + "\n")
    b = _budget(tmp_path, {"wall_s": 120}, {"wall_s": 30})
    r = _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", budget=b)
    assert r.returncode == 75 and "wall_s" in r.stderr                     # 100 s used > 120 - 30


def test_bad_budget_file_is_a_usage_error(tmp_path):
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"caps": {"gpu_hours": 1}}))
    assert _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1", budget=b).returncode == 2


def test_combined_tokens_enforce_shared_input_and_output_holds(tmp_path):
    b = tmp_path / 'combined.json'
    b.write_text(json.dumps({'caps': {'llm_requests': 5000}, 'combined_tokens_cap': 100}))
    hold = _ledger(tmp_path, 'admit', '--hold', '--need', 'tokens_in=60', '--need', 'tokens_out=30', budget=b)
    assert hold.returncode == 0
    assert _ledger(tmp_path, 'admit', '--hold', '--need', 'tokens_in=6', '--need', 'tokens_out=5', budget=b).returncode == 75
    assert _ledger(tmp_path, 'settle', '--hold-id', hold.stdout.strip(), '--actual', 'tokens_in=10',
                   '--actual', 'tokens_out=10', budget=b).returncode == 0
    assert json.loads(_ledger(tmp_path, 'remaining', budget=b).stdout)['combined_tokens'] == 80
    summary = json.loads(_ledger(tmp_path, 'summary', '--json', budget=b).stdout)
    assert summary['combined_tokens_cap'] == 100
    assert _ledger(tmp_path, 'charge', '--resource', 'tokens_out', '--amount', 'UNKNOWN', budget=b).returncode == 0
    assert _ledger(tmp_path, 'admit', '--hold', '--need', 'tokens_in=1', budget=b).returncode == 75


def test_combined_tokens_parallel_reservations_do_not_overrun(tmp_path):
    b = tmp_path / 'combined.json'
    b.write_text(json.dumps({'caps': {}, 'combined_tokens_cap': 100}))
    with ThreadPoolExecutor(8) as ex:
        rcs = list(ex.map(lambda _: _ledger(tmp_path, 'admit', '--hold', '--need', 'tokens_in=60',
                                           '--need', 'tokens_out=40', budget=b).returncode, range(8)))
    assert sorted(rcs) == [0] + [75] * 7


def _unknown_with_authorization(tmp_path, bound=60):
    b = tmp_path / 'combined.json'
    b.write_text(json.dumps({'caps': {'llm_requests': 10}, 'combined_tokens_cap': 100}))
    result = _ledger(tmp_path, 'admit', '--hold', '--need', 'llm_requests=1',
                     '--need', 'tokens_in=60', '--need', 'tokens_out=30', budget=b)
    assert result.returncode == 0
    hold = result.stdout.strip()
    assert _ledger(tmp_path, 'settle', '--hold-id', hold, '--actual', 'llm_requests=1',
                   '--actual', 'tokens_in=UNKNOWN', '--actual', 'tokens_out=UNKNOWN', budget=b).returncode == 0
    grant = tmp_path / 'authorization.json'
    grant.write_text(json.dumps({'schema': 'r2g-unknown-occupancy-authorization-v1',
        'user_approved': True, 'targets': [{'ledger_path': str(tmp_path/'reports/resource_ledger.jsonl'),
        'hold_id': hold, 'upper_bound': {'tokens_in': bound, 'tokens_out': 30}}]}))
    sha = hashlib.sha256(grant.read_bytes()).hexdigest()
    args = ('occupy-unknown', '--hold-id', hold, '--authorization', str(grant), '--authorization-sha256', sha)
    return b, hold, grant, args


def test_authorized_occupancy_preserves_unknown_actual_and_enforces_bound(tmp_path):
    b, hold, grant, args = _unknown_with_authorization(tmp_path)
    original = (tmp_path/'reports/resource_ledger.jsonl').read_bytes()
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=1', budget=b).returncode == 75
    assert _ledger(tmp_path, *args, budget=b).returncode == 0
    assert (tmp_path/'reports/resource_ledger.jsonl').read_bytes().startswith(original)
    summary = json.loads(_ledger(tmp_path, 'summary', '--json', budget=b).stdout)
    assert summary['spent']['tokens_in'] == summary['spent']['tokens_out'] == 'UNKNOWN'
    assert summary['budget_spent']['tokens_in'] == 60 and summary['budget_spent']['tokens_out'] == 30
    assert summary['spent']['llm_requests'] == summary['budget_spent']['llm_requests'] == 1
    assert json.loads(_ledger(tmp_path, 'remaining', budget=b).stdout)['combined_tokens'] == 10
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=11', budget=b).returncode == 75
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=10', budget=b).returncode == 0
    assert _ledger(tmp_path, *args, budget=b).returncode == 2
    assert sum(e['type']=='budget_unknown_occupancy' for e in _events(tmp_path)) == 1


def test_authorized_occupancy_cannot_reduce_original_reservation(tmp_path):
    b, hold, grant, args = _unknown_with_authorization(tmp_path, bound=59)
    assert _ledger(tmp_path, *args, budget=b).returncode == 2
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=1', budget=b).returncode == 75


def test_occupancy_grant_is_hash_bound_and_other_unknowns_still_stop(tmp_path):
    b, hold, grant, args = _unknown_with_authorization(tmp_path)
    assert _ledger(tmp_path, *args, budget=b).returncode == 0
    grant.write_text(grant.read_text()+' ')
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=1', budget=b).returncode != 0


def test_occupancy_is_only_for_the_named_settlement(tmp_path):
    b, hold, grant, args = _unknown_with_authorization(tmp_path)
    assert _ledger(tmp_path, *args, budget=b).returncode == 0
    assert _ledger(tmp_path, 'charge', '--resource', 'tokens_in', '--amount', 'UNKNOWN', budget=b).returncode == 0
    assert _ledger(tmp_path, 'admit', '--need', 'tokens_in=1', budget=b).returncode == 75


def test_concurrent_appends_get_unique_sequence_numbers(tmp_path):
    with ThreadPoolExecutor(8) as ex:
        rcs = list(ex.map(lambda _: _ledger(tmp_path, "charge", "--resource", "flows", "--amount", "1").returncode,
                          range(24)))
    assert rcs == [0] * 24
    assert sorted(e["event_seq"] for e in _events(tmp_path)) == list(range(1, 25))


def test_flow_run_reads_stage_wall_and_cpu_and_marks_a_killed_stage_unknown(tmp_path):
    run = tmp_path / "backend" / "RUN_x"
    (run / "logs").mkdir(parents=True)
    (run / "logs" / "5_1_grt.log").write_text(
        "...\nElapsed time: 1:02:03.50[h:]min:sec. CPU time: user 100.5 sys 4.5 (300%). Peak memory: 1KB.\n")
    (run / "logs" / "5_2_route.log").write_text("[INFO DRT] still routing\n")   # killed: no end line
    assert _ledger(tmp_path, "flow-run", "--run-dir", str(run), "--rc", "124").returncode == 0
    e = _events(tmp_path)[-1]
    st = {s["stage"]: s for s in e["stages"]}
    assert e["timeout"] is True and st["5_1_grt"] == {"stage": "5_1_grt", "wall_s": 3723.5, "cpu_s": 105.0}
    assert st["5_2_route"]["wall_s"] == "UNKNOWN" and st["5_2_route"]["cpu_s"] == "UNKNOWN"


def test_concurrent_admissions_cannot_both_pass_a_cap_that_fits_one(tmp_path):
    # read, check and reserve are one critical section: with a cap of 1, exactly one of 12 wins
    b = _budget(tmp_path, {"flows": 1})
    with ThreadPoolExecutor(12) as ex:
        rcs = list(ex.map(lambda _: _ledger(tmp_path, "admit", "--resource", "flows", "--amount", "1",
                                            budget=b).returncode, range(12)))
    assert sorted(rcs) == [0] + [75] * 11
    assert json.loads(_ledger(tmp_path, "summary", "--json", budget=b).stdout)["spent"]["flows"] == 1


def test_holds_count_until_settled_and_settle_replaces_them_with_actuals(tmp_path):
    b = _budget(tmp_path, {"tokens_out": 1000})
    r = _ledger(tmp_path, "admit", "--hold", "--need", "tokens_out=800", budget=b)
    hold = r.stdout.strip()
    assert r.returncode == 0 and hold
    assert _ledger(tmp_path, "admit", "--need", "tokens_out=300", budget=b).returncode == 75   # 800 booked
    assert _ledger(tmp_path, "settle", "--hold-id", hold, "--actual", "tokens_out=50", budget=b).returncode == 0
    assert _ledger(tmp_path, "admit", "--need", "tokens_out=300", budget=b).returncode == 0    # 50 + 300
    assert _ledger(tmp_path, "settle", "--hold-id", hold, budget=b).returncode == 2            # settled once


def test_remaining_reports_the_deadline_less_the_reserve(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "resource_ledger.jsonl").write_text(
        json.dumps({"event_seq": 1, "ts": time.time() - 100, "type": "attempt_start"}) + "\n")
    b = _budget(tmp_path, {"wall_s": 1000}, {"wall_s": 200})
    rem = json.loads(_ledger(tmp_path, "remaining", budget=b).stdout)
    assert 690 <= rem["wall_s"] <= 700 and abs(rem["deadline_epoch"] - (time.time() + rem["wall_s"])) < 5
    fin = json.loads(_ledger(tmp_path, "remaining", "--purpose", "final_acceptance", budget=b).stdout)
    assert 890 <= fin["wall_s"] <= 900


def test_run_orfs_bounds_every_stage_by_the_attempt_deadline():
    src = RUN_ORFS.read_text()
    assert 'remaining "$PROJECT_DIR"' in src and "deadline_epoch" in src
    stage = src[src.index("run_stage() {"):src.index("MAKE_STATUS=0")]
    assert '_left=$(( _R2G_DEADLINE - $(date +%s) - 60 ))' in stage           # less the 60 s kill grace
    assert 'timeout --signal=TERM --kill-after=60 "$_stage_to"' in stage      # not the bare ORFS_TIMEOUT
    assert "STAGE_STATUS=75" in stage                                         # deadline stop is a budget stop
    assert "exit 76" in src                                                   # accounting error is separate


def test_run_orfs_admits_before_creating_a_run_dir_and_records_every_exit():
    src = RUN_ORFS.read_text()
    admit, newdir = src.index('admit "$PROJECT_DIR" --resource flows'), src.index("_r2g_new_backend_dir \"$PROJECT_DIR")
    assert admit < newdir                                                  # refused flows create nothing
    assert "exit 75" in src[admit:newdir] and "trap _r2g_ledger_end EXIT" in src[admit:newdir]


# --- fix_signoff integration: a run_orfs stub that admits through the real ledger ---------------------
def _stub(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fix_project(tmp_path, steps, caps, reserve=None, rc_override=None):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text("export DESIGN_NAME = demo\nexport CORE_UTILIZATION = 11\n")
    (proj / "reports" / "route.json").write_text(json.dumps({"status": "fail", "total_violations": None}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    queue = " ".join(steps)
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  [[ -f {state}/clean ]] && {{ echo -e "STOP\\tclean\\tdone"; exit 0; }}\n'
          f'  for s in {queue}; do grep -qx "$s" {state}/tried 2>/dev/null && continue\n'
          f'    echo "$s" >> {state}/tried; echo -e "$s\\tfloorplan\\troute"; exit 0; done; echo -e "STOP\\tresidual\\tnone"\n'
          f'elif [[ "$*" == *"--apply"* ]]; then\n'
          f'  s=$(tail -1 {state}/tried); echo "$s" > {state}/current\n'
          f'  echo "export EDIT_$s = 1" >> {proj}/constraints/config.mk\n'
          f'  echo "{{\\"applied\\":\\"$s\\",\\"config_edits\\":{{}}}}"; fi')
    # the same admission run_orfs.sh performs, then: tehm_fail* fails, cat_fix fixes, others do nothing
    _stub(bindir / "run_orfs.sh",
          (f'exit {rc_override}\n' if rc_override else '') +
          f'{sys.executable} {LEDGER} admit "$1" --resource flows --amount 1 || exit $?\n'
          f'echo "$R2G_BUDGET_COMPONENT $(cat {state}/current)" >> {state}/launched\n'
          f's=$(cat {state}/current); [[ "$s" == tehm_fail* ]] && exit 2\n'
          f'[[ "$s" == cat_fix ]] && touch {state}/clean; exit 0')
    _stub(bindir / "extract_route.py",
          f'python3 - "$@" <<\'PY\'\nimport json,os,sys\nclean=os.path.exists("{state}/clean")\n'
          f'open(sys.argv[2],"w").write(json.dumps({{"status":"clean","total_violations":0}} if clean '
          f'else {{"status":"fail","total_violations":None}}))\nPY')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("R2G_BUDGET", "R2G_LEDGER"))}
    env.update(R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"), R2G_MEMORY_BACKEND="tehm",
               R2G_BUDGET_FILE=str(_budget(tmp_path, caps, reserve)))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "route", "--max-iters", "6"],
                       env=env, capture_output=True, text=True)
    return r, proj, state


def test_memory_trials_are_charged_to_the_same_total_as_the_catalogue(tmp_path):
    r, proj, state = _fix_project(tmp_path, ["tehm_fail_a", "cat_fix"], {"flows": 5})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (state / "launched").read_text().split("\n")[:2] == ["memory tehm_fail_a", "catalogue cat_fix"]
    s = json.loads(_ledger(proj, "summary", "--json").stdout)
    assert s["flows_by_component"] == {"memory": 1, "catalogue": 1}


def test_accounting_error_stops_the_session_separately_from_exhaustion(tmp_path):
    r, proj, state = _fix_project(tmp_path, ["cat_fix"], {"flows": 5}, rc_override=76)
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines()]
    assert rows[-1]["verdict"] == "accounting_error" and "EDIT_cat_fix" not in (proj / "constraints" / "config.mk").read_text()


def test_exhausted_budget_stops_the_session_restores_the_state_and_is_not_a_failed_rerun(tmp_path):
    # 2 flows total, 0 reserve: the memory trial and one catalogue step run; the third step is refused
    r, proj, state = _fix_project(tmp_path, ["tehm_fail_a", "cat_noop", "cat_fix"], {"flows": 2})
    assert "shared budget: budget_exhausted before/during cat_fix" in r.stderr
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines()]
    assert rows[-1]["strategy"] == "cat_fix" and rows[-1]["verdict"] == "budget_exhausted"
    assert not any(x["verdict"].startswith("rerun_failed") and x["strategy"] == "cat_fix" for x in rows)
    assert "EDIT_cat_fix" not in (proj / "constraints" / "config.mk").read_text()   # refused edit rolled back
    assert not (state / "clean").exists()
    assert json.loads(_ledger(proj, "summary", "--json").stdout)["refused"] == 1


# --- signoff checkers (Magic DRC, KLayout/Netgen LVS, RCX) are bounded by the same deadline ------------
BOUNDED = FLOW / "_bounded_run.sh"


def _bounded(tmp_path, deadline_offset, timeout, cmd):
    marker = tmp_path / "ran"
    script = (f'source "{BOUNDED}"; t0=$SECONDS; '
              f'R2G_CHECKER_DEADLINE=$(( $(date +%s) + {deadline_offset} )) '
              f'r2g_bounded_run {timeout} 1 "{tmp_path}/log" bash -c "touch {marker}; {cmd}"; '
              f'echo "rc=$? took=$((SECONDS-t0))"')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)
    rc, took = (int(x.split("=")[1]) for x in r.stdout.split())
    return rc, took, marker.exists()


def test_a_checker_step_with_no_time_left_is_not_started(tmp_path):
    rc, took, ran = _bounded(tmp_path, -5, 100, "sleep 30")
    assert rc == 75 and not ran and took < 3


def test_a_checker_step_is_cut_at_the_attempt_deadline_with_75_not_124(tmp_path):
    rc, took, ran = _bounded(tmp_path, 4, 100, "sleep 30")      # 4 s left - 1 s grace -> bounded to ~3 s
    assert rc == 75 and ran and took < 15


def test_without_a_deadline_the_checker_timeout_is_unchanged(tmp_path):
    script = f'source "{BOUNDED}"; r2g_bounded_run 1 1 "{tmp_path}/log" sleep 30; echo $?'
    assert subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).stdout.strip() == "124"


def test_a_recheck_stopped_by_the_budget_rolls_back_and_stops(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text("export DESIGN_NAME = demo\nexport CORE_UTILIZATION = 11\n")
    (proj / "reports" / "drc.json").write_text(json.dumps({"status": "fail", "total_violations": 5}))
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then grep -qx cat_x {state}/tried 2>/dev/null && {{ echo -e "STOP\\tresidual\\tnone"; exit 0; }}\n'
          f'  echo cat_x >> {state}/tried; echo -e "cat_x\\tfloorplan\\tdrc"\n'
          f'elif [[ "$*" == *"--apply"* ]]; then echo "export EDIT_cat_x = 1" >> {proj}/constraints/config.mk\n'
          f'  echo "{{\\"applied\\":\\"cat_x\\",\\"config_edits\\":{{}}}}"; fi')
    _stub(bindir / "run_orfs.sh", f'{sys.executable} {LEDGER} admit "$1" --resource flows --amount 1 || exit $?; exit 0')
    _stub(bindir / "run_drc.sh", "exit 75")                       # the recheck finds no time left
    _stub(bindir / "extract_drc.py", "exit 0")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("R2G_BUDGET", "R2G_LEDGER"))}
    env.update(R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_RUN_DRC=str(bindir / "run_drc.sh"), R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
               R2G_MEMORY_BACKEND="none", R2G_BUDGET_FILE=str(_budget(tmp_path, {"flows": 5, "wall_s": 10 ** 6})))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hd", "--check", "drc", "--max-iters", "3"],
                       env=env, capture_output=True, text=True)
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines()]
    assert rows[-1]["strategy"] == "cat_x" and rows[-1]["verdict"] == "budget_exhausted", r.stdout + r.stderr
    assert "EDIT_cat_x" not in (proj / "constraints" / "config.mk").read_text()


def test_uncapped_budget_meters_without_a_balance_cap_but_pauses_on_unknown_usage(tmp_path):
    # T2a 2026-10-16: Table 2 and the learning curve have independent ledgers with NO cumulative token,
    # money or request cap, but missing usage is still UNKNOWN (never 0) and pauses the queue.
    b = tmp_path / 'uncapped.json'
    b.write_text(json.dumps({'budget_id': 'table2', 'caps': {}, 'reserve': {}, 'halt_on_unknown_usage': True}))
    for _ in range(3):
        hold = _ledger(tmp_path, 'admit', '--hold', '--need', 'llm_requests=1', '--need', 'tokens_in=900000',
                       '--need', 'tokens_out=800', budget=b)
        assert hold.returncode == 0                       # no balance cap: large known usage is admitted
        assert _ledger(tmp_path, 'settle', '--hold-id', hold.stdout.strip(), '--actual', 'llm_requests=1',
                       '--actual', 'tokens_in=900000', '--actual', 'tokens_out=800', budget=b).returncode == 0
    s = json.loads(_ledger(tmp_path, 'summary', '--json', budget=b).stdout)
    assert s['caps'] == {} and s['combined_tokens_cap'] is None and s['spent']['tokens_in'] == 2700000
    assert _ledger(tmp_path, 'charge', '--resource', 'tokens_out', '--amount', 'UNKNOWN', budget=b).returncode == 0
    refused = _ledger(tmp_path, 'admit', '--hold', '--need', 'llm_requests=1', budget=b)
    assert refused.returncode == 75 and 'UNKNOWN' in (refused.stdout + refused.stderr)


def test_without_the_pause_flag_an_uncapped_budget_only_records(tmp_path):
    b = tmp_path / 'plain.json'
    b.write_text(json.dumps({'caps': {}}))
    assert _ledger(tmp_path, 'charge', '--resource', 'tokens_in', '--amount', 'UNKNOWN', budget=b).returncode == 0
    assert _ledger(tmp_path, 'admit', '--hold', '--need', 'llm_requests=1', budget=b).returncode == 0


def test_the_pause_flag_must_be_a_boolean(tmp_path):
    b = tmp_path / 'bad.json'
    b.write_text(json.dumps({'caps': {}, 'halt_on_unknown_usage': 'yes'}))
    assert _ledger(tmp_path, 'admit', '--resource', 'flows', '--amount', '1', budget=b).returncode == 2
