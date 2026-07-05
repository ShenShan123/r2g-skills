"""Unit tests for the 2026-07-05 feature-semantics fixes (RTL2Graph integration audit).

Three defects surfaced while verifying the RTL2Graph pipeline against ODB ground
truth (they predate RTL2Graph — the skill's extractors shared them via the common
feature_test_v2/v3 ancestry):

  1. nodes_net counted a DEF ``PIN`` connection by its raw DEF DIRECTION, but DEF
     pin direction is from the CHIP's perspective — an INPUT port *drives* the
     net, an OUTPUT port *sinks* it. Measured: every output-port net came out
     2-driver/0-sink on cordic nangate45.
  2. nodes_net's ``connects_macro_flag`` was hardwired 0. It is now derived from
     ``techlib.liberty.macro_cell_keys`` (masters that only exist in the
     per-design macro libs = R2G_LIB_FILES minus R2G_SC_LIB_FILES).
  3. nodes_pin summed ``get_pin_cap_fF`` per net, whose OUTPUT-pin fallback is
     liberty ``max_capacitance`` — a drive LIMIT, not a load. Measured 62.54 fF
     vs a true 3.19 fF load on cordic net _0062_. ``get_pin_load_cap_fF`` sums
     input loads only.

All fixtures are synthetic (no ORFS/design_cases dependency).
"""
from __future__ import annotations

import csv
import sys
import textwrap

import pytest

import nodes_net
import nodes_pin
from techlib.liberty import (
    get_pin_load_cap_fF,
    load_liberty_db,
    macro_cell_keys,
    norm_cell_key,
)


STD_LIB = textwrap.dedent(
    """
    library (std) {
      capacitive_load_unit (1, ff);
      nom_voltage : 1.10;
      cell (INV) {
        area : 1.0;
        cell_leakage_power : 2.0;
        pin (A) {
          direction : input;
          capacitance : 1.5;
        }
        pin (ZN) {
          direction : output;
          max_capacitance : 50.0;
        }
      }
    }
    """
)

MACRO_LIB = textwrap.dedent(
    """
    library (macros) {
      capacitive_load_unit (1, ff);
      cell (MACRO_RAM) {
        area : 100.0;
        pin (D) {
          direction : input;
          capacitance : 2.0;
        }
      }
    }
    """
)

# Three nets:
#   n_in  : chip INPUT port  -> i1/A          (port drives, cell sinks)
#   n_out : i1/ZN            -> chip OUTPUT port (cell drives, port sinks)
#   n_mac : i2/ZN            -> m1/D (macro)  (macro net -> connects_macro_flag)
MINI_DEF = textwrap.dedent(
    """
    VERSION 5.8 ;
    DESIGN mini ;
    UNITS DISTANCE MICRONS 1000 ;
    DIEAREA ( 0 0 ) ( 10000 10000 ) ;
    COMPONENTS 3 ;
    - i1 INV + PLACED ( 1000 1000 ) N ;
    - i2 INV + PLACED ( 2000 1000 ) N ;
    - m1 MACRO_RAM + PLACED ( 5000 5000 ) N ;
    END COMPONENTS
    PINS 2 ;
    - in_port + NET n_in + DIRECTION INPUT + USE SIGNAL
      + PLACED ( 0 500 ) N ;
    - out_port + NET n_out + DIRECTION OUTPUT + USE SIGNAL
      + PLACED ( 9999 500 ) N ;
    END PINS
    NETS 3 ;
    - n_in ( PIN in_port ) ( i1 A ) + USE SIGNAL ;
    - n_out ( i1 ZN ) ( PIN out_port ) + USE SIGNAL ;
    - n_mac ( i2 ZN ) ( m1 D ) + USE SIGNAL ;
    END NETS
    END DESIGN
    """
)


