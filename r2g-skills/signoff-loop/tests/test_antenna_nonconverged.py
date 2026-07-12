"""Antenna repair non-convergence auto-exit (failure-patterns.md #36) + the
stage-scoped resume default (#35).

The ORFS inner loop re-inserts diodes and re-runs detailed route up to
MAX_REPAIR_ANTENNAS_ITER_DRT times per reflow with no improvement check, and
OpenROAD's antenna model can disagree with the signoff deck — so the same 1-2
residual violations survive every round (the SHA-1/SHA-256 loop). fix_signoff
now: (a) declares antenna_nonconverged after 2 non-improving antenna
strategies and STOPS; (b) persists reports/antenna_nonconverged.json so later
sessions exclude the proven-futile strategies instead of silently burning the
same reflows; (c) clears the marker the moment DRC reaches CLEAN;
(d) R2G_FIX_RETRY_NONCONVERGED=1 deliberately retries. Separately, the reflow
after a config edit now resumes FROM the strategy's rerun_from stage by
default (run_orfs invalidates that stage via make clean_<stage> so the edit
applies), with R2G_FIX_FULL_REFLOW=1 restoring the full rebuild.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"
RUN_ORFS = SKILL / "scripts" / "flow" / "run_orfs.sh"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _proj(tmp_path, count=1):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": count,
         "categories": {"M2_ANTENNA": {"count": count}}}))
    return proj


def _bin(tmp_path, *, stuck_count=1, diagnose_log=None, orfs_log=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log_line = f'echo "$*" >> "{diagnose_log}"\n' if diagnose_log else ""
    _stub(bindir / "diagnose.py",
          log_line
          + 'if [[ "$*" == *"--next"* ]]; then echo -e "antenna_diode_iters\\troute\\tdrc";\n'
          + 'elif [[ "$*" == *"--apply"* ]]; then echo "{}"; fi')
    orfs_body = 'exit 0'
    if orfs_log:
        orfs_body = f'echo "FROM_STAGE=${{FROM_STAGE:-}}" >> "{orfs_log}"\nexit 0'
    _stub(bindir / "orfs.sh", orfs_body)
    _stub(bindir / "noop.sh", 'exit 0')
    _stub(bindir / "extract.py",
          'python3 - "$@" <<PY\nimport json,sys\n'
          'n=%d\n'
          'open(sys.argv[2],"w").write(json.dumps(\n'
          '  {"status":"clean" if n==0 else "fail","total_violations":n,\n'
          '   "categories":{} if n==0 else {"M2_ANTENNA":{"count":n}}}))\nPY' % stuck_count)
    return bindir


def _bin_seq(tmp_path, counts, *, orfs_log=None):
    """Like _bin, but extract.py returns a SEQUENCE of violation counts (one per
    call, clamped to the last) so a converging-with-plateau antenna run can be
    driven — the fixture for the consecutive-vs-cumulative reset (#38)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "antenna_diode_iters\\troute\\tdrc";\n'
          'elif [[ "$*" == *"--apply"* ]]; then echo "{}"; fi')
    orfs_body = 'exit 0'
    if orfs_log:
        orfs_body = f'echo "FROM_STAGE=${{FROM_STAGE:-}} REASON=${{R2G_RERUN_REASON:-}}" >> "{orfs_log}"\nexit 0'
    _stub(bindir / "orfs.sh", orfs_body)
    _stub(bindir / "noop.sh", 'exit 0')
    ctr = tmp_path / "extract_ctr"
    body = (
        'CTR="%s"\n'
        'COUNTS=(%s)\n'
        'i=0; [[ -f "$CTR" ]] && i="$(cat "$CTR")"\n'
        'last=$(( ${#COUNTS[@]} - 1 ))\n'
        '(( i > last )) && i=$last\n'
        'n=${COUNTS[$i]}\n'
        'echo $((i+1)) > "$CTR"\n'
        'python3 - "$@" "$n" <<PY\n'
        'import json,sys\n'
        'n=int(sys.argv[-1])\n'
        'open(sys.argv[2],"w").write(json.dumps({"status":"clean" if n==0 else "fail",'
        '"total_violations":n,"categories":{} if n==0 else {"M2_ANTENNA":{"count":n}}}))\n'
        'PY'
    ) % (ctr, " ".join(str(c) for c in counts))
    _stub(bindir / "extract.py", body)
    return bindir


