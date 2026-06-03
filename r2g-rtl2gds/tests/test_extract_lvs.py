"""Tests for extract_lvs.py: crash / incomplete / clean detection.

Models the two real failure modes observed in the nangate45 corpus:

* **crash** — ``lvs_run.log`` contains ``ERROR: Signal number: 11`` plus a KLayout
  C++ backtrace (sort_circuit / gen_log_entry / ruby_run_node) ending with
  ``Crash log written to .../klayout_crash.log``.

* **incomplete** — ``6_lvs.log`` reached device extraction
  (``"extract_devices" in: FreePDK45.lylvs:NNN`` / ``"netlist" in: FreePDK45.lylvs:NNN``)
  but the process was killed before it produced a match/mismatch verdict and no
  ``6_lvs.lvsdb`` file was written (logs end with ``make: *** ... Terminated``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import extract_lvs as e

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "extract_lvs.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, *, log_6_lvs: str = "", log_run: str = "",
                  lvsdb: bool = False) -> Path:
    """Build a minimal project dir with lvs/ subdirectory."""
    proj = tmp_path / "proj"
    lvs_dir = proj / "lvs"
    lvs_dir.mkdir(parents=True)
    if log_6_lvs:
        (lvs_dir / "6_lvs.log").write_text(log_6_lvs, encoding="utf-8")
    if log_run:
        (lvs_dir / "lvs_run.log").write_text(log_run, encoding="utf-8")
    if lvsdb:
        # Minimal text-format lvsdb that parses as "match"
        (lvs_dir / "6_lvs.lvsdb").write_text(
            "#%lvsdb-klayout\ncircuits match\n", encoding="utf-8"
        )
    return proj


def _run_script(proj: Path, out: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(proj), str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"script failed: {r.stderr}"
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# Crash log fixtures (modelling real KLayout SIGSEGV output)
# ---------------------------------------------------------------------------

# Representative lvs_run.log fragment for a KLayout crash — contains the
# "ERROR: Signal number: 11" line followed by the backtrace.
_CRASH_RUN_LOG = """\
KLayout 0.30.7
"extract_devices" in: FreePDK45.lylvs:123
    Elapsed: 4.700s  Memory: 1127.00M
"netlist" in: FreePDK45.lylvs:246
ERROR: Signal number: 11
/usr/lib64/klayout/libklayout_db.so.0 +0x18de9de db::NetlistCrossReference::sort_circuit() [??:?]
/usr/lib64/klayout/libklayout_db.so.0 +0x18de7e9 db::NetlistCrossReference::gen_log_entry(...) [??:?]
/lib64/libruby.so.2.5 +0xa3382 ruby_run_node [??:?]
Crash log written to /home/user5/.klayout/klayout_crash.log
"""

# The same backtrace may instead appear only in 6_lvs.log (depending on how
# run_command.py redirected output).
_CRASH_6LVS_LOG = """\
KLayout 0.30.7
"extract_devices" in: FreePDK45.lylvs:123
    Elapsed: 4.700s  Memory: 1127.00M
"netlist" in: FreePDK45.lylvs:246
ERROR: Signal number: 11
/usr/lib64/klayout/libklayout_db.so.0 db::NetlistCrossReference::sort_circuit() [??:?]
/lib64/libruby.so.2.5 +0xa3382 ruby_run_node [??:?]
Crash log written to /home/user5/.klayout/klayout_crash.log
"""

# Incomplete run: reached device extraction but terminated before verdict.
_INCOMPLETE_6LVS_LOG = """\
KLayout 0.30.7
"input" in: FreePDK45.lylvs:53
    Polygons (raw): 398604 (flat)  96 (hierarchical)
    Elapsed: 0.950s  Memory: 900.00M
"extract_devices" in: FreePDK45.lylvs:123
    Elapsed: 4.700s  Memory: 1127.00M
"extract_devices" in: FreePDK45.lylvs:126
    Elapsed: 1.360s  Memory: 1158.00M
