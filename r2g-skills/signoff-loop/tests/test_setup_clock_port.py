"""tools/setup_rtl_designs.detect_clock_port must name a TOP-module input (failure-patterns.md
"Unconstrained Timing (Silent Clock Mismatch)": a submodule's posedge `clk` gave i2c_master_top an
SDC on a non-existent port, so every flow ran unconstrained)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import setup_rtl_designs as S  # noqa: E402

TOP = """module i2c_top(wb_clk_i, wb_rst_i, dat_o);
  input wb_clk_i;   // master clock
  input wb_rst_i;
  output dat_o;
  byte_ctrl u0(.clk(wb_clk_i), .rst(wb_rst_i));
  always @(posedge wb_clk_i) ;
endmodule
"""
SUB = """module byte_ctrl(clk, rst);
  input clk;
  input rst;
  always @(posedge clk or negedge rst) ;
endmodule
"""


def test_submodule_clock_name_is_not_taken(tmp_path):
    (tmp_path / "byte_ctrl.v").write_text(SUB)
    (tmp_path / "i2c_top.v").write_text(TOP)
    files = sorted(tmp_path.glob("*.v"))          # submodule file first, as in the i2c corpus
    assert S.detect_clock_port(files, "i2c_top") == "wb_clk_i"


def test_ansi_top_ports_and_plain_clk(tmp_path):
    (tmp_path / "t.v").write_text("module t(input wire clk, input [3:0] a, output y);\n"
                                  "always @(posedge clk) ;\nendmodule\n")
    assert S._top_input_ports((tmp_path / "t.v").read_text(), "t") == {"clk", "a"}
    assert S.detect_clock_port([tmp_path / "t.v"], "t") == "clk"
