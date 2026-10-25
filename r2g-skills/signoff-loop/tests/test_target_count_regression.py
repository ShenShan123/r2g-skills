"""A fix that worsens its OWN target count is a regression, not a no-op.

Held-out R2G memory evaluation (round 2, sky130hs sha256_core li.3): density_relief took
DRC 4 -> 6. fix_signoff.sh labelled it `no_improvement` (ingest -> `no_change`) and did
not roll back, so the learner could never record the harm and the next design repeated
it. 56 committed fix_events carried `no_change` with after > before. Now: the target
increase is recorded as a regression signal, the config is restored, and every
normaliser maps after > before to `regression`.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"
sys.path.insert(0, str(SKILL / "knowledge"))

import backfill_fix_events  # noqa: E402
import ingest_run  # noqa: E402


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _project(tmp_path: Path, drc_before: int) -> Path:
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = sha\nexport PLATFORM = sky130hs\nexport CORE_UTILIZATION = 12\n")
    run = proj / "backend" / "RUN_A" / "results"
    run.mkdir(parents=True)
    (run / "6_final.def").write_text("A\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": drc_before, "run_tag": "RUN_A",
         "categories": {"li.3": {"count": drc_before}}}))
    return proj


def _harness(tmp_path: Path, proj: Path, drc_after: int, extra: str = "") -> dict:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "_applied"
    _stub(bindir / "diagnose.py",
          f'if [[ "$*" == *"--next"* ]]; then\n'
          f'  if [[ -f "{marker}" ]]; then echo -e "STOP\\tcatalog_exhausted\\tdone";\n'
          f'  else echo -e "density_relief\\tfloorplan\\tdrc"; fi\n'
          f'elif [[ "$*" == *"--apply"* ]]; then touch "{marker}";\n'
          f'  sed -i "s/CORE_UTILIZATION = 12/CORE_UTILIZATION = 8/" "{proj}/constraints/config.mk";\n'
          f'  echo "{{\\"applied\\":\\"density_relief\\",'
          f'\\"config_edits\\":{{\\"CORE_UTILIZATION\\":\\"8\\"}},'
          f'\\"config_before\\":{{\\"CORE_UTILIZATION\\":\\"12\\"}}{extra}}}"; fi')
    # The reflow writes a NEW, different layout (otherwise fix_signoff correctly takes the
    # byte-identical `recipe_no_effect` shortcut and never re-checks).
    _stub(bindir / "run_orfs.sh", f'''python3 - <<'PY'
import os
run = os.path.join("{proj}", "backend", "RUN_B", "results")
os.makedirs(run, exist_ok=True)
open(os.path.join(run, "6_final.def"), "w").write("B\\n")
os.utime(os.path.join("{proj}", "backend", "RUN_B"), (1_900_000_000, 1_900_000_000))
PY''')
    _stub(bindir / "run_drc.sh", 'exit 0')
    _stub(bindir / "extract_drc.py", f'''python3 - <<'PY'
import json, os
open(os.path.join("{proj}", "reports", "drc.json"), "w").write(
    json.dumps({{"status": "fail", "total_violations": {drc_after}, "run_tag": "RUN_B",
                "categories": {{"li.3": {{"count": {drc_after}}}}}}}))
PY''')
    for name in ("extract_route.py", "extract_ppa.py", "check_timing.py"):
        _stub(bindir / name, 'exit 0')
    return dict(os.environ,
                R2G_DIAGNOSE=str(bindir / "diagnose.py"), R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
                R2G_RUN_DRC=str(bindir / "run_drc.sh"), R2G_EXTRACT_DRC=str(bindir / "extract_drc.py"),
                R2G_EXTRACT_ROUTE=str(bindir / "extract_route.py"),
                R2G_EXTRACT_PPA=str(bindir / "extract_ppa.py"),
                R2G_CHECK_TIMING=str(bindir / "check_timing.py"))


def _run(proj: Path, env: dict) -> list[dict]:
    res = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "sky130hs", "--check", "drc",
                          "--max-iters", "1"], env=env, check=False, capture_output=True, text=True)
    assert res.returncode in (0, 2), f"rc={res.returncode}\n{res.stdout[-3000:]}"
    log = proj / "reports" / "fix_log.jsonl"
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def test_target_increase_is_regression_and_rolls_back(tmp_path):
    proj = _project(tmp_path, drc_before=4)
    rows = [r for r in _run(proj, _harness(tmp_path, proj, drc_after=6)) if r.get("strategy") == "density_relief"]
    assert rows, "density_relief iteration not logged"
    row = rows[0]
    assert row["verdict"] == "regression"
    regs = row.get("global_regressions") or []
    assert "drc_target_regression:4->6" in regs, regs
    # rolled back to the accepted config (the fix's CORE_UTILIZATION 8 is undone)
    assert "CORE_UTILIZATION = 12" in (proj / "constraints" / "config.mk").read_text()


def test_equal_count_stays_no_improvement(tmp_path):
    proj = _project(tmp_path, drc_before=4)
    rows = [r for r in _run(proj, _harness(tmp_path, proj, drc_after=4)) if r.get("strategy") == "density_relief"]
    assert rows and rows[0]["verdict"] == "no_improvement"
    assert not [g for g in (rows[0].get("global_regressions") or []) if "_target_regression:" in g]


def test_ingest_maps_no_change_with_increase_to_regression():
    assert ingest_run._normalize_verdict("no_improvement", 4.0, 6.0) == "regression"
    assert ingest_run._normalize_verdict("no_improvement", 4.0, 4.0) == "no_change"
    assert ingest_run._normalize_verdict("no_change", 7.5, 10.9) == "regression"
    assert ingest_run._normalize_verdict("recipe_no_effect", 3.0, 3.0) == "no_change"
    assert ingest_run._normalize_verdict("applied", 4.0, 2.0) == "win"


def test_backfill_maps_increase_to_regression():
    assert backfill_fix_events._verdict(4.0, 6.0) == "regression"
    assert backfill_fix_events._verdict(4.0, 4.0) == "no_change"
    assert backfill_fix_events._verdict(4.0, 2.0) == "win"
    assert backfill_fix_events._verdict(4.0, 0.0) == "cleared"


def test_iteration_row_carries_prefix_situation(tmp_path):
    # Same stub harness: the logged situation (knowledge/situation.py) describes the
    # PRE-fix state — util 12 and 4 li.3 violations — not the applied CORE_UTILIZATION 8.
    proj = _project(tmp_path, drc_before=4)
    rows = [r for r in _run(proj, _harness(tmp_path, proj, drc_after=2)) if r.get("strategy") == "density_relief"]
    sit = rows[0].get("situation")
    assert sit and sit["check"] == "drc" and sit["violation_class"] == "li.3"
    assert sit["platform"] == "sky130hs" and sit["die_mode"] == "auto"
    assert sit["util_band"] == "le12" and sit["count_band"] == "1-5"
    # the pre-fix knob value (from diagnose --apply) rides the row: the delta form (B2)
    assert rows[0].get("config_before") == {"CORE_UTILIZATION": "12"}
    assert "memory_rule" not in rows[0]          # a catalogue apply carries no attribution
    # graded severity (Phase G0): the pre-fix DRC count and the post-fix one
    assert rows[0]["severity_before"] == 4.0 and rows[0]["severity_after"] == 2.0


def test_memory_rule_attribution_rides_the_row(tmp_path):
    """A TEHM rule's --apply output (rule id, family, trial flag) is logged on the row,
    so the TEHM adapter attributes the evidence to the rule (amendment D-A1)."""
    proj = _project(tmp_path, drc_before=4)
    extra = (',\\"memory_rule\\":\\"r1\\",\\"strategy_family\\":\\"DENSITY_RELIEF\\",'
             '\\"memory_trial\\":true,\\"witness_selected\\":[]')
    rows = [r for r in _run(proj, _harness(tmp_path, proj, drc_after=2, extra=extra))
            if r.get("strategy") == "density_relief"]
    assert rows[0]["memory_rule"] == "r1" and rows[0]["strategy_family"] == "DENSITY_RELIEF"
    assert rows[0]["memory_trial"] is True