"netlist" in: FreePDK45.lylvs:246
"""

# ---------------------------------------------------------------------------
# Unit tests for parse_lvs_log
# ---------------------------------------------------------------------------

def test_parse_lvs_log_detects_crash_in_run_log(tmp_path):
    """Crash signature in lvs_run.log → crash=True, no false log_status."""
    proj = _make_project(tmp_path, log_run=_CRASH_RUN_LOG)
    info = e.parse_lvs_log(proj / "lvs")
    assert info.get("crash") is True, f"expected crash=True; got {info}"
    # crash_line must capture something meaningful
    assert info.get("crash_line"), "expected crash_line to be set"
    # Reached device extraction too (the log has extract_devices)
    assert info.get("reached_device_extraction") is True


def test_parse_lvs_log_detects_crash_in_6lvs_log(tmp_path):
    """Crash signature in 6_lvs.log (when run_command.py --tee wrote it there)."""
    proj = _make_project(tmp_path, log_6_lvs=_CRASH_6LVS_LOG)
    info = e.parse_lvs_log(proj / "lvs")
    assert info.get("crash") is True


def test_parse_lvs_log_detects_incomplete(tmp_path):
    """Log reached extract_devices but has no verdict → reached_device_extraction, no crash."""
    proj = _make_project(tmp_path, log_6_lvs=_INCOMPLETE_6LVS_LOG)
    info = e.parse_lvs_log(proj / "lvs")
    assert info.get("reached_device_extraction") is True
    assert not info.get("crash"), f"unexpected crash flag; info={info}"
    # No match/mismatch verdict in the log
    assert info.get("log_status") is None or info.get("log_status") == ""


def test_parse_lvs_log_clean(tmp_path):
    """Normal clean log → log_status=match, no crash/incomplete flags."""
    clean_log = (
        "KLayout 0.30.7\n"
        '"extract_devices" in: FreePDK45.lylvs:123\n'
        "Netlists match.\n"
    )
    proj = _make_project(tmp_path, log_6_lvs=clean_log)
    info = e.parse_lvs_log(proj / "lvs")
    assert info.get("log_status") == "match"
    assert not info.get("crash")


# ---------------------------------------------------------------------------
# Integration tests: status returned by the main extraction pipeline
# ---------------------------------------------------------------------------

def test_status_crash_when_run_log_has_signal_11(tmp_path):
    """Project with lvs_run.log containing 'ERROR: Signal number: 11' → status='crash'."""
    proj = _make_project(tmp_path, log_run=_CRASH_RUN_LOG)
    out = tmp_path / "lvs.json"
    result = _run_script(proj, out)
    assert result["status"] == "crash", f"expected crash; got {result['status']!r}"
    assert result.get("reason") == "klayout_cpp_crash"
    # Crash line should be surfaced in errors
    errors = (result.get("log_info") or {}).get("errors", [])
    assert any("crash" in e.lower() or "signal" in e.lower() for e in errors), (
        f"expected crash/signal in errors; got {errors}"
    )


def test_status_incomplete_when_reached_devices_no_lvsdb(tmp_path):
    """Project whose 6_lvs.log reached extract_devices with no verdict and no lvsdb
    → status='incomplete', reason='lvs_no_verdict_no_lvsdb'."""
    proj = _make_project(tmp_path, log_6_lvs=_INCOMPLETE_6LVS_LOG)
    # No lvsdb file written
    out = tmp_path / "lvs.json"
    result = _run_script(proj, out)
    assert result["status"] == "incomplete", f"expected incomplete; got {result['status']!r}"
    assert result.get("reason") == "lvs_no_verdict_no_lvsdb"


def test_status_incomplete_requires_no_lvsdb(tmp_path):
    """If lvsdb IS present (somehow) and reached device extraction with a match verdict,
    the status should be 'clean', NOT 'incomplete'."""
    clean_log = (
        "KLayout 0.30.7\n"
        '"extract_devices" in: FreePDK45.lylvs:123\n'
        "Netlists match.\n"
    )
    proj = _make_project(tmp_path, log_6_lvs=clean_log, lvsdb=True)
    out = tmp_path / "lvs.json"
    result = _run_script(proj, out)
    assert result["status"] == "clean", f"expected clean; got {result['status']!r}"


def test_status_unknown_for_uninformative_log(tmp_path):
    """Truly empty/uninformative log (no crash, no verdict, no device extraction) → unknown."""
    proj = _make_project(tmp_path, log_6_lvs="KLayout 0.30.7\n")
    out = tmp_path / "lvs.json"
    result = _run_script(proj, out)
    assert result["status"] == "unknown", f"expected unknown; got {result['status']!r}"


# ---------------------------------------------------------------------------
# Mismatch-class classification (symmetric-matcher residual vs real connectivity)
# Models the KLayout s-expression lvsdb shapes seen in the 2026-06-02 LVS triage.
# ---------------------------------------------------------------------------
_MISMATCH_LOG = "KLayout 0.30.7\nERROR : Netlists don't match\n"


def _make_fail_project(tmp_path: Path, lvsdb_text: str) -> Path:
    proj = tmp_path / "proj"
    lvs_dir = proj / "lvs"
    lvs_dir.mkdir(parents=True)
    (lvs_dir / "6_lvs.log").write_text(_MISMATCH_LOG, encoding="utf-8")
    (lvs_dir / "6_lvs.lvsdb").write_text(lvsdb_text, encoding="utf-8")
    return proj


def test_mismatch_class_symmetric_matcher(tmp_path):
    """Zero net deltas + same-cell instance swaps + ambiguous groups → symmetric_matcher."""
    lvsdb = (
        "circuit(top TOP nomatch\n"
        "   entry(warning description('Matching nets $3003 vs. _2694_ from an ambiguous group of nets'))\n"
        "   entry(warning description('Matching nets $3191 vs. _2727_ from an ambiguous group of nets'))\n"
        "   circuit(2359 601 mismatch)\n"
        "   circuit(7871 598 mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["status"] == "fail"
    assert result["mismatch_class"] == "symmetric_matcher", result
    assert result["net_mismatches"] == 0 and result["circuit_swaps"] == 2


def test_mismatch_class_real_connectivity(tmp_path):
    """An explicit 'not matching any net' error → real_connectivity (priority over swaps)."""
    lvsdb = (
        "circuit(axi2axilite AXI2AXILITE nomatch\n"
        "   entry(error description('Net M_AXI_BREADY is not matching any net from reference netlist'))\n"
        "   entry(warning description('foo from an ambiguous group of nets'))\n"
        "   net(3204 () mismatch)\n"
        "   circuit(10450 3687 mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["mismatch_class"] == "real_connectivity", result


def test_mismatch_class_symmetric_with_balanced_net_deltas(tmp_path):
    """BALANCED unmatched nets (1 schematic-only + 1 layout-only) + a same-cell swap +
    ambiguous group + zero device mismatches → symmetric_matcher.

    Models the corpus reality (aes_core 8+8, vlsi_axi_slave 40+40, iccad2017_unit5_F
    64+64): KLayout's symmetric-matcher leaves perfectly balanced unmatched nets with
    every device matched. The layout is correct; only assignment is ambiguous.
    """
    lvsdb = (
        "circuit(axi_slave AXI_SLAVE nomatch\n"
        "   entry(warning description('x from an ambiguous group of nets'))\n"
        "   net(() 1635 mismatch)\n"
        "   net(1919 () mismatch)\n"
        "   circuit(100 200 mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["mismatch_class"] == "symmetric_matcher", result
    assert result["net_mismatches"] == 2
    assert result["net_mismatches_schematic_only"] == 1
    assert result["net_mismatches_layout_only"] == 1
    assert result["device_mismatches"] == 0


def test_mismatch_class_generic_when_nets_imbalanced(tmp_path):
    """IMBALANCED unmatched nets (2 layout-only vs 1 schematic-only) with no explicit
    'not matching any net' error → generic (a genuine delta, operator review)."""
    lvsdb = (
        "circuit(top TOP nomatch\n"
        "   entry(warning description('x from an ambiguous group of nets'))\n"
        "   net(() 1635 mismatch)\n"
        "   net(1919 () mismatch)\n"
        "   net(2020 () mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["mismatch_class"] == "generic", result
    assert result["net_mismatches_schematic_only"] == 1
    assert result["net_mismatches_layout_only"] == 2


def test_mismatch_class_generic_when_device_mismatch_present(tmp_path):
    """Balanced nets but a DEVICE-count delta → generic (devices must match for a
    benign symmetric label)."""
    lvsdb = (
        "circuit(top TOP nomatch\n"
        "   entry(warning description('x from an ambiguous group of nets'))\n"
        "   net(() 1635 mismatch)\n"
        "   net(1919 () mismatch)\n"
        "   device(5 () mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["mismatch_class"] == "generic", result
    assert result["device_mismatches"] == 1


def test_mismatch_class_real_connectivity_imbalanced_bus_opens(tmp_path):
    """Imbalanced unmatched nets + explicit 'not matching any net' on named ports
    → real_connectivity (models wb2axip_axilsingle: 16 bus opens, 104 vs 120)."""
    lvsdb = (
        "circuit(axilsingle AXILSINGLE nomatch\n"
        "   entry(error description('Net S_AXI_RDATA[15] is not matching any net from reference netlist'))\n"
        "   net(() 100 mismatch)\n"
        "   net(200 () mismatch)\n"
        "   net(201 () mismatch)\n"
        ")\n"
    )
    out = tmp_path / "lvs.json"
    result = _run_script(_make_fail_project(tmp_path, lvsdb), out)
    assert result["mismatch_class"] == "real_connectivity", result


def test_cdl_parse_error_reason(tmp_path):
    """A KLayout SPICE-reader 'Pin count mismatch ... Netlist::read' abort (no verdict,
    no lvsdb) → status unknown with reason cdl_parse_error (models spi_master_single_cs)."""
    log = (
        "KLayout 0.30.7\n"
        "ERROR: Pin count mismatch (7 expected, got 8) for 'DFFR_X1' in "
        "Xr_CS_Inactive_Count\\[-1\\]$_DFFE_PN0P_ at /path/6_final_concat.cdl, line 367 "
        "in Netlist::read\n"
    )
    proj = _make_project(tmp_path, log_6_lvs=log)
    out = tmp_path / "lvs.json"
    result = _run_script(proj, out)
    assert result["status"] == "unknown", result
    assert result.get("reason") == "cdl_parse_error", result
