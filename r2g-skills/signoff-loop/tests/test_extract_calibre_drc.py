"""Tests for extract_calibre_drc.py — signoff-grade Calibre DRC results parsing.

Covers: results-DB violation counting, clean detection, the mtime freshness guard
(built in from day one so this extractor cannot fabricate clean like the KLayout DRC
extractor did on 2026-06-30), and honoring a fresh skip/incompatible marker.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "extract_calibre_drc.py"

# A realistic Calibre ASCII DRC results database with 3 violations across 2 rules:
#   M1.S.1        -> 2 results (two polygons)
#   V2.M3.AUX.2   -> 1 result  (one edge)
# The coordinate records must NOT be mis-parsed as rulecheck headers.
_RESULTS_DB_3 = """\
top_cell
1000 0.001
M1.S.1
2 2 0.01
p 4
0 0
0 100
100 100
100 0
p 4
200 0
200 100
300 100
300 0
V2.M3.AUX.2
1 1 0.02
e 2
50 50
60 60
"""

# A clean run: every rulecheck reports 0 results (Calibre still emits the header).
_RESULTS_DB_CLEAN = """\
top_cell
1000 0.001
M1.S.1
0 0 0.01
V2.M3.AUX.2
0 0 0.02
"""


def _run(proj: Path, out: Path):
    return subprocess.run([sys.executable, str(SCRIPT), str(proj), str(out)],
                          capture_output=True, text=True)


def _mk(tmp_path: Path, *, results_db: str | None, with_runlog: bool = False,
        marker: dict | None = None) -> Path:
    proj = tmp_path / "proj"
    cal = proj / "drc" / "calibre"
    cal.mkdir(parents=True)
    if results_db is not None:
        (cal / "top_cell.drc.results").write_text(results_db, encoding="utf-8")
    if with_runlog:
        (cal / "calibre_drc_run.log").write_text("calibre -drc\n", encoding="utf-8")
    if marker is not None:
        (proj / "drc" / "calibre_drc_result.json").write_text(
            json.dumps(marker), encoding="utf-8")
    return proj


def test_counts_violations_by_rule(tmp_path):
    proj = _mk(tmp_path, results_db=_RESULTS_DB_3)
    out = tmp_path / "o.json"
    r = _run(proj, out); assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    assert res["status"] == "fail", res
    assert res["total_violations"] == 3, res
    assert res["engine"] == "calibre"
    assert res["categories"]["M1.S.1"]["count"] == 2
    assert res["categories"]["V2.M3.AUX.2"]["count"] == 1


def test_clean_when_all_rulechecks_zero(tmp_path):
    proj = _mk(tmp_path, results_db=_RESULTS_DB_CLEAN)
    out = tmp_path / "o.json"
    r = _run(proj, out); assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    assert res["status"] == "clean", res
    assert res["total_violations"] == 0, res


def test_stale_results_db_not_reported_clean(tmp_path):
    """A clean results DB OLDER than a fresh calibre_drc_run.log must NOT read clean."""
    proj = _mk(tmp_path, results_db=_RESULTS_DB_CLEAN, with_runlog=True)
    cal = proj / "drc" / "calibre"
    # Backdate the results DB far behind the run log (stale).
    db = cal / "top_cell.drc.results"
    st = db.stat(); os.utime(db, (st.st_atime, st.st_mtime - 900_000.0))
    out = tmp_path / "o.json"
    r = _run(proj, out); assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    assert res["status"] == "stale", res
    assert res["total_violations"] is None, res


def test_fresh_results_db_still_clean(tmp_path):
    """Guard must not over-fire: a results DB written in the same run stays clean."""
    proj = _mk(tmp_path, results_db=_RESULTS_DB_CLEAN, with_runlog=True)
    cal = proj / "drc" / "calibre"
    # Make the run log slightly older than the DB (healthy ordering).
    log = cal / "calibre_drc_run.log"
    st = log.stat(); os.utime(log, (st.st_atime, st.st_mtime - 5.0))
    out = tmp_path / "o.json"
    r = _run(proj, out); assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["status"] == "clean"


def test_honors_fresh_skip_marker(tmp_path):
    """When run_calibre_drc.sh wrote status=skipped (deck missing), honor it."""
    proj = _mk(tmp_path, results_db=None,
               marker={"status": "skipped", "reason": "deck_missing", "engine": "calibre"})
    out = tmp_path / "o.json"
    r = _run(proj, out); assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    assert res["status"] == "skipped", res
    assert res["reason"] == "deck_missing"
