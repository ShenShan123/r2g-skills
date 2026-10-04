"""Hierarchical clock inference must also follow POSITIONAL instance connections.

Regression (wave-3 E5L, source fe9bc8b0fd8a, top `Datapath`): the top only fans
`in_clk` out to 16 ProcessingElements and a MinTrackerComp, all connected
positionally (`ProcessingElements PE0(in_clk, in_rst, ...)`). fe64cff followed
named connections only, so promotion still refused a sequential design as
rejected_unconstrained_clock. A position is mapped to the child's DECLARED port
order; anything that makes that order uncertain (preprocessor directives in the
port list, more arguments than ports) is not guessed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "promote"))

from common.clock_infer import infer_clock_ports  # noqa: E402
import promote_candidates as pc  # noqa: E402

# Shape of the E5L ProcessingElements header: parameter block, ANSI ports, one
# declaration naming several ports.
PE = """
module ProcessingElements
  #(parameter DATA_WIDTH = 8,
              MAX_DATA_WIDTH = 16)
  (
  input in_clk,
  input in_rst,
  input in_sw_mux, // comment inside the port list
  input [DATA_WIDTH-1:0]in_sw_data1, in_sw_data2,
  output reg [MAX_DATA_WIDTH-1:0] out_SAD
  );
  always @(posedge in_clk or posedge in_rst) if (in_rst) out_SAD <= 0; else out_SAD <= in_sw_data1;
endmodule
"""

DATAPATH = """
module Datapath #(parameter W = 8) (
  input in_clk,
  input in_rst,
  input [15:0] in_sw_mux,
  input [W-1:0] in_a, in_b,
  output [15:0] out_sad
);
  ProcessingElements PE0(in_clk, in_rst, in_sw_mux[0], in_a, in_b, out_sad);
  ProcessingElements #(8, 16) PE1(in_clk, in_rst, in_sw_mux[1], {in_a[3:0], in_b[3:0]}, in_b, );
endmodule
"""


def test_positional_clock_is_mapped_by_declared_port_order() -> None:
    assert infer_clock_ports("Datapath", [DATAPATH, PE]) == ["in_clk"]


def test_non_ansi_child_header_is_mapped_too() -> None:
    child = """
module ff (d, ck, q);
  input d; input ck; output reg q;
  always @(posedge ck) q <= d;
endmodule
"""
    top = """
module t (input a, input sysclk, output y);
  ff u (a, sysclk, y);
endmodule
"""
    assert infer_clock_ports("t", [top, child]) == ["sysclk"]


def test_position_is_not_confused_with_a_non_clock_port() -> None:
    # sysclk sits in the child's DATA position: it must NOT be inferred.
    child = """
module ff (input ck, input d, output reg q);
  always @(posedge ck) q <= d;
endmodule
"""
    top = """
module t (input sysclk, input other, output y);
  ff u (other, sysclk, y);
endmodule
"""
    assert infer_clock_ports("t", [top, child]) == ["other"]


def test_preprocessed_port_list_is_not_guessed() -> None:
    child = """
module ff (
`ifdef WITH_EN
  input en,
`endif
  input ck, input d, output reg q);
  always @(posedge ck) q <= d;
endmodule
"""
    top = """
module t (input sysclk, input d, output y);
  ff u (sysclk, d, y);
endmodule
"""
    assert infer_clock_ports("t", [top, child]) == []


def test_more_arguments_than_ports_is_not_guessed() -> None:
    child = """
module ff (input ck, output reg q);
  always @(posedge ck) q <= 1'b1;
endmodule
"""
    top = """
module t (input sysclk, input d, output y);
  ff u (sysclk, d, y);
endmodule
"""
    assert infer_clock_ports("t", [top, child]) == []


E5L_RTL = Path("/data2/home/shenshan/r2g-work/corpora/e5l/d/fe9bc8b0fd8a/_downloads/"
               "e5l_fe9bc8b0fd8a/rtl")


@pytest.mark.skipif(not E5L_RTL.is_dir(), reason="E5L corpus not on this host")
def test_real_e5l_datapath_resolves_in_clk() -> None:
    files = [E5L_RTL / n for n in ("Datapath.v", "InstantMinComp.v",
                                   "MinTrackerComp.v", "ProcessingElements.v")]
    assert pc.detect_clock_port("Datapath", files) == "in_clk"