def _env(bindir, **extra):
    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "orfs.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _rows(proj):
    lines = [json.loads(l) for l in
             (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    return [r for r in lines if r["strategy"] not in ("none",) and r.get("iter")]


def _run(proj, env):
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc"], env=env, check=False,
                   capture_output=True, text=True)


def test_stuck_antenna_declares_nonconverged_after_two(tmp_path):
    proj = _proj(tmp_path)
    bindir = _bin(tmp_path, stuck_count=1)
    _run(proj, _env(bindir))
    rows = _rows(proj)
    assert len(rows) == 2, f"expected 2 antenna iterations then stop, got {len(rows)}"
    assert rows[0]["verdict"] == "no_improvement"
    assert rows[1]["verdict"] == "antenna_nonconverged"
    marker = json.loads((proj / "reports" / "antenna_nonconverged.json").read_text())
    assert marker["class"] == "antenna"
    assert marker["residual_count"] == 1
    assert "antenna_diode_iters" in marker["strategies_tried"]


def test_marker_excludes_strategies_on_next_session(tmp_path):
    proj = _proj(tmp_path)
    dlog = tmp_path / "diag_args.log"
    bindir = _bin(tmp_path, stuck_count=1, diagnose_log=dlog)
    _run(proj, _env(bindir))                     # session 1: writes the marker
    dlog.write_text("")                          # observe only session 2
    _run(proj, _env(bindir))                     # session 2: marker must exclude
    first_next = next(l for l in dlog.read_text().splitlines() if "--next" in l)
    assert "--exclude" in first_next and "antenna_diode_iters" in first_next, first_next


def test_retry_env_bypasses_marker(tmp_path):
    proj = _proj(tmp_path)
    dlog = tmp_path / "diag_args.log"
    bindir = _bin(tmp_path, stuck_count=1, diagnose_log=dlog)
    _run(proj, _env(bindir))
    dlog.write_text("")
    _run(proj, _env(bindir, R2G_FIX_RETRY_NONCONVERGED="1"))
    first_next = next(l for l in dlog.read_text().splitlines() if "--next" in l)
    # deliberate retry: the marker's strategies must NOT be pre-excluded
    assert "antenna_diode_iters" not in first_next.split("--exclude", 1)[-1].split("--")[0]


def test_clean_run_clears_marker(tmp_path):
    proj = _proj(tmp_path)
    (proj / "reports" / "antenna_nonconverged.json").write_text(json.dumps(
        {"class": "antenna", "strategies_tried": ["stale_strategy"]}))
    bindir = _bin(tmp_path, stuck_count=0)   # re-check comes back clean
    _run(proj, _env(bindir, R2G_FIX_RETRY_NONCONVERGED="1"))
    assert not (proj / "reports" / "antenna_nonconverged.json").exists()


def test_reflow_resumes_from_rerun_stage_by_default(tmp_path):
    proj = _proj(tmp_path)
    olog = tmp_path / "orfs_calls.log"
    bindir = _bin(tmp_path, stuck_count=1, orfs_log=olog)
    _run(proj, _env(bindir))
    calls = olog.read_text().splitlines()
    assert calls and all(c == "FROM_STAGE=route" for c in calls), calls


def test_full_reflow_env_restores_clean_all_path(tmp_path):
    proj = _proj(tmp_path)
    olog = tmp_path / "orfs_calls.log"
    bindir = _bin(tmp_path, stuck_count=1, orfs_log=olog)
    _run(proj, _env(bindir, R2G_FIX_FULL_REFLOW="1"))
    calls = olog.read_text().splitlines()
    assert calls and all(c == "FROM_STAGE=" for c in calls), calls


def test_converging_antenna_not_declared_nonconverged(tmp_path):
    """The consecutive-vs-cumulative fix (failure-patterns.md #38): a design that
    CONVERGES via interleaved wins and no-ops (10 -> 8 -> 8 -> 5 -> 5 -> 3 -> 3
    -> 0) must NOT be falsely aborted. The OLD code only ever incremented
    antenna_noimp, so the 2nd cumulative no-op (iter 4) declared it
    non-converged despite clear progress; the fix RESETS the counter on every
    improving antenna iteration, so the run reaches CLEAN."""
    proj = _proj(tmp_path, count=10)
    bindir = _bin_seq(tmp_path, [8, 8, 5, 5, 3, 3, 0])
    _run(proj, _env(bindir))
    rows = _rows(proj)
    verdicts = [r["verdict"] for r in rows]
    assert "antenna_nonconverged" not in verdicts, verdicts
    assert "cleared" in verdicts, verdicts
    # the fix loop reached CLEAN, so no non-convergence marker was persisted
    assert not (proj / "reports" / "antenna_nonconverged.json").exists()


def test_rerun_reason_threaded_into_run_orfs(tmp_path):
    """codex #3 / failure-patterns.md #38: the concrete rerun reason (strategy +
    rerun_from) is passed to run_orfs via R2G_RERUN_REASON so the run's own
    stage_log/flow.log records WHY a stage was re-triggered."""
    proj = _proj(tmp_path, count=1)
    olog = tmp_path / "orfs_calls.log"
    bindir = _bin_seq(tmp_path, [1, 1], orfs_log=olog)   # stuck -> 2 reflows
    _run(proj, _env(bindir))
    calls = [c for c in olog.read_text().splitlines() if c.strip()]
    assert calls, "run_orfs was never invoked"
    for c in calls:
        assert "FROM_STAGE=route" in c, c
        assert "REASON=" in c and "strategy=antenna_diode_iters" in c, c


def test_run_orfs_records_stage_provenance():
    """codex #3: run_orfs.sh persists per-stage timestamps + output artifact in
    stage_log.jsonl and a resume rationale in flow.log + resume_meta.json — the
    reuse/rerun decision must be auditable from backend/RUN_*/, not stdout-only."""
    src = RUN_ORFS.read_text(encoding="utf-8")
    # stage_log rows carry timestamps + the produced ODB (additive to status)
    assert "ts_start" in src and "ts_end" in src and "artifact" in src
    # the resume decision is tee'd to flow.log with its concrete reason
    assert "R2G_RERUN_REASON" in src
    assert 'tee -a "$BACKEND_DIR/flow.log"' in src
    # a structured resume rationale file records which stages were reused
    assert "resume_meta.json" in src and "reused_stages" in src


def test_run_orfs_invalidates_resumed_stage():
    """run_orfs.sh must clean exactly the resumed stage (make clean_<stage>)
    unless R2G_RESUME_NO_CLEAN=1 — config.mk is not a make prerequisite, so a
    resume without invalidation silently NO-OPs a config edit (#35)."""
    src = RUN_ORFS.read_text(encoding="utf-8")
    assert 'clean_$FROM_STAGE' in src
    assert "R2G_RESUME_NO_CLEAN" in src
    # the invalidation must sit AFTER the FROM_STAGE validity guard (a garbage
    # stage name must never reach make)
    assert src.index("does not match any stage") < src.index('clean_$FROM_STAGE')
