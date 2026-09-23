"""No powered netlist means LVS was not executed. It is never a design mismatch.

Regression (found by E9H, 2026-09-23): when OpenROAD's
`write_verilog -include_pwr_gnd` failed, run_netgen_lvs.sh printed "WARNING:
powered-netlist generation failed; falling back to …6_final.v" and ran Netgen
against the UNPOWERED netlist. Its own comment says that gives a spurious
implicit-power mismatch (132 vs 626 nets, VPWR fanout 248 vs 1), and the verdict
was recorded as `lvs: mismatch`, a design failure. Every E9H/E9H-ext LVS
mismatch (14/14) was this. The writer crashed deterministically
("free(): unaligned chunk detected", Signal 6/11) because no Liberty was loaded.

The contract under test, end to end:
  run_netgen_lvs.sh  -> netgen_lvs_result.json status "error",
                        reason "powered_netlist_unavailable", Netgen never runs,
                        and the log keeps the exact "powered-netlist generation
                        failed" signature wave-4 re-bucketing keys on;
  extract_lvs.py     -> reports/lvs.json keeps status "error" + reason;
  signoff_gate.py    -> blocks LVS as category "not_executed", not "mismatch";
  ingest_run.py      -> a tool-error-lvs-powered_netlist_unavailable event and
                        no LVS design symptom.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from .test_run_netgen_lvs_timeout_group_kill import OK_MAGIC, _run, _setup

SKILL = Path(__file__).resolve().parents[1]
GATE = SKILL.parent / "def-graph" / "scripts" / "flow" / "signoff_gate.py"
SIGNATURE = "powered-netlist generation failed"

# The E9H shape: heap corruption inside write_verilog, SIGABRT, a partial file.
CRASHING_OPENROAD = """#!/usr/bin/env bash
tcl="${@: -1}"
out=$(sed -n 's/^write_verilog -include_pwr_gnd "\\(.*\\)"$/\\1/p' "$tcl")
printf 'module demo(VPWR' > "$out"
echo "free(): unaligned chunk detected in tcache 2"
echo "Signal 6 received"
exit 134
"""
NETGEN_MUST_NOT_RUN = """#!/usr/bin/env bash
echo ran > "$(dirname "$0")/netgen_ran"
echo "Netlists do not match."
"""


def test_writer_crash_is_not_executed_never_mismatch(tmp_path: Path) -> None:
    skill, orfs, pdk, bindir, proj = _setup(tmp_path, OK_MAGIC, NETGEN_MUST_NOT_RUN,
                                            CRASHING_OPENROAD)
    r = _run(tmp_path, skill, orfs, pdk, bindir, proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert SIGNATURE in r.stderr                     # the exact re-bucketing signature
    assert not (bindir / "netgen_ran").exists()      # no comparison against 6_final.v
    result = json.loads((proj / "lvs" / "netgen_lvs_result.json").read_text())
    assert result["status"] == "error"
    assert result["reason"] == "powered_netlist_unavailable"
    assert "exit=134" in result["detail"]


def test_missing_odb_is_not_executed_either(tmp_path: Path) -> None:
    skill, orfs, pdk, bindir, proj = _setup(tmp_path, OK_MAGIC, NETGEN_MUST_NOT_RUN)
    next((orfs / "flow" / "results").rglob("6_final.odb")).unlink()
    r = _run(tmp_path, skill, orfs, pdk, bindir, proj)

    assert r.returncode == 1
    assert SIGNATURE in r.stderr
    assert not (bindir / "netgen_ran").exists()
    result = json.loads((proj / "lvs" / "netgen_lvs_result.json").read_text())
    assert (result["status"], result["reason"]) == ("error", "powered_netlist_unavailable")


def test_liberty_is_loaded_before_the_odb(tmp_path: Path) -> None:
    """Root cause: this OpenROAD's write_verilog corrupts the heap without Liberty."""
    skill, orfs, pdk, bindir, proj = _setup(
        tmp_path, OK_MAGIC, "#!/usr/bin/env bash\necho 'Circuits match uniquely.'\n")
    r = _run(tmp_path, skill, orfs, pdk, bindir, proj)

    assert r.returncode == 0, r.stdout + r.stderr
    tcl = (proj / "lvs" / "openroad_seen.tcl").read_text().splitlines()
    libs = [i for i, ln in enumerate(tcl) if ln.startswith("read_liberty ")]
    assert libs and max(libs) < tcl.index(next(ln for ln in tcl if ln.startswith("read_db ")))
    result = json.loads((proj / "lvs" / "netgen_lvs_result.json").read_text())
    assert result["status"] == "clean"
    assert result["reference_netlist"].endswith("powered.v")


def test_extract_lvs_keeps_the_non_verdict(tmp_path: Path) -> None:
    lvs = tmp_path / "proj" / "lvs"
    lvs.mkdir(parents=True)
    (lvs / "netgen_lvs_result.json").write_text(json.dumps(
        {"tool": "netgen", "status": "error", "reason": "powered_netlist_unavailable",
         "detail": "openroad write_verilog exit=134"}))
    out = tmp_path / "proj" / "reports" / "lvs.json"
    subprocess.run(["python3", str(SKILL / "scripts" / "extract" / "extract_lvs.py"),
                    str(tmp_path / "proj"), str(out)], check=True, capture_output=True)
    rep = json.loads(out.read_text())
    assert (rep["status"], rep["reason"]) == ("error", "powered_netlist_unavailable")


def _gate():
    spec = importlib.util.spec_from_file_location("signoff_gate_t", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("lvs,category", [
    ({"status": "error", "reason": "powered_netlist_unavailable"}, "not_executed"),
    ({"status": "mismatch"}, "mismatch"),
])
def test_gate_separates_not_executed_from_mismatch(tmp_path: Path, lvs, category) -> None:
    (tmp_path / "lvs.json").write_text(json.dumps(lvs))
    chk = _gate()._check_lvs(str(tmp_path))
    assert chk["category"] == category
    assert chk["status"] not in _gate().LVS_OK           # still blocks the dataset


def test_ingest_records_a_tool_error_not_an_lvs_symptom(tmp_knowledge_dir: Path,
                                                        tmp_path: Path) -> None:
    import ingest_run
    import knowledge_db

    proj = tmp_path / "d"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = d\nexport PLATFORM = sky130hd\n")
    (proj / "reports").mkdir()
    (proj / "reports" / "drc.json").write_text(json.dumps({"status": "clean"}))
    (proj / "reports" / "lvs.json").write_text(json.dumps(
        {"status": "error", "reason": "powered_netlist_unavailable",
         "detail": "openroad write_verilog exit=134"}))
    run = proj / "backend" / "RUN_2026-09-23_00-00-00"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text("".join(
        json.dumps({"stage": s, "status": 0}) + "\n"
        for s in ("synth", "floorplan", "place", "cts", "route", "finish")))
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    run_id = ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")

    sigs = [r[0] for r in conn.execute(
        "SELECT signature FROM failure_events WHERE run_id=?", (run_id,))]
    assert sigs == ["tool-error-lvs-powered_netlist_unavailable"]
    check = conn.execute("SELECT signature_json FROM run_violations WHERE run_id=?",
                         (run_id,)).fetchone()[0]
    assert '"lvs"' not in check                          # no LVS design symptom
    row = dict(zip(("lvs_status",), conn.execute(
        "SELECT lvs_status FROM runs WHERE run_id=?", (run_id,)).fetchone()))
    assert row["lvs_status"] == "error"
    assert not knowledge_db.is_success({"orfs_status": "pass", "drc_status": "clean",
                                        "lvs_status": "error", "rcx_status": None})
