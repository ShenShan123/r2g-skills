"""fix_signoff.sh --check timing: the timing-closure fix loop.

A post-backend timing miss flows through --check timing: diagnose period_relax
(rewrite SDC CLOCK_PERIOD) -> apply -> rerun_from synth (run_orfs reflow) ->
check_timing.py re-measures the reflowed reports/ppa.json into timing_check.json
-> log. Timing has NO separate signoff tool: the run_orfs reflow IS the check
(analogous to --check route's extract_route reading the route stage).

The CRUX of this loop (mirroring the route loop, test_fix_signoff_route.py):
  * --check timing is accepted (no usage error);
  * fix_one flows the timing fix through _log_iter, so a check='timing',
    strategy='period_relax' row lands in fix_log.jsonl (ingest -> fix_events);
  * the A/B arm-B knob (R2G_FIX_RANK_FIRST) is passed straight through to
    `diagnose --next --rank-first period_relax` unchanged (same fix_one path);
  * the final clean-gate judges ONLY timing_check.json's `tier`: exit 0 iff tier
    in {clean, minor} (met), exit 2 otherwise (residual) — fail-closed.
"""
from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _seed_project(tmp_path, name="proj"):
    proj = tmp_path / name
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
        "export CORE_UTILIZATION = 30\n")
    (proj / "constraints" / "constraint.sdc").write_text(
        "set clk_period 4.0\ncreate_clock -name clk -period $clk_period [get_ports clk]\n")
    # ppa.json with a severe timing miss (wns_ns negative). check_timing.py reads
    # summary.timing.setup_wns; our STUB reads it too (see _stub below).
    (proj / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {"timing": {"setup_wns": -1.2, "setup_tns": -50.0,
                                "setup_violation_count": 7}}}))
    return proj


