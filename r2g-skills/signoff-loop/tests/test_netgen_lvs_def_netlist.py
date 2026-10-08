"""Netgen LVS on a run that kept 6_final.def and no ODB.

`run_netgen_lvs.sh` builds its comparison netlist with
`write_verilog -include_pwr_gnd` from 6_final.odb, and refuses to fall back to
ORFS's unpowered 6_final.v -- rightly, since comparing against that
manufactures an implicit-power mismatch that would be logged as a DESIGN
failure. But a harvested corpus run may hold only 6_final.def: keeping every
stage costs ~1.1 GB per design, and the sky130hd corpus here kept none of the
2,497 ODBs.

"No ODB" is not "no power information". `read_lef` + `read_def` rebuilds a
database with the same connectivity, because DEF's SPECIALNETS wildcard
`( * VPWR )` expands to per-instance connections. Verified on nangate45, the
only platform holding both artifacts: the ODB- and DEF-derived powered netlists
have the same instance count and the same VDD/VSS fanout (2127 / 1406 / 1406),
differing only in module port declaration order. On sky130hd the DEF-derived
netlist then took all six verification designs, 1 to 6,921 cells, to "Circuits
match uniquely" -- including the two that KLayout's comparer could not do.

Four defects were hit putting this in, all guarded below:
  - the Verilog gate rejected the run before the powered-netlist step it
    precedes, so a DEF-only run never got that far;
  - the LEF lookup matched `=` but ORFS platform configs write `?=`, leaving a
    tcl with no read_lef at all and openroad answering ORD-0005;
  - PLATFORM_DIR is defined in run_lvs.sh, never in this one;
  - `local` inside the `{ ... }` group command that writes the tcl is a runtime
    error bash -n does not catch.

These tests read the script rather than running Netgen, which needs magic, a
PDK and a real GDS.
"""
import re
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "flow" / "run_netgen_lvs.sh"


@pytest.fixture(scope="module")
def src() -> str:
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    return SCRIPT.read_text()


def test_the_script_is_syntactically_valid(src):
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_a_def_only_run_is_not_rejected_by_the_verilog_gate(src):
    gate = re.search(
        r"if \[\[ -z \"\$VERILOG_NETLIST\" \]\](.*?)\nfi", src, re.S)
    assert gate, "the Verilog-netlist gate is gone"
    body = gate.group(1)
    assert "6_final.def" in body, \
        "the gate must let a DEF-only run through to the powered-netlist step"
    assert "R2G_LVS_DEF_NETLIST" in body, "the fallback needs an off switch"


def test_the_odb_is_still_preferred_when_present(src):
    # Both sides descending from different serializations is one degree more
    # independent than deriving layout and netlist from the same DEF.
    i_odb = src.index('ODB_FILE=$(find')
    i_def = src.index('DEF_FILE=""')
    assert i_odb < i_def, "the ODB must be looked for first"
    assert re.search(r'if \[\[ -z "\$ODB_FILE" && "\$\{R2G_LVS_DEF_NETLIST:-auto\}" != "0" \]\]',
                     src), "the DEF is only consulted when there is no ODB"


def test_the_fallback_reads_liberty_then_lef_then_def(src):
    # Order the generated tcl by the printf/`for` lines that emit it, not by
    # where the strings happen to appear in the file -- the comments mention
    # read_lef too, and keying on that made this test read its own prose.
    # Bound the block by its own delimiters, not by a character count: the
    # 2,500-char window this used broke the moment the gf180 well handling and
    # its comments were added, failing on a script whose read_liberty was still
    # first. A structural bound cannot drift that way.
    end = src.index('} > "$LVS_DIR/write_powered_verilog.tcl"')
    start = src.rindex('POWERED_NETLIST="$LVS_DIR/powered.v"', 0, end)
    block = src[start:end]
    emit = [l.strip() for l in block.splitlines()
            if re.search(r"printf '(read_liberty|read_lef|read_def|write_verilog)", l)
            or "read_liberty" in l and "for _lib" in l]
    seq = []
    for line in emit:
        for cmd in ("read_liberty", "read_lef", "read_def", "write_verilog"):
            if cmd in line:
                seq.append(cmd)
                break
    assert "read_liberty" in seq, \
        "Liberty first: without it write_verilog corrupts the heap (2026-09-23)"
    assert "read_lef" in seq and "read_def" in seq, seq
    assert seq.index("read_liberty") < seq.index("read_lef") < seq.index("read_def"), seq
    assert seq.index("read_def") < seq.index("write_verilog"), seq


def test_lef_lookup_accepts_the_optional_assignment_forms(src):
    # ORFS writes `export TECH_LEF ?= $(PLATFORM_DIR)/lef/...`; matching only
    # `=` produced a tcl with no read_lef and openroad's ORD-0005.
    m = re.search(r'grep -E "\^\[\[:space:\]\]\*\(override.*?\$1\[\[:space:\]\]\*([^"]*)"',
                  src)
    assert m, "the LEF lookup grep is gone"
    assert "?" in m.group(1), f"`?=` not matched by: {m.group(1)!r}"


def test_the_platform_dir_has_a_fallback(src):
    # run_lvs.sh defines PLATFORM_DIR; this script never did, so the lookup
    # silently found nothing.
    assert re.search(r'_pdir="\$\{PLATFORM_DIR:-\$FLOW_DIR/platforms/\$PLATFORM\}"', src), \
        "PLATFORM_DIR must fall back to the flow's platform directory"


def test_no_local_inside_the_tcl_group_command(src):
    # `{ ... } > file` is a group command, not a function: bash rejects `local`
    # there at runtime, and `bash -n` passes it.
    start = src.index("if [[ -n \"$ODB_FILE\" ]]; then")
    end = src.index('write_powered_verilog.tcl"')
    offenders = [l.strip() for l in src[start:end].splitlines()
                 if re.match(r"\s*local\s", l)]
    assert not offenders, f"`local` in a group command: {offenders}"


def test_the_unpowered_netlist_is_still_never_compared_against(src):
    # The whole reason the fallback had to be added rather than relaxing the
    # refusal: an unpowered netlist yields a spurious mismatch that would be
    # recorded as a design failure.
    assert "_powered_netlist_unavailable" in src
    assert "powered_netlist_unavailable" in src
    assert re.search(r"NEVER fall back to the unpowered netlist", src), \
        "the rule explaining why must stay with the code"
