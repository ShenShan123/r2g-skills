"""A rejected live repair must roll back TRANSACTIONALLY (RMD-HO-P1-04).

Held-out V3 P1-HO-04, sky130hs SPI Flash: the global comparator correctly rejected
a density change that introduced a route violation and an LVS mismatch, and
`constraints/config.mk` was restored — but `backend/.r2g_signoff_run` and every
project-level report still described the REJECTED rerun. The ingested `runs` row
therefore paired the restored config with the regressed physical outcome, and the
next diagnose/repair iteration started from a run the comparator had refused.

The invariant under test: after a rejected repair, the config, the reports and the
active-run pointer all name ONE accepted run. The rejected artifacts stay on disk
under backend/ (negative learning + audit), they are simply no longer active.

The fixture is deliberately faithful about FRESHNESS: result_vector.capture binds
every signal to the newest backend RUN via `run_tag`, and only a MEASURED fresh
good->bad flip fires the comparator. A fixture without real RUN dirs would make
every signal unmeasured and the test would pass vacuously.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"

ACCEPTED = "RUN_ACCEPTED"
REJECTED = "RUN_REJECTED"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _mkrun(proj: Path, tag: str, def_body: str, mtime: int) -> Path:
    run = proj / "backend" / tag
    (run / "results").mkdir(parents=True, exist_ok=True)
    d = run / "results" / "6_final.def"
    d.write_text(def_body)
    os.utime(d, (mtime, mtime))
    os.utime(run, (mtime, mtime))
    return run


def _project(tmp_path: Path) -> Path:
    """Baseline is ACCEPTED: DRC 2 residual, route clean, LVS clean."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = spirom\nexport PLATFORM = sky130hs\n"
        "export CORE_UTILIZATION = 30\n")
    _mkrun(proj, ACCEPTED, "ACCEPTED LAYOUT\n", 1_700_000_000)
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 2, "run_tag": ACCEPTED,
         "categories": {"li.3": {"count": 2}}}))
    (proj / "reports" / "route.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0, "run_tag": ACCEPTED}))
    (proj / "reports" / "lvs.json").write_text(json.dumps(
        {"status": "clean", "mismatch_count": 0, "run_tag": ACCEPTED}))
    (proj / "backend" / ".r2g_signoff_run").write_text(json.dumps(
        {"run_tag": ACCEPTED, "gds_sha256": "a" * 64}))
    return proj


def _regressing_harness(tmp_path: Path, proj: Path) -> dict:
    """Stubs where one density_relief attempt improves DRC 2->1 but regresses
    route 0->1 and LVS clean->pin_pdn_short: the exact SPI Flash shape."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "_applied"

    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  if [[ -f "{marker}" ]]; then echo -e "STOP\\tcatalog_exhausted\\tdone";\n'
          f'  else echo -e "density_relief\\tfloorplan\\tdrc"; fi\n'
          f'elif [[ "$*" == *"--apply"* ]]; then touch "{marker}";\n'
          f'  echo "{{\\"applied\\":\\"density_relief\\",'
          f'\\"config_edits\\":{{\\"CORE_UTILIZATION\\":\\"22\\"}}}}"; fi')

    # The "reflow": a NEW backend run becomes newest, the active pointer follows
    # it, and route/LVS regress — exactly what run_orfs + restage really do.
    _stub(bindir / "run_orfs.sh", f'''python3 - <<'PY'
import json, os
proj = "{proj}"
run = os.path.join(proj, "backend", "{REJECTED}", "results")
os.makedirs(run, exist_ok=True)
open(os.path.join(run, "6_final.def"), "w").write("REJECTED LAYOUT\\n")
os.utime(os.path.join(proj, "backend", "{REJECTED}"), (1_800_000_000, 1_800_000_000))
open(os.path.join(proj, "backend", ".r2g_signoff_run"), "w").write(
    json.dumps({{"run_tag": "{REJECTED}", "gds_sha256": "b" * 64}}))
open(os.path.join(proj, "reports", "route.json"), "w").write(
    json.dumps({{"status": "fail", "total_violations": 1, "run_tag": "{REJECTED}"}}))
open(os.path.join(proj, "reports", "lvs.json"), "w").write(
    json.dumps({{"status": "fail", "mismatch_count": 1,
                "mismatch_class": "pin_pdn_short", "run_tag": "{REJECTED}"}}))
PY''')

    _stub(bindir / "run_drc.sh", 'exit 0')
    _stub(bindir / "extract_drc.py", f'''python3 - <<'PY'
import json, os
open(os.path.join("{proj}", "reports", "drc.json"), "w").write(
    json.dumps({{"status": "fail", "total_violations": 1, "run_tag": "{REJECTED}",
                "categories": {{"li.3": {{"count": 1}}}}}}))
PY''')
    # route.json / lvs.json are owned by the rerun stub; no-op the re-extracts.
    _stub(bindir / "extract_route.py", 'exit 0')
    _stub(bindir / "extract_ppa.py", 'exit 0')
    _stub(bindir / "check_timing.py", 'exit 0')

    return dict(os.environ,
                R2G_DIAGNOSE=str(bindir / "diagnose.py"),
                R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
                R2G_RUN_DRC=str(bindir / "run_drc.sh"),
                R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
                R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"),
                R2G_EXTRACT_PPA=str(bindir / "extract_ppa.py"),
                R2G_CHECK_TIMING=str(bindir / "check_timing.py"))


def _run(proj: Path, env: dict) -> subprocess.CompletedProcess:
    res = subprocess.run(
        ["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "drc",
         "--max-iters", "1"], env=env, check=False, capture_output=True, text=True)
    # rc 2 == "a residual remains" — the honest verdict for this deliberately dirty
    # fixture (the repair was rolled back, so DRC is 2 again). rc 1 is an abort.
    assert res.returncode in (0, 2), f"rc={res.returncode}\n{res.stdout[-3000:]}"
    return res


def test_rejected_repair_restores_config_reports_and_run_pointer(tmp_path):
    proj = _project(tmp_path)
    env = _regressing_harness(tmp_path, proj)
    res = _run(proj, env)

    # 0. the comparator actually MEASURED the regression (never a vacuous pass)
    rows = [json.loads(line) for line in
            (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if line.strip()]
    applied = [r for r in rows if r["strategy"] == "density_relief"]
    assert applied, f"no density_relief row; log={rows}\n{res.stdout[-3000:]}"
    assert applied[0]["verdict"] == "regression", res.stdout[-3000:]
    assert applied[0]["global_regressions"], "the measured regression must ride the row"

    # 1. config restored (the pre-existing half of the fix)
    cfg = (proj / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION = 30" in cfg
    assert "22" not in cfg

    # 2. THE FIX: the active-run pointer is back on the accepted run
    rec = json.loads((proj / "backend" / ".r2g_signoff_run").read_text())
    assert rec["run_tag"] == ACCEPTED, "active pointer left on the rejected run"

    # 3. THE FIX: the project-level reports describe the accepted run again
    for name, key, want in (("route.json", "total_violations", 0),
                            ("lvs.json", "status", "clean"),
                            ("drc.json", "total_violations", 2)):
        rep = json.loads((proj / "reports" / name).read_text())
        assert rep[key] == want, f"{name} still describes the rejected run: {rep}"
        assert rep["run_tag"] == ACCEPTED, f"{name} run_tag not restored: {rep}"

    # 4. the rejected attempt stays on disk and auditable
    assert (proj / "backend" / REJECTED / "results" / "6_final.def").exists()
    audit = json.loads((proj / "reports" / "global_regression_it1.json").read_text())
    assert audit["config_restored"] is True
    assert audit["evidence_bundle_restored"] is True
    assert audit["regressions"]


def test_interrupted_rollback_reconciles_to_the_accepted_state(tmp_path):
    """A crash between the marker and its removal leaves a MIXED bundle; the next
    entry must complete the restore, never expose the mix."""
    proj = _project(tmp_path)
    env = _regressing_harness(tmp_path, proj)
    _run(proj, env)                       # leaves reports/.rv_accepted behind

    # Simulate the interruption: a half-applied restore + the pending marker.
    (proj / "reports" / "route.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 1, "run_tag": REJECTED}))
    (proj / "backend" / ".r2g_signoff_run").write_text(json.dumps(
        {"run_tag": REJECTED, "gds_sha256": "b" * 64}))
    (proj / "reports" / ".rv_rollback_pending").write_text("")

    _run(proj, env)
    assert json.loads((proj / "reports" / "route.json").read_text())["run_tag"] == ACCEPTED
    assert json.loads(
        (proj / "backend" / ".r2g_signoff_run").read_text())["run_tag"] == ACCEPTED
    assert not (proj / "reports" / ".rv_rollback_pending").exists()
