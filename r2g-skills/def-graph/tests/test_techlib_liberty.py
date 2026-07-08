"""Tests for techlib.liberty — the consolidated liberty/DB parser + classifiers.

Behavioral equivalence to the original ``features/lib_db.py`` was proven during the
migration (Task 3) by a full-dict ``==`` oracle comparison and the byte-for-byte CSV
gate (tests/test_techlib_crossplatform.py). That oracle module was deleted in Task 9,
so these tests now pin ``techlib.liberty`` against KNOWN values and real-PDK behavior:

  * Real std-cell libs (nangate45, sky130hd) parse to many cells, each with area>0.
  * Getters return positive, finite area/power/cap on real cells; pin directions are
    valid liberty tokens.
  * Classifiers (direction_id, infer_net_type_id, is_tap_master) return hard-coded
    expected ids on known inputs — the durable contract, no oracle needed.
  * .lib.gz decompression works on asap7 + gf180 (non-empty cells dict).
  * tap patterns: with R2G_PLATFORM=gf180, is_tap_master recognises gf180-style names.
  * "no liberty" warning: load_liberty_db([]) emits the WARN to stderr and returns a
    DB with empty sources['lib'] / cells.

Tech lib paths are resolved from $ORFS_ROOT first, then the literal machine-local
fallback below. Tests SKIP (never fail) when the file is absent, so the suite runs
on a bare checkout.
"""
from __future__ import annotations

import glob
import os

import pytest

from techlib import liberty


# --------------------------------------------------------------------------- #
# Path resolution — ORFS root first, machine-local fallback.                  #
# --------------------------------------------------------------------------- #
def _platforms_dir() -> str | None:
    """Return the ORFS platforms directory, or None if not present.

    Prefers $ORFS_ROOT/flow/platforms; falls back to the literal path below
    which is machine-local. Returns None when neither exists — tests SKIP,
    not fail.
    """
    candidates: list[str] = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    # Machine-local fallback for this dev box; absent elsewhere -> tests SKIP, not fail.
    candidates.append("/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms")
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _lib_path(platform: str) -> str | None:
    """Return the primary liberty path for a platform, or None if absent."""
    pdir = _platforms_dir()
    if not pdir:
        return None
    literal: dict[str, str] = {
        "nangate45": "nangate45/lib/NangateOpenCellLibrary_typical.lib",
        "sky130hd": "sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
    }
    if platform in literal:
        path = os.path.join(pdir, literal[platform])
        return path if os.path.isfile(path) else None
    return None


