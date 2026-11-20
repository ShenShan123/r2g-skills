"""Regression for delta D17 (2026-11-07): a top-level `input [0:0] x;` is the bit `x[0]`.

Yosys JSON cannot distinguish `[0:0]` from a scalar, so the base graph named the port `x`
while OpenROAD/OpenSTA call it `x[0]` (pilot500 C02816 `i_register_ready`): NaN IO position
at every stage and a four-stage build aborted by the timing path-alignment gate.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

R2G2 = Path(__file__).resolve().parents[1] / "scripts" / "r2g2"


@pytest.fixture(scope="module")
def stage01():
    pytest.importorskip("torch")
    spec = importlib.util.spec_from_file_location("t_d17_base", R2G2 / "01_build_base_graph.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t_d17_base"] = mod
    spec.loader.exec_module(mod)
    return mod


NETLIST = """
module sub(a, y); input [0:0] a; output y; endmodule
module top(clk, rdy, bus, q);
  input clk;
  input [0:0] rdy;   // one-bit vector
  input [3:0] bus;
  output q;
  wire [0:0] w0;
endmodule
"""


def test_single_bit_vectors_are_found_in_the_top_module_only(stage01):
    names = stage01.single_bit_vector_names(NETLIST, "top")
    assert names == frozenset({"rdy", "w0"})          # not 'a' (sub), not scalars / wide buses


def test_one_bit_vector_is_named_with_its_index(stage01):
    vectors = frozenset({"rdy"})
    assert stage01.yosys_signal_names("rdy", {"bits": [7], "offset": 0}, vectors) == ["rdy[0]"]
    assert stage01.yosys_signal_names("clk", {"bits": [2], "offset": 0}, vectors) == ["clk"]
    assert stage01.yosys_signal_names("bus", {"bits": [3, 4], "offset": 0}) == ["bus[0]", "bus[1]"]
