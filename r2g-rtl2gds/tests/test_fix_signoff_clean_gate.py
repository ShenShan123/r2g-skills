"""fix_signoff.sh final exit gate must be FAIL-CLOSED.

Regression for the 2026-06-20 honesty bug: the exit gate (the python tail of
fix_signoff.sh) decided "clean" with a fail-OPEN allowlist
    fail_states = {"fail","failed","residual","timeout"}
so any signoff status NOT in that set — notably DRC ``stuck`` (FEOL-hang timeout)
and LVS ``incomplete`` (extracted devices but died before any match verdict, no
lvsdb) — slipped through as exit 0. engineer_loop._process_one then called
_mark_clean() on a design whose signoff never actually verified, recording it
``clean`` in the campaign ledger. The knowledge run-row stayed honest (it stores
the real drc_status/lvs_status), but the loop-control layer lied.

The gate must be fail-CLOSED, matching engineer_loop's own first-pass predicate
(``status in {clean, clean_beol, skipped}``): a check counts as signed off ONLY
for those three statuses; every other status (stuck/incomplete/crash/unknown/
fail/…) leaves a residual -> exit 2.
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


def _run_with_reports(tmp_path, drc_status, lvs_status):
    """Seed reports/{drc,lvs}.json with the given statuses, stub diagnose to STOP
    (no actionable fix -> fix_one returns 0, leaving the reports untouched), and
    run the full script through to its exit gate. Returns the process returncode."""
    # unique project dir per call so a test can probe several status pairs.
    proj = tmp_path / f"proj_{drc_status}_{lvs_status}"
    (proj / "reports").mkdir(parents=True, exist_ok=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": drc_status, "total_violations": None, "categories": {}}))
    (proj / "reports" / "lvs.json").write_text(json.dumps(
        {"status": lvs_status, "mismatch_count": None, "lvsdb": {}}))

    bindir = proj / "bin"
    bindir.mkdir()
    # diagnose --next always STOPs (no fix exists for stuck/incomplete); never
    # reaches --apply, so the seeded reports are preserved verbatim.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "STOP\\tno_fix\\tnone"; fi')
    # safety no-ops: if _ensure_baseline ever fired it must not clobber the seed.
    _stub(bindir / "noop.sh", 'exit 0')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_RUN_LVS=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_LVS=str(bindir / "noop.sh"),
               R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                        "--check", "both", "--max-iters", "1"],
                       env=env, check=False)
    return r.returncode


def test_stuck_drc_and_incomplete_lvs_are_not_clean(tmp_path):
    # The exact cf_fir_24_16_16 signature: DRC timed out stuck, LVS no-verdict.
    assert _run_with_reports(tmp_path, "stuck", "incomplete") == 2


def test_crash_and_unknown_are_not_clean(tmp_path):
    assert _run_with_reports(tmp_path, "clean", "crash") == 2
    assert _run_with_reports(tmp_path, "unknown", "clean") == 2


def test_real_failures_still_caught(tmp_path):
    # regression guard: the statuses the old allowlist DID catch must stay caught.
    assert _run_with_reports(tmp_path, "fail", "clean") == 2
    assert _run_with_reports(tmp_path, "clean", "fail") == 2


def test_genuinely_clean_states_pass(tmp_path):
    # clean / clean_beol (BEOL-verified FEOL-hang) / skipped (no rule) are signed off.
    assert _run_with_reports(tmp_path, "clean", "clean") == 0
    assert _run_with_reports(tmp_path, "clean_beol", "clean") == 0
    assert _run_with_reports(tmp_path, "clean_beol", "skipped") == 0
    assert _run_with_reports(tmp_path, "clean", "skipped") == 0
