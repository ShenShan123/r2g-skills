"""A module named like a keyword in another case (`Reg`) must join the closure.

Regression (wave-3 E5L, source b52bba95da51): `riscv_top` instantiates
`Reg IR_DReg(...)`, defined in the same directory. extract_instantiated_modules
dropped the reference because it compared `token.lower()` against the keyword
set, and "reg" is a keyword. Verilog keywords are case-sensitive, so `Reg` is an
ordinary module name. The bundle shipped without Reg.v and synthesis failed as a
compile-closure error ("Module `\\Reg' ... is not part of the design").
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "acquire"))

import discover_download_candidates as dd  # noqa: E402

TOP = """
module riscv_top(input clk, input rst_n, input [31:0] instr, output [31:0] instr_D);
   Reg        IR_DReg(clk, rst_n, 1'b0, instr, instr_D);
   Wire #(8)  w0(clk);
   reg [3:0] r;
   wire [3:0] x;
   always @(posedge clk) r <= x;
endmodule
"""


def test_case_variant_of_a_keyword_is_an_instantiation() -> None:
    refs = dd.extract_instantiated_modules(TOP)
    assert {"Reg", "Wire"} <= refs
    assert not refs & {"reg", "wire", "always"}


def test_keyword_named_module_joins_the_bundle(tmp_path: Path) -> None:
    # The real discovery entry point: the defect lived in the ref extraction
    # that builds the symbol table, not in bundle_closure.
    rtl = tmp_path / "downloads" / "repo_rv"
    rtl.mkdir(parents=True)
    pad = "\n".join(f"   wire [31:0] p{i} = instr + {i};" for i in range(40))
    (rtl / "riscv_top.v").write_text(TOP.replace("endmodule", pad + "\nendmodule"),
                                     encoding="utf-8")
    (rtl / "Reg.v").write_text(
        "module Reg(input clk, rst, den, input [31:0] d, output reg [31:0] q);\n"
        "  always @(posedge clk) q <= d;\nendmodule\n", encoding="utf-8")
    out_csv = tmp_path / "candidates.csv"
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONPATH"):
        env.pop(name, None)
    env.update({
        "R2G_ACQUIRE_ROOT": str(tmp_path / "acq"),
        "R2G_ACQUIRE_WORKSPACE": str(tmp_path / "workspace"),
        "R2G_ACQUIRE_OUT": str(tmp_path / "external"),
        "R2G_ACQUIRE_SEED_ROOT": str(tmp_path / "orfs"),
    })
    subprocess.run(
        [sys.executable, str(SCRIPTS / "acquire" / "discover_download_candidates.py"),
         "--downloads-root", str(tmp_path / "downloads"), "--out-csv", str(out_csv),
         "--scan-state-json", str(tmp_path / "state.json"), "--no-sync-upstream"],
        env=env, check=True, capture_output=True, text=True)
    rows = list(csv.DictReader(out_csv.open(encoding="utf-8", newline="")))
    top_rows = [r for r in rows if r["expected_top"] == "riscv_top"]
    assert len(top_rows) == 1
    assert str(rtl / "Reg.v") in top_rows[0]["rtl_files"].split(";")


def test_macro_instantiation_path_is_case_sensitive_too() -> None:
    # Review finding (2026-09-23): the macro-instantiation path still compared
    # inst.lower(), so "`MOD Reg (...)" was dropped while "MOD Reg (...)" was kept.
    assert dd.extract_macro_instantiations("`MOD Reg (.a(b));\n") == {"MOD"}
    assert dd.extract_macro_instantiations("`MOD reg (.a(b));\n") == set()
