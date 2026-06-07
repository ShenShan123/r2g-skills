"""fix_signoff.sh emits an enriched, session-keyed fix_log.jsonl."""
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


def test_fix_log_has_session_id_and_violation_class(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
        "# >>> r2g signoff-fix (auto) >>>\nexport MAX_REPAIR_ANTENNAS_ITER_DRT = 10\n"
        "# <<< r2g signoff-fix (auto) <<<\n")
    # drc.json: one antenna category, 5 violations -> after stub makes it 0.
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 5,
         "categories": {"M2_ANTENNA": {"count": 5}}}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # diagnose --next yields one strategy then STOP; --apply is a no-op success.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f /tmp/_did_$$ ]]; then echo -e "STOP\\tresidual\\tdone"; \n'
          '  else echo -e "antenna_diode_repair\\troute\\tdrc"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch /tmp/_did_$$; echo "{}"; fi')
    # run_orfs / run_drc: no-ops. extract_drc: write a CLEAN drc.json (count 0).
    _stub(bindir / "noop.sh", 'exit 0')
    _stub(bindir / "extract.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0,"categories":{}}))\nPY')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc", "--max-iters", "2"], env=env, check=False)

    lines = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    applied = [r for r in lines if r["strategy"] == "antenna_diode_repair"]
    assert applied, "expected an applied iteration row"
    row = applied[0]
    assert row["fix_session_id"]                       # minted, non-empty
    assert row["check"] == "drc"
    assert row["violation_class"] == "M2_ANTENNA"      # dominant category captured
    assert row["from_stage"] == "route"
    assert row["verdict"] == "cleared"                 # after == 0
    assert json.loads(row["before_categories"]) == {"M2_ANTENNA": {"count": 5}}


def test_empty_after_count_is_not_a_phantom_win(tmp_path):
    """Bug #14: if the post-fix re-check report is unparseable (no count key),
    after-count is empty and the verdict must NOT be 'applied'/'win' (which the
    ingester would map to a phantom 'win'). It should be a non-evidence verdict
    the ingester maps to 'inconclusive'."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    # drc.json: one antenna category, 5 violations -> before-count == 5.
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 5,
         "categories": {"M2_ANTENNA": {"count": 5}}}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # diagnose --next yields one strategy then STOP; --apply is a no-op success.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f /tmp/_did2_$$ ]]; then echo -e "STOP\\tresidual\\tdone"; \n'
          '  else echo -e "antenna_diode_repair\\troute\\tdrc"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch /tmp/_did2_$$; echo "{}"; fi')
    _stub(bindir / "noop.sh", 'exit 0')
    # extract_drc: simulate a CRASHED/unparseable re-check — the report has no
    # total_violations / mismatch_count key, so _count() returns '' (after empty).
    _stub(bindir / "extract.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"fail"}))\nPY')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc", "--max-iters", "2"], env=env, check=False)

    lines = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    applied = [r for r in lines if r["strategy"] == "antenna_diode_repair"]
    assert applied, "expected an applied iteration row"
    row = applied[0]
    assert row["after"] is None                        # unparseable -> empty after
    # The crux: a failed re-check must NOT be recorded as 'applied' (-> phantom win)
    # nor 'cleared'/'no_improvement'. It must be a non-evidence verdict.
    assert row["verdict"] not in ("applied", "cleared", "no_improvement")
    assert row["verdict"] == "recheck_unparsed"
