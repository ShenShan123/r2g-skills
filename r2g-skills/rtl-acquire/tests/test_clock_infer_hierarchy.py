"""Clock inference must follow the hierarchy when the top only fans the clock out.

Regression (wave-3 E5L, 2026-09-23): 6 of 14 synthesis-qualified sources were
rejected at promotion as `rejected_unconstrained_clock` because inference only
read the TOP body's edge events. A wrapper (e.g. an AXI top) that passes its
clock to a core has none, so a real sequential design was refused as unclocked.
Ambiguity must still be refused: two independent clocks stay unresolved.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "promote"))

from common.clock_infer import infer_clock_ports  # noqa: E402
import promote_candidates as pc  # noqa: E402

CORE = """
module axis_core (input wire clk, input wire rst_n, input wire [7:0] d, output reg [7:0] q);
  always @(posedge clk or negedge rst_n) if (!rst_n) q <= 0; else q <= d;
endmodule
"""

WRAPPER = """
module axi_wrap #(parameter W = 8) (
  input  wire         s_axi_aclk,
  input  wire         s_axi_aresetn,
  input  wire [W-1:0] s_axi_wdata,
  output wire [W-1:0] s_axi_rdata
);
  axis_core #(.W(W)) u_core (
    .clk   (s_axi_aclk),
    .rst_n (s_axi_aresetn),
    .d     (s_axi_wdata[7:0]),
    .q     (s_axi_rdata[7:0])
  );
endmodule
"""


def test_clock_fanned_only_to_a_submodule_is_found() -> None:
    assert infer_clock_ports("axi_wrap", [WRAPPER, CORE]) == ["s_axi_aclk"]


def test_clock_is_followed_through_two_levels() -> None:
    mid = """
module mid (input logic ck, input logic [7:0] a, output logic [7:0] b);
  axis_core core_i (.clk(ck), .rst_n(1'b1), .d(a), .q(b));
endmodule
"""
    top = """
module soc_top (input logic sys_clk, input logic [7:0] x, output logic [7:0] y);
  mid m (.ck(sys_clk), .a(x), .b(y));
endmodule
"""
    assert infer_clock_ports("soc_top", [top, mid, CORE]) == ["sys_clk"]


def test_sv_implicit_named_connection_is_followed() -> None:
    top = """
module top_impl (input logic clk, input logic rst_n, input logic [7:0] d, output logic [7:0] q);
  axis_core u (.clk, .rst_n, .d, .q);
endmodule
"""
    assert infer_clock_ports("top_impl", [top, CORE]) == ["clk"]


def test_two_independent_clocks_stay_ambiguous(tmp_path: Path) -> None:
    top = """
module dual (input wire aclk, input wire bclk, input wire [7:0] d, output wire [7:0] q1, output wire [7:0] q2);
  axis_core a (.clk(aclk), .rst_n(1'b1), .d(d), .q(q1));
  axis_core b (.clk(bclk), .rst_n(1'b1), .d(d), .q(q2));
endmodule
"""
    assert sorted(infer_clock_ports("dual", [top, CORE])) == ["aclk", "bclk"]
    rtl = tmp_path / "dual.v"
    rtl.write_text(top + CORE, encoding="utf-8")
    # promotion refuses to pick one: still needs an explicit --clock-port
    assert pc.detect_clock_port("dual", [rtl]) == ""


def test_promotion_resolves_the_wrapper_clock(tmp_path: Path) -> None:
    rtl = tmp_path / "axi_wrap.v"
    rtl.write_text(WRAPPER + CORE, encoding="utf-8")
    assert pc.detect_clock_port("axi_wrap", [rtl]) == "s_axi_aclk"


def test_top_body_edge_events_still_take_precedence() -> None:
    top = """
module mixed (input wire Clk, input wire sub_clk, input wire [7:0] d, output reg [7:0] q, output wire [7:0] r);
  always @(posedge Clk) q <= d;
  axis_core u (.clk(sub_clk), .rst_n(1'b1), .d(d), .q(r));
endmodule
"""
    assert infer_clock_ports("mixed", [top, CORE]) == ["Clk"]