def _gz_lib_path(platform: str) -> str | None:
    """Return a single .lib.gz path for asap7 or gf180, or None if absent.

    Picks deterministically (sorted-first) so tests are reproducible.
    """
    pdir = _platforms_dir()
    if not pdir:
        return None
    if platform == "asap7":
        # prefer NLDM TT corner
        matches = sorted(glob.glob(os.path.join(pdir, "asap7", "lib", "NLDM", "*_TT_*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(os.path.join(pdir, "asap7", "lib", "**", "*.lib.gz"),
                                        recursive=True))
        return matches[0] if matches else None
    if platform == "gf180":
        # prefer tt 5v00 corner; fall back to any
        matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*tt*5v00*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*.lib.gz")))
        return matches[0] if matches else None
    return None


def _lib_or_skip(platform: str) -> str:
    path = _lib_path(platform)
    if not path:
        pytest.skip(f"liberty absent for {platform} (machine-local ORFS platforms)")
    return path


def _gz_or_skip(platform: str) -> str:
    path = _gz_lib_path(platform)
    if not path:
        pytest.skip(f".lib.gz absent for {platform} (machine-local ORFS platforms)")
    return path


# --------------------------------------------------------------------------- #
# DB parse — real std-cell libs yield a populated, well-formed DB.             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["nangate45", "sky130hd"])
def test_db_parse_real_lib(platform):
    """load_liberty_db([path]) yields a populated DB: many cells + the source path."""
    path = _lib_or_skip(platform)
    db = liberty.load_liberty_db([path])
    # A real std-cell lib must have many cells.
    assert len(db["cells"]) > 10, f"{platform}: suspiciously few cells ({len(db['cells'])})"
    # The parsed source path must be recorded.
    assert path in db["sources"]["lib"], f"{platform}: lib path missing from sources"
    # Every cell must carry a name + pins dict (DB shape contract).
    for cell in list(db["cells"].values())[:20]:
        assert "name" in cell and "pins" in cell, f"{platform}: malformed cell {cell!r}"


# --------------------------------------------------------------------------- #
# Classifier / getter equivalence.                                             #
# --------------------------------------------------------------------------- #
def _sample_cells_pins(db: dict, n_cells: int = 10) -> list[tuple[str, str]]:
    """Return up to n_cells (cell_name, pin_name) pairs from a liberty DB."""
    result: list[tuple[str, str]] = []
    for _key, cell in list(db["cells"].items())[:n_cells]:
        cname = cell["name"]
        for pin_name in list(cell["pins"].keys())[:3]:
            result.append((cname, pin_name))
    return result


@pytest.mark.parametrize("platform", ["nangate45", "sky130hd"])
def test_getters_real_lib(platform):
    """get_cell_area/power and get_pin_cap_fF/direction return well-formed values.

    On a real std-cell lib the getters must yield finite, non-negative numbers and
    valid liberty direction tokens on the cells/pins they actually parsed.
    """
    path = _lib_or_skip(platform)
    db = liberty.load_liberty_db([path])

    samples = _sample_cells_pins(db)
    assert len(samples) > 0, "No cells sampled from the parsed DB"

    # get_pin_direction returns an UPPER-cased liberty token ("" when unknown).
    valid_dirs = {"INPUT", "OUTPUT", "INOUT", "INTERNAL", "TRISTATE", ""}
    for cname, pname in samples:
        area = liberty.get_cell_area(cname, db)
        assert isinstance(area, (int, float)) and area >= 0.0, f"bad area for {cname}: {area!r}"
        power = liberty.get_cell_power(cname, db)
        assert isinstance(power, (int, float)) and power >= 0.0, f"bad power for {cname}: {power!r}"
        cap = liberty.get_pin_cap_fF(cname, pname, db)
        assert isinstance(cap, float) and cap >= 0.0, f"bad cap for {cname}/{pname}: {cap!r}"
        pdir = liberty.get_pin_direction(cname, pname, db)
        assert pdir in valid_dirs, f"unexpected direction {pdir!r} for {cname}/{pname}"
        # classify_pin_type returns an int id; just exercise it without crashing.
        assert isinstance(liberty.classify_pin_type(cname, pname, db), int)

    # Guard: real std-cell areas must be positive (a zero-area first cell means the
    # parser silently dropped the area attribute).
    first_cell = list(db["cells"].values())[0]["name"]
    assert liberty.get_cell_area(first_cell, db) > 0.0, \
        f"First cell {first_cell!r} has zero area — lib parse likely failed"


def test_classifiers_pinned_values():
    """direction_id, infer_net_type_id, is_tap_master against KNOWN expected values.

    These are pure-logic classifiers (no liberty file needed), so this test runs
    unconditionally — even on a bare checkout without ORFS platforms. The expected
    ids are the durable contract (INPUT=0/OUTPUT=1/INOUT=2/FEEDTHRU=3/else -1; net
    types POWER=1/GROUND=2/CLOCK=3/RESET=4/SCAN=5/SIGNAL=0).
    """
    # direction_id — pinned ids (case-insensitive; unknown/empty/None -> -1).
    assert liberty.direction_id("INPUT") == 0
    assert liberty.direction_id("OUTPUT") == 1
    assert liberty.direction_id("INOUT") == 2
    assert liberty.direction_id("FEEDTHRU") == 3
    assert liberty.direction_id("input") == 0
    assert liberty.direction_id("output") == 1
    assert liberty.direction_id("") == -1
    assert liberty.direction_id(None) == -1
    assert liberty.direction_id("UNKNOWN") == -1

    # infer_net_type_id — (net_name, net_use, is_clock) -> expected id.
    cases = [
        (("VDD", "POWER", False), 1),
        (("VSS", "GROUND", False), 2),
        (("clk", "", False), 3),       # 'clk' token
        (("clk_core", "", True), 3),   # is_clock flag
        (("reset_n", "", False), 4),   # 'reset' in name
        (("scan_en", "", False), 5),   # 'scan' token
        (("data_out", "", False), 0),  # plain signal
        (("", "", False), 0),
    ]
    for (net_name, net_use, is_clock), expected in cases:
        got = liberty.infer_net_type_id(net_name, net_use, is_clock)
        assert got == expected, \
            f"infer_net_type_id({net_name!r}, {net_use!r}, {is_clock}) = {got}, expected {expected}"

    # is_tap_master — "TAP" substring matches nangate/sky130/asap7 tap masters; std
    # cells must NOT match (guards against a too-broad pattern).
    for name in ["TAPCELL_X1", "sky130_fd_sc_hd__tapvpwrvgnd_1", "TAPCELL_ASAP7_75t_L"]:
        assert liberty.is_tap_master(name) is True, \
               f"Expected {name!r} to be recognised as tap cell"
    for name in ["INV_X1", "DFF_X1", "AND2_X1"]:
        assert liberty.is_tap_master(name) is False, \
               f"Expected {name!r} NOT to be recognised as tap cell"


# --------------------------------------------------------------------------- #
# .lib.gz decompression (asap7 + gf180).                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["asap7", "gf180"])
def test_gz_lib_parses(platform):
    """.lib.gz loads to a non-empty cells dict (gzip decompression path works)."""
    path = _gz_or_skip(platform)
    db_new = liberty.load_liberty_db([path])
    # asap7/gf180 are full std-cell libs; >10 catches a truncated/partial decompress.
    assert len(db_new["cells"]) > 10, \
        f"{platform}: .lib.gz parse returned only {len(db_new['cells'])} cells (truncated?)"
    # Guard: sources must be populated
    assert path in db_new["sources"]["lib"], f"{platform}: .lib.gz path missing from sources"


# --------------------------------------------------------------------------- #
# Tap-pattern: gf180 FILLTIE / ENDCAP recognition.                            #
# --------------------------------------------------------------------------- #
def test_tap_patterns_gf180(monkeypatch):
    """With R2G_PLATFORM=gf180, FILLTIE/ENDCAP names are recognised as tap masters."""
    monkeypatch.setenv("R2G_PLATFORM", "gf180")
    gf180_tap_names = [
        "gf180mcu_fd_sc_mcu7t5v0__filltie",
        "gf180mcu_fd_sc_mcu7t5v0__endcap",
        "FILLTIE_X1",
        "ENDCAP_EDGE",
    ]
    for name in gf180_tap_names:
        assert liberty.is_tap_master(name) is True, \
               f"Expected gf180 name {name!r} to be recognised as tap master"
    # Negative control under the same env: a plain std cell must NOT match, so the
    # gf180 FILLTIE/ENDCAP extras can't accidentally over-match.
    assert liberty.is_tap_master("gf180mcu_fd_sc_mcu7t5v0__inv_1") is False


# --------------------------------------------------------------------------- #
# "no liberty" warning emitted + empty DB returned.                            #
# --------------------------------------------------------------------------- #
def test_no_liberty_warning(capsys):
    """load_liberty_db([]) emits the WARN to stderr and returns an empty DB."""
    db_new = liberty.load_liberty_db([])
    captured = capsys.readouterr()
    assert "WARN" in captured.err, f"Expected WARN on stderr, got: {captured.err!r}"
    assert db_new["sources"]["lib"] == [], \
           f"sources['lib'] should be empty, got {db_new['sources']['lib']!r}"
    assert db_new["cells"] == {}, \
           f"cells should be empty, got {db_new['cells']!r}"


def test_no_liberty_warning_none_input(capsys, monkeypatch):
    """load_liberty_db() with no args (fallback to empty env var) emits WARN."""
    monkeypatch.delenv("R2G_LIB_FILES", raising=False)
    db_new = liberty.load_liberty_db()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert db_new["cells"] == {}


def test_quoted_attribute_values_parse(tmp_path):
    """sky130hd/hs write `direction : "input";` and `clock : "true";` (ihp:
    `clock : "true" ;`) — quoted simple-attribute values must parse identically
    to unquoted ones. Regression for the 2026-07-05 sky130 direction/clock
    quote bug (every sky130 std-cell pin lost its direction; every DFF its
    clock flag)."""
    lib = tmp_path / "quoted.lib"
    lib.write_text(
        'library (quoted) {\n'
        '  capacitive_load_unit(1.0000000000, "pf");\n'
        '  cell ("dff_q") {\n'
        '    area : 5.0;\n'
        '    ff ("IQ","IQ_N") { clocked_on : "CLK"; }\n'
        '    pin ("D") {\n'
        '      direction : "input";\n'
        '      capacitance : "0.0021";\n'
        '    }\n'
        '    pin ("CLK") {\n'
        '      direction : input ;\n'
        '      clock : "true" ;\n'
        '      capacitance : 0.0017;\n'
        '    }\n'
        '    pin ("Q") {\n'
        '      direction : "output";\n'
        '      max_capacitance : "0.30";\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    db = liberty.load_liberty_db(str(lib))
    # sky130 quotes the cap unit too: `capacitive_load_unit(1.0, "pf");` —
    # missing it left cap_scale_ff at 1.0 (pin caps 1000x too small).
    assert db["cap_scale_ff"] == pytest.approx(1000.0)
    pins = db["cells"]["DFF_Q"]["pins"]  # cell keys are _norm_key-uppercased
    assert pins["D"]["direction"] == "INPUT"
    assert pins["D"]["capacitance"] == pytest.approx(0.0021 * db["cap_scale_ff"])
    assert pins["CLK"]["direction"] == "INPUT"
    assert pins["CLK"]["clock"] is True
    assert pins["Q"]["direction"] == "OUTPUT"
    assert pins["Q"]["max_capacitance"] == pytest.approx(0.30 * db["cap_scale_ff"])


def test_real_sky130hd_pins_have_directions():
    """On the real sky130hd tt lib, (nearly) every std-cell pin must resolve a
    direction — the quote bug made this 0/1771 before the fix."""
    path = _lib_path("sky130hd")
    if not path:
        pytest.skip("sky130hd liberty not available")
    db = liberty.load_liberty_db(path)
    total = empty = clocks = 0
    for info in db["cells"].values():
        for pi in info.get("pins", {}).values():
            total += 1
            empty += 0 if pi.get("direction") else 1
            clocks += 1 if pi.get("clock") else 0
    assert total > 1000
    assert empty == 0, f"{empty}/{total} pins lost direction"
    assert clocks > 0, "no clock pins flagged (quoted `clock : \"true\";` missed)"


# --------------------------------------------------------------------------- #
# bus()/bundle() groups + bus-member lookup (2026-07-06 nangate45 fakeram audit)
# --------------------------------------------------------------------------- #
_BUS_LIB = """
library (buslib) {
  capacitive_load_unit (1,ff);
  cell (fakeram45_256x32) {
    area : 5065.704;
    pin(clk)   {
      direction : input;
      clock : true;
      capacitance : 25.000;
    }
    pin(we_in) {
      direction : input;
      capacitance : 10.000;
    }
    bus(addr_in) {
      bus_type : fakeram45_256x32_ADDRESS;
      direction : input;
      capacitance : 5.000;
      timing() {
        related_pin : clk;
      }
    }
    bus(rd_out) {
      bus_type : fakeram45_256x32_DATA;
      direction : output;
      max_capacitance : 500.000;
    }
  }
}
"""


def test_bus_group_members_resolve(tmp_path):
    """DEF-style per-bit pins (addr_in[3]) resolve via the bus() base entry.

    Macro liberty declares direction/capacitance ONCE at the bus() level with no
    per-bit pin() members; without the bus parse + [idx] fallback every macro bus
    pin classified 14 with cap 0 (nangate45 fakeram audit 2026-07-06).
    """
    p = tmp_path / "bus.lib"
    p.write_text(_BUS_LIB)
    db = liberty.load_liberty_db([str(p)])
    # scalar pins unaffected
    assert liberty.get_pin_direction("fakeram45_256x32", "clk", db) == "INPUT"
    # bus base parsed
    assert liberty.get_pin_direction("fakeram45_256x32", "addr_in", db) == "INPUT"
    # per-bit members fall back to the bus entry: direction, cap, classification
    assert liberty.get_pin_direction("fakeram45_256x32", "addr_in[3]", db) == "INPUT"
    assert liberty.get_pin_load_cap_fF("fakeram45_256x32", "addr_in[7]", db) == 5.0
    assert liberty.get_pin_direction("fakeram45_256x32", "rd_out[31]", db) == "OUTPUT"
    # output bus member: max_capacitance is a drive limit, NOT a load
    assert liberty.get_pin_load_cap_fF("fakeram45_256x32", "rd_out[0]", db) == 0.0
    # classification: input bus member no longer the unclassified 14 bucket
    # (addr_in -> the A-prefix input group 0; rd_out -> output 4)
    assert liberty.classify_pin_type("fakeram45_256x32", "addr_in[3]", db) == 0
    assert liberty.classify_pin_type("fakeram45_256x32", "rd_out[0]", db) == 4


def test_select_name_on_output_pin_is_output(tmp_path):
    """FA/HA sum output `S` must classify OUTPUT (4), not select (10) — audit F3."""
    lib = """
library (fal) {
  cell (FA_X1) {
    pin(S)  {
      direction : output;
      max_capacitance : 60.0;
    }
    pin(CI) {
      direction : input;
      capacitance : 1.6;
    }
  }
  cell (MUX2_X1) {
    pin(S) {
      direction : input;
      capacitance : 1.7;
    }
  }
}
"""
    p = tmp_path / "fa.lib"
    p.write_text(lib)
    db = liberty.load_liberty_db([str(p)])
    assert liberty.classify_pin_type("FA_X1", "S", db) == 4       # sum output
    assert liberty.classify_pin_type("MUX2_X1", "S", db) == 10    # true select


def test_statetable_marks_sequential(tmp_path):
    """CLKGATE*-style cells hold state via statetable() — audit F4."""
    lib = """
library (icg) {
  cell (CLKGATE_X1) {
    statetable ("CK E", "IQ") { table : "L L : - : L, L H : - : H "; }
    pin(GCK) { direction : output; }
  }
  cell (AND2_X1) {
    pin(ZN) { direction : output; }
  }
}
"""
    p = tmp_path / "icg.lib"
    p.write_text(lib)
    db = liberty.load_liberty_db([str(p)])
    assert db["cells"]["CLKGATE_X1"]["is_sequential"] is True
    assert db["cells"]["AND2_X1"]["is_sequential"] is False
