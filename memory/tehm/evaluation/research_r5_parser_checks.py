"""R5 parameterized-header parser regression checks; no corpus writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tehm.rtl.verilog_parse import PARSE_VERSION, parse_verilog


def check(axis_path: Path) -> dict:
    axis = parse_verilog(axis_path.read_text(encoding="utf-8"))
    simple = parse_verilog(
        "module foo(input wire clk, output reg q); "
        "always @(posedge clk) begin q <= clk; end endmodule")
    parameterized = parse_verilog(
        "module bar #(parameter W = (2*(3+1)), parameter MODE=2) "
        "(input wire clk, output wire [W-1:0] q); endmodule")
    multiple = parse_verilog("module a; endmodule module b; endmodule")
    cases = {
        "actual_axis_one_module": len(axis) == 1 and axis[0].name == "axis_register",
        "actual_axis_header_parameters": len(axis) == 1 and
            axis[0].params.get("DATA_WIDTH") == "8" and
            axis[0].params.get("REG_TYPE") == "2" and
            axis[0].params.get("KEEP_WIDTH") == "((DATA_WIDTH+7)/8)",
        "actual_axis_ports_and_blocks": len(axis) == 1 and
            "s_axis_tdata" in axis[0].signals and
            "m_axis_tdata" in axis[0].signals and
            len(axis[0].always_blocks) == 2,
        "simple_module_retained": len(simple) == 1 and
            simple[0].name == "foo" and len(simple[0].always_blocks) == 1,
        "balanced_nested_parameter": len(parameterized) == 1 and
            parameterized[0].params.get("W") == "(2*(3+1))" and
            parameterized[0].params.get("MODE") == "2",
        "multiple_modules_retained": [m.name for m in multiple] == ["a", "b"],
        "unbalanced_parameter_rejected": not parse_verilog(
            "module bad #(parameter W=(2+3) (input wire clk); endmodule"),
        "header_macro_rejected": not parse_verilog(
            "module bad #(`WIDTH) (input wire clk); endmodule"),
        "header_string_rejected": not parse_verilog(
            'module bad #(parameter NAME="foo") (input wire clk); endmodule'),
        "comment_decoy_ignored": [m.name for m in parse_verilog(
            "// module fake; endmodule\nmodule real; endmodule")] == ["real"],
    }
    return {"parser_version": PARSE_VERSION, "valid": all(cases.values()),
            "case_count": len(cases), "cases": cases,
            "failed": sorted(k for k, ok in cases.items() if not ok)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--axis", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.axis)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
