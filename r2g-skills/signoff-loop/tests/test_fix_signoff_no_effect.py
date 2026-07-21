"""fix_signoff.sh no-effect guard (pilot P1-1, 2026-07-21).

When a fix reflow produces a BYTE-IDENTICAL 6_final.def, the strategy provably
had no physical effect: the loop must record verdict `recipe_no_effect`, count it
toward the antenna non-convergence exit, and SKIP the expensive signoff tool
re-run (the pilot burned ~5,200s of full DRC re-grading an unchanged layout).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"

DEF_CONTENT = "DESIGN demo ;\nCOMPONENTS 4 ;\nEND DESIGN\n"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_identical_layout_skips_signoff_rerun_and_nonconverges(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 4,
         "categories": {"M2_ANTENNA": {"count": 4}}}))
    # Pre-existing backend run: the pre-reflow layout fingerprint source.
    run0 = proj / "backend" / "RUN_2026-07-21_00-00-00" / "results"
    run0.mkdir(parents=True)
    (run0 / "6_final.def").write_text(DEF_CONTENT)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # diagnose always offers the antenna strategy with a route rerun.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then echo -e "antenna_diode_repair\\troute\\tdrc";\n'
          'elif [[ "$*" == *"--apply"* ]]; then echo "{}"; fi')
    # run_orfs creates a NEW run dir whose DEF is BYTE-IDENTICAL (the no-op repair).
    _stub(bindir / "run_orfs.sh",
          'd="$1/backend/RUN_$(date +%s%N)"\n'
          'mkdir -p "$d/results"\n'
          'cat > "$d/results/6_final.def" <<\'DEF\'\n'
          + DEF_CONTENT + 'DEF')
    # The expensive signoff tool: leaves a sentinel — it must NEVER run.
    _stub(bindir / "run_drc.sh", 'touch "$(dirname "$1")/DRC_WAS_RERUN"')
    _stub(bindir / "extract.py", 'touch "$(dirname "$1")/EXTRACT_WAS_RERUN"')
    _stub(bindir / "noop.sh", 'exit 0')

    env = dict(os.environ,
               R2G_JOURNAL="0",
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "run_orfs.sh"),
               R2G_RUN_DRC=str(bindir / "run_drc.sh"),
               R2G_RUN_LVS=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"),
               R2G_EXTRACT_LVS=str(bindir / "noop.sh"))
    r = subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                        "--check", "drc"],
                       env=env, capture_output=True, text=True, timeout=120)
    # Residual remains (drc.json still fail) -> rc 2; the run itself must not crash.
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)

    rows = [json.loads(l) for l in
            (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    verdicts = [row["verdict"] for row in rows if row.get("iter")]
    # iter1: layout unchanged -> recipe_no_effect; iter2 (same antenna strategy,
    # still unchanged): the CONSECUTIVE antenna counter reaches 2 -> terminal
    # antenna_nonconverged with the persistent marker.
    assert verdicts[0] == "recipe_no_effect", rows
    assert "antenna_nonconverged" in verdicts, rows
    assert (proj / "reports" / "antenna_nonconverged.json").is_file()
    # The whole point: the expensive DRC re-grade never ran.
    assert not (tmp_path / "DRC_WAS_RERUN").exists()
    assert not list(tmp_path.rglob("DRC_WAS_RERUN"))
    assert not list(tmp_path.rglob("EXTRACT_WAS_RERUN"))
    assert "skipping signoff re-run" in r.stdout
