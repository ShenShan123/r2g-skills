"""Unconstrained timing is a configuration failure, never a pass and never a repair target
(failure-patterns "Unconstrained timing passed strict acceptance", 2026-10-18).

The i2c_master_top task's shipped SDC creates its clock on port `clk`, which the design
does not have: STA-0366 "port 'clk' not found", CTS "No clock nets", 215 unconstrained
endpoints, setup/hold WNS 1e+39. Acceptance passed A6/A7, the engineer loop spent a timing
repair on it (utilization_reduce), and the fix log recorded that 0 -> 0 step as `cleared`.
"""
import json
import subprocess
import sys
from pathlib import Path

import engineer_loop as el

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "reports"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_check as ac  # noqa: E402
import test_acceptance_check as TA  # noqa: E402
import test_fix_signoff_timing as FT  # noqa: E402

UNC = 1e39


# -- acceptance -------------------------------------------------------------------------

def test_unconstrained_setup_or_hold_fails_acceptance(tmp_path):
    t = {"setup_wns": UNC, "setup_tns": 0, "hold_wns": UNC, "hold_tns": 0,
         "max_cap_violations": 0, "max_slew_violations": 0}
    r = ac.evaluate(TA._project(tmp_path, **{"ppa.json": {"summary": {"timing": t}}}), 10.0)
    assert r["accepted"] is False
    for k in ("A6_setup", "A7_hold"):
        assert r["checks"][k]["result"] == "fail"
        assert r["checks"][k]["detail"].get("reason") == "timing_unconstrained"


def test_a_real_positive_slack_still_passes(tmp_path):
    assert ac.evaluate(TA._project(tmp_path), 10.0)["accepted"] is True


# -- engineer loop ------------------------------------------------------------------------

def _entry(tmp_path):
    project = tmp_path / "project"
    (project / "reports").mkdir(parents=True)
    return {"design": "unc", "project_path": str(project), "platform": "sky130hd", "kind": "normal"}


def test_the_loop_escalates_unconstrained_timing_instead_of_repairing_it(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    led = el.Ledger(tmp_path / "ledger.jsonl")
    led.add(entry)
    phys = {"drc": "clean", "lvs": "clean", "route": "clean", "rcx": "clean"}
    statuses = iter([{**{k: "unknown" for k in phys}, "timing": "unknown"},
                     {**phys, "timing": "unconstrained"}, {**phys, "timing": "unconstrained"}])
    checks = []
    monkeypatch.setattr(el, "_run_flow", lambda unused: 0)
    monkeypatch.setattr(el, "_signoff_status", lambda unused: next(statuses))
    monkeypatch.setattr(el, "_run_fix", lambda item: checks.append(item.get("check", "both")) or 0)
    monkeypatch.setattr(el, "_ingest", lambda unused: None)
    assert el.process_one(led, led.pending()[0], conn=None) == "escalated"
    assert checks == ["both"]                                    # no timing repair turn
    assert el._signoff_escalation_reason({**phys, "timing": "unconstrained"}) == "timing_unconstrained"


def test_the_escalation_reason_is_registered():
    import escalations
    assert "timing_unconstrained" in escalations.REASONS


# -- fix harness ----------------------------------------------------------------------------

def _timing_stubs(tmp_path, before: dict, after: dict):
    """Own stubs with the apply marker INSIDE tmp_path (never a shared /tmp path)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argfile, applied, marker = tmp_path / "diag_args.txt", tmp_path / "applied", tmp_path / "orfs_ran"
    FT._stub(bindir / "diagnose.py",
             f'echo "$*" >> "{argfile}"\n'
             'if [[ "$*" == *"--next"* ]]; then\n'
             f'  if [[ -f "{applied}" ]]; then echo -e "STOP\\tclean\\tdone";\n'
             '  else echo -e "period_relax\\tsynth\\ttiming"; fi\n'
             f'elif [[ "$*" == *"--apply"* ]]; then touch "{applied}";\n'
             '  echo "{\\"applied\\":\\"period_relax\\",\\"config_edits\\":{},'
             '\\"sdc_edits\\":{\\"CLOCK_PERIOD\\":\\"5.4\\"}}"; fi')
    FT._stub(bindir / "noop.sh", f'touch "{marker}"')
    FT._stub(bindir / "check_timing.py",
             'python3 - "$@" <<\'PY\'\nimport json,sys,os\n'
             'proj=sys.argv[1]\n'
             f'd={json.dumps(after)} if os.path.exists({str(applied)!r}) else {json.dumps(before)}\n'
             'open(os.path.join(proj,"reports","timing_check.json"),"w").write(json.dumps(d))\n'
             'sys.exit(0 if d["tier"] in ("clean","minor") else 1)\nPY')
    return bindir, argfile, marker


def _rows(proj):
    return [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]


def test_the_harness_never_repairs_unconstrained_timing(tmp_path):
    proj = FT._seed_project(tmp_path)
    unc = {"tier": "unconstrained", "wns": UNC, "wns_ns": UNC, "clock_period": 4.0}
    bindir, argfile, marker = _timing_stubs(tmp_path, unc, unc)
    r = subprocess.run(["bash", str(FT.FIX_SIGNOFF), str(proj), "nangate45", "--check", "timing",
                        "--max-iters", "2"], env=FT._env(bindir, tmp_path), capture_output=True, text=True)
    assert r.returncode == 2, (r.stdout[-2000:], r.stderr[-2000:])
    assert not marker.exists()                                     # no flow launched
    assert "--next" not in (argfile.read_text() if argfile.exists() else "")
    rows = _rows(proj)
    assert [x["verdict"] for x in rows] == ["stop_timing_unconstrained"]
    assert rows[0]["strategy"] == "none"


def test_a_zero_to_zero_step_is_never_logged_as_cleared(tmp_path):
    proj = FT._seed_project(tmp_path)
    before = {"tier": "moderate", "wns": 0.0, "wns_ns": 0.0, "clock_period": 4.0}
    after = {"tier": "clean", "wns": 0.0, "wns_ns": 0.0, "clock_period": 5.4}
    bindir, _, _ = _timing_stubs(tmp_path, before, after)
    subprocess.run(["bash", str(FT.FIX_SIGNOFF), str(proj), "nangate45", "--check", "timing", "--max-iters", "1"],
                   env=FT._env(bindir, tmp_path), capture_output=True, text=True)
    step = [x for x in _rows(proj) if x.get("strategy") == "period_relax"]
    assert step and step[0]["before"] == "0" and step[0]["after"] == "0"
    assert step[0]["verdict"] == "no_target"