def _common_stubs(tmp_path, *, argfile):
    """Stub diagnose (records argv to argfile; yields period_relax then STOP),
    run_orfs (no-op), and check_timing (severe pre-apply -> clean post-apply, gated
    on the /tmp marker the --apply branch touches)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # diagnose: log every invocation's args; --next yields period_relax once
    # (until the apply marker exists), then STOP; --apply rewrites the SDC period
    # and touches the marker + echoes config/sdc edits.
    _stub(bindir / "diagnose.py",
          f'echo "$*" >> "{argfile}"\n'
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f /tmp/_tm_$$ ]]; then echo -e "STOP\\tclean\\tdone";\n'
          '  else echo -e "period_relax\\tsynth\\ttiming"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch /tmp/_tm_$$;\n'
          '  echo "{\\"applied\\":\\"period_relax\\",\\"config_edits\\":{},'
          '\\"sdc_edits\\":{\\"CLOCK_PERIOD\\":\\"5.4\\"}}"; fi')
    # run_orfs: no-op (a real rerun would re-synthesize at the relaxed period).
    _stub(bindir / "noop.sh", 'exit 0')
    # check_timing: writes timing_check.json. severe until the apply marker exists,
    # then clean (the relaxed period absorbed the WNS). Mirrors the real script's
    # output keys (tier + wns + wns_ns alias). Exits 0 either way (the fix loop
    # swallows the rc anyway via _run_extract's `|| true`).
    _stub(bindir / "check_timing.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys,os,glob\n'
          'proj=sys.argv[1]\n'
          'marker=glob.glob("/tmp/_tm_*")\n'
          'if marker:\n'
          '    d={"tier":"clean","wns":0.05,"wns_ns":0.05,"clock_period":5.4,"clock_period_ns":5.4}\n'
          'else:\n'
          '    d={"tier":"severe","wns":-1.2,"wns_ns":-1.2,"clock_period":4.0,"clock_period_ns":4.0}\n'
          'open(os.path.join(proj,"reports","timing_check.json"),"w").write(json.dumps(d))\n'
          'sys.exit(0 if d["tier"] in ("clean","minor") else 1)\nPY')
    return bindir


def _env(bindir, tmp_path, **extra):
    e = dict(os.environ,
             R2G_DIAGNOSE=str(bindir / "diagnose.py"),
             R2G_RUN_ORFS=str(bindir / "noop.sh"),
             R2G_CHECK_TIMING=str(bindir / "check_timing.py"),
             R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"))
    e.update(extra)
    return e


def test_timing_check_accepted_and_armB_drives_severe_to_clean(tmp_path):
    """Arm B (R2G_FIX_RANK_FIRST=period_relax) drives the tier severe->clean after
    one apply + reflow: the script exits 0, a period_relax timing row is logged, and
    diagnose --next received --rank-first period_relax."""
    proj = _seed_project(tmp_path)
    argfile = tmp_path / "diag_args.txt"
    bindir = _common_stubs(tmp_path, argfile=argfile)

    r = subprocess.run(
        ["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
         "--check", "timing", "--max-iters", "2"],
        env=_env(bindir, tmp_path, R2G_FIX_RANK_FIRST="period_relax"), check=False,
        capture_output=True, text=True)

    # (a) --check timing accepted: no usage/ERROR about the --check value.
    assert "ERROR: --check must be" not in (r.stderr + r.stdout)
    # (d) final tier clean -> exit 0.
    assert r.returncode == 0, (r.stdout, r.stderr)

    # (b) a fix_log row reflects the timing fix.
    lines = [json.loads(l) for l in
             (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    applied = [x for x in lines if x.get("strategy") == "period_relax"]
    assert applied, f"expected a period_relax timing row, got {lines}"
    row = applied[0]
    assert row["check"] == "timing"            # NOT remapped (unlike route->orfs_stage)
    assert row["violation_class"] == "timing"
    assert row["from_stage"] == "synth"
    assert row["after"] == "0"                 # badness 0 == timing met
    assert row["verdict"] == "cleared"

    # (c) the A/B arm-B knob reached diagnose --next unchanged.
    args = argfile.read_text()
    assert "--rank-first period_relax" in args, args


def test_timing_gate_exit2_when_tier_stays_severe(tmp_path):
    """When the reflow does NOT close timing (tier stays severe), the clean-gate is
    fail-closed: exit 2 (residual)."""
    proj = _seed_project(tmp_path, name="proj_stuck")
    argfile = tmp_path / "diag_args2.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    # diagnose always STOPs (no actionable fix) so timing_check.json stays severe.
    _stub(bindir / "diagnose.py",
          f'echo "$*" >> "{argfile}"\n'
          'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tsevere\\tno_fix"; fi')
    _stub(bindir / "noop.sh", 'exit 0')
    # check_timing always reports severe.
    _stub(bindir / "check_timing.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys,os\n'
          'open(os.path.join(sys.argv[1],"reports","timing_check.json"),"w").write('
          'json.dumps({"tier":"severe","wns":-1.2,"wns_ns":-1.2}))\n'
          'sys.exit(1)\nPY')

    r = subprocess.run(
        ["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
         "--check", "timing", "--max-iters", "1"],
        env=_env(bindir, tmp_path), check=False, capture_output=True, text=True)
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_timing_gate_exit0_for_minor_tier(tmp_path):
    """tier=minor (WNS<0 but auto-closeable) counts as MET (exit 0)."""
    proj = _seed_project(tmp_path, name="proj_minor")
    argfile = tmp_path / "diag_args3.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir / "diagnose.py",
          f'echo "$*" >> "{argfile}"\n'
          'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tminor\\tauto"; fi')
    _stub(bindir / "noop.sh", 'exit 0')
    _stub(bindir / "check_timing.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys,os\n'
          'open(os.path.join(sys.argv[1],"reports","timing_check.json"),"w").write('
          'json.dumps({"tier":"minor","wns":-0.02,"wns_ns":-0.02}))\n'
          'sys.exit(0)\nPY')
    r = subprocess.run(
        ["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
         "--check", "timing", "--max-iters", "1"],
        env=_env(bindir, tmp_path), check=False, capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_count_maps_timing_check_to_badness(tmp_path):
    """_count: a MET timing_check.json (wns>=0) -> 0; a violated one -> positive
    badness round(-wns*1000). Exercises the _count python branch directly so the
    invariant is pinned even without the full reflow."""
    src = FIX_SIGNOFF.read_text()
    # Extract the _count body's python -c program (between the first `python3 -c '`
    # in _count and the closing `' "$1"`), then run it on crafted reports.
    snippet = src.split("_count() {", 1)[1].split("python3 -c '", 1)[1].split("' \"$1\"", 1)[0]

    met = tmp_path / "met.json"
    met.write_text(json.dumps({"tier": "clean", "wns": 0.05, "wns_ns": 0.05}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tier": "severe", "wns": -1.2, "wns_ns": -1.2}))

    def run(p):
        return subprocess.run(["python3", "-c", snippet, str(p)],
                              capture_output=True, text=True).stdout.strip()

    assert run(met) == "0"
    assert run(bad) == "1200"     # round(1.2*1000)