@pytest.fixture()
def mini_case(tmp_path, monkeypatch):
    std_lib = tmp_path / "std.lib"
    macro_lib = tmp_path / "macro.lib"
    def_file = tmp_path / "mini.def"
    std_lib.write_text(STD_LIB)
    macro_lib.write_text(MACRO_LIB)
    def_file.write_text(MINI_DEF)

    monkeypatch.setenv("R2G_LIB_FILES", f"{std_lib} {macro_lib}")
    monkeypatch.setenv("R2G_SC_LIB_FILES", str(std_lib))
    monkeypatch.setenv("R2G_PLATFORM", "testplat")
    for var in ("R2G_SDC", "R2G_SPEF", "R2G_CONFIG", "R2G_TECH_LEF", "R2G_DEF"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path, def_file


def _run_worker(monkeypatch, module, def_file, out_csv):
    monkeypatch.setattr(sys, "argv", [module.__name__, str(def_file), str(out_csv), "mini"])
    module.main()
    with open(out_csv, newline="") as f:
        return list(csv.DictReader(f))


# --- fix 1: DEF PIN direction is chip-perspective ---------------------------

def test_input_port_is_a_driver_output_port_is_a_sink(mini_case, monkeypatch):
    tmp_path, def_file = mini_case
    rows = {r["net_name"]: r for r in _run_worker(monkeypatch, nodes_net, def_file, tmp_path / "nodes_net.csv")}

    n_in = rows["n_in"]
    assert (n_in["num_drivers"], n_in["num_sinks"]) == ("1", "1"), (
        "chip INPUT port must count as the net's driver (was counted as a sink)"
    )
    n_out = rows["n_out"]
    assert (n_out["num_drivers"], n_out["num_sinks"]) == ("1", "1"), (
        "chip OUTPUT port must count as a sink (was counted as a 2nd driver)"
    )


# --- fix 2: connects_macro_flag from macro-lib-only masters ------------------

def test_connects_macro_flag_set_only_on_macro_nets(mini_case, monkeypatch):
    tmp_path, def_file = mini_case
    rows = {r["net_name"]: r for r in _run_worker(monkeypatch, nodes_net, def_file, tmp_path / "nodes_net.csv")}
    assert rows["n_mac"]["connects_macro_flag"] == "1"
    assert rows["n_in"]["connects_macro_flag"] == "0"
    assert rows["n_out"]["connects_macro_flag"] == "0"


def test_macro_cell_keys_empty_without_extra_libs(tmp_path):
    std_lib = tmp_path / "std.lib"
    std_lib.write_text(STD_LIB)
    assert macro_cell_keys([str(std_lib)], [str(std_lib)]) == set()


def test_macro_cell_keys_from_extra_lib(tmp_path):
    std_lib = tmp_path / "std.lib"
    macro_lib = tmp_path / "macro.lib"
    std_lib.write_text(STD_LIB)
    macro_lib.write_text(MACRO_LIB)
    keys = macro_cell_keys([str(std_lib), str(macro_lib)], [str(std_lib)])
    assert keys == {norm_cell_key("MACRO_RAM")}


# --- fix 3: net pin-cap sums exclude the driver's max_capacitance -----------

def test_get_pin_load_cap_excludes_output_max_capacitance(tmp_path):
    std_lib = tmp_path / "std.lib"
    std_lib.write_text(STD_LIB)
    db = load_liberty_db([str(std_lib)])
    assert get_pin_load_cap_fF("INV", "A", db) == pytest.approx(1.5)
    assert get_pin_load_cap_fF("INV", "ZN", db) == 0.0  # max_capacitance NOT a load


def test_sum_pin_cap_is_input_loads_only(mini_case, monkeypatch):
    tmp_path, def_file = mini_case
    rows = _run_worker(monkeypatch, nodes_pin, def_file, tmp_path / "nodes_pin.csv")
    by_pin = {(r["inst_name"], r["pin_name"]): r for r in rows}
    # n_mac's load = m1/D capacitance (2.0); before the fix i2/ZN's
    # max_capacitance (50.0) was added -> 52.0.
    assert float(by_pin[("m1", "D")]["sum_pin_cap_fF"]) == pytest.approx(2.0)
    assert float(by_pin[("i2", "ZN")]["sum_pin_cap_fF"]) == pytest.approx(2.0)
    # n_in's load = i1/A capacitance (1.5).
    assert float(by_pin[("i1", "A")]["sum_pin_cap_fF"]) == pytest.approx(1.5)
