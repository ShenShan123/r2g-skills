"""Strong signoff report provenance (RMD-P0-02, three-platform pilot 2026-07-22).

The old attribution chain was dead on arrival: _restage_for_signoff.sh wrote
its identity marker into the ORFS WORKSPACE, while report_io.run_provenance()
globbed <project>/backend/RUN_*/.r2g_restaged — a path nothing ever wrote — so
every report silently degraded to the `latest_run` guess (all 12 pilot DRC
reports). The fix: a project-side JSON record (backend/.r2g_signoff_run,
written by the shared resolver in _backend_run.sh) that run_provenance reads as
the authoritative `signoff_record` source, carrying the picked run's artifact
digests; extractors also accept an explicit --run-dir.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
EXTRACT = SKILL / "scripts" / "extract"
sys.path.insert(0, str(EXTRACT))

import report_io  # noqa: E402


def _proj(tmp_path, record=None, runs=("RUN_A", "RUN_B")):
    proj = tmp_path / "proj"
    for r in runs:
        (proj / "backend" / r).mkdir(parents=True)
    if record is not None:
        (proj / "backend" / ".r2g_signoff_run").write_text(record)
    return proj


def test_signoff_record_is_authoritative(tmp_path):
    rec = {"run_tag": "RUN_A", "gds_sha256": "aa" * 32, "def_sha256": "bb" * 32}
    proj = _proj(tmp_path, record=json.dumps(rec))
    prov = report_io.run_provenance(proj)
    assert prov["source"] == "signoff_record"
    assert prov["run_tag"] == "RUN_A"
    assert prov["run_dir"].endswith("RUN_A")
    assert prov["gds_sha256"] == "aa" * 32
    assert prov["def_sha256"] == "bb" * 32


def test_explicit_run_dir_wins_over_record(tmp_path):
    proj = _proj(tmp_path, record=json.dumps({"run_tag": "RUN_A"}))
    prov = report_io.run_provenance(proj, proj / "backend" / "RUN_B")
    assert prov["source"] == "explicit"
    assert prov["run_tag"] == "RUN_B"


def test_plain_text_record_tolerated(tmp_path):
    proj = _proj(tmp_path, record="RUN_B\n")
    prov = report_io.run_provenance(proj)
    assert prov["source"] == "signoff_record"
    assert prov["run_tag"] == "RUN_B"


def test_no_record_falls_back_to_latest_run(tmp_path):
    proj = _proj(tmp_path, record=None)
    prov = report_io.run_provenance(proj)
    assert prov["source"] == "latest_run"
    assert prov["run_tag"] == "RUN_B"   # lexicographically newest


def test_garbage_record_falls_back(tmp_path):
    proj = _proj(tmp_path, record="{not json at all")
    prov = report_io.run_provenance(proj)
    # An unreadable record must not crash attribution; the def-graph gate is
    # where an unreadable record HARD-fails publication.
    assert prov["source"] in ("signoff_record", "latest_run")


def test_extract_drc_run_dir_and_digest_carry(tmp_path):
    """extract_drc --run-dir stamps source=explicit; the drc_result.json
    strong-provenance fields (run_tag, gds_sha256, deck digest, toolchain)
    ride into reports/drc.json."""
    proj = _proj(tmp_path)
    drc = proj / "drc"
    drc.mkdir()
    (drc / "6_drc_count.rpt").write_text("0\n")
    json.dump({"status": "clean", "violations": 0, "drc_mode": "full",
               "checker": "klayout_direct", "run_tag": "RUN_A",
               "gds_sha256": "cc" * 32, "deck_sha256": "dd" * 32,
               "klayout_version": "KLayout 0.30.7"},
              open(drc / "drc_result.json", "w"))
    out = tmp_path / "drc.json"
    r = subprocess.run(
        [sys.executable, str(EXTRACT / "extract_drc.py"), str(proj), str(out),
         "--run-dir", str(proj / "backend" / "RUN_A")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["status"] == "clean"
    assert doc["provenance"]["source"] == "explicit"
    assert doc["provenance"]["run_tag"] == "RUN_A"
    assert doc["gds_sha256"] == "cc" * 32
    assert doc["deck_sha256"] == "dd" * 32
    assert doc["checker"] == "klayout_direct"
    assert doc["run_tag"] == "RUN_A"


def test_extract_lvs_netgen_path_stamps_provenance(tmp_path):
    """The netgen (sky130 production) path used to return with NO provenance at
    all — sky130 LVS could never bind to a run. It must now stamp the envelope
    and carry the graded layout's digest."""
    rec = {"run_tag": "RUN_A", "gds_sha256": "ee" * 32}
    proj = _proj(tmp_path, record=json.dumps(rec))
    lvs = proj / "lvs"
    lvs.mkdir()
    json.dump({"tool": "netgen", "status": "clean", "match": "match",
               "mismatch_class": "", "run_tag": "RUN_A",
               "gds_path": "/x/6_final.gds", "gds_sha256": "ee" * 32},
              open(lvs / "netgen_lvs_result.json", "w"))
    out = tmp_path / "lvs.json"
    r = subprocess.run(
        [sys.executable, str(EXTRACT / "extract_lvs.py"), str(proj), str(out)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["status"] == "clean"
    assert doc["tool"] == "netgen"
    assert doc["provenance"]["source"] == "signoff_record"
    assert doc["provenance"]["run_tag"] == "RUN_A"
    assert doc["gds_sha256"] == "ee" * 32


def test_extract_lvs_skip_path_stamps_provenance(tmp_path):
    proj = _proj(tmp_path, record=json.dumps({"run_tag": "RUN_B"}))
    lvs = proj / "lvs"
    lvs.mkdir()
    json.dump({"status": "skipped", "reason": "No LVS rules available"},
              open(lvs / "lvs_result.json", "w"))
    out = tmp_path / "lvs.json"
    r = subprocess.run(
        [sys.executable, str(EXTRACT / "extract_lvs.py"), str(proj), str(out)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["status"] == "skipped"
    assert doc["provenance"]["run_tag"] == "RUN_B"
    assert doc["provenance"]["source"] == "signoff_record"


def test_record_digest_written_by_shared_resolver(tmp_path):
    """_backend_run.sh's writer records the picked run + exact artifact bytes."""
    proj = tmp_path / "proj"
    run = proj / "backend" / "RUN_X" / "results"
    run.mkdir(parents=True)
    (run / "6_final.gds").write_text("GDSDATA")
    (run / "6_final.def").write_text("DEFDATA")
    script = SKILL / "scripts" / "flow" / "_backend_run.sh"
    r = subprocess.run(
        ["bash", "-c",
         f'source "{script}" && '
         f'run=$(r2g_pick_backend_run "{proj}") && '
         f'r2g_write_signoff_record "{proj}" "$run" nangate45 variantX && '
         f'echo "picked=$run"'],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "picked=" in r.stdout and r.stdout.strip().endswith("RUN_X")
    rec = json.loads((proj / "backend" / ".r2g_signoff_run").read_text())
    assert rec["run_tag"] == "RUN_X"
    assert rec["platform"] == "nangate45"
    assert rec["flow_variant"] == "variantX"
    assert rec["gds_sha256"] == hashlib.sha256(b"GDSDATA").hexdigest()
    assert rec["def_sha256"] == hashlib.sha256(b"DEFDATA").hexdigest()
