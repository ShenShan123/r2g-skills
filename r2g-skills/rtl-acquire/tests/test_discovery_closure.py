"""Discovery blind spots + macro-indirect closure (held-out V3 frontend cohorts).

Two independent findings, both about what autonomous discovery FAILS TO SEE:

  * frontend development cohort: `src_v` was not a preferred RTL directory, and
    ANY `$display` rejected a file as a testbench even when the call sits inside a
    synthesis-safe guard (`` `ifdef DEBUG ``, `// synopsys translate_off`). Real
    cores do this constantly — picorv32's debug tracing is exactly this shape.
  * reserved validation cohort, P1-FE-VAL-01: ZipCPU's `zipcore` instantiates its
    implementations through preprocessor macros (`` `DIVIDE_MODULE ``, `` `MPYOP ``)
    with the defines in a companion header, so autonomous closure omitted div.v,
    mpyop.v and slowmpy.v. Controlled synthesis only succeeded because the
    authoritative file list was supplied by hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "repair"))

from acquire.discover_download_candidates import (  # noqa: E402
    PREFERRED_DIR_PARTS,
    collect_defines,
    extract_macro_instantiations,
    file_is_candidate,
    path_is_likely_rtl_source,
    resolve_macro,
    resolve_macro_refs,
    strip_synthesis_off_regions,
    unguarded_testbench_marker,
)
from classify_failed_candidates import classify  # noqa: E402

BODY = "\n".join(f"  wire w{i} = {i};" for i in range(30))


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- discovery blind spot 1: source-layout recognition -----------------------

def test_src_v_is_a_preferred_rtl_directory():
    assert "src_v" in PREFERRED_DIR_PARTS


def test_a_deep_src_v_file_is_reachable(tmp_path):
    root = tmp_path / "downloads"
    p = _write(root, "myrepo/hw/src_v/core.v", "module core(); endmodule\n")
    assert path_is_likely_rtl_source(root, p)


def test_a_deep_test_directory_is_still_skipped(tmp_path):
    root = tmp_path / "downloads"
    p = _write(root, "myrepo/hw/tests/core_tb.v", "module core_tb(); endmodule\n")
    assert not path_is_likely_rtl_source(root, p)


# --- discovery blind spot 2: guarded simulation tasks ------------------------

def test_guarded_display_does_not_reject_the_file(tmp_path):
    text = ("module core(input clk);\n" + BODY + "\n"
            "`ifdef DEBUG\n"
            "  always @(posedge clk) $display(\"trace %d\", w0);\n"
            "`endif\n"
            "endmodule\n")
    assert not unguarded_testbench_marker(text)
    ok, reason = file_is_candidate(_write(tmp_path, "core.v", text))
    assert ok, reason


def test_translate_off_guarded_display_does_not_reject(tmp_path):
    text = ("module core(input clk);\n" + BODY + "\n"
            "// synopsys translate_off\n"
            "  always @(posedge clk) $display(\"trace\");\n"
            "// synopsys translate_on\n"
            "endmodule\n")
    assert not unguarded_testbench_marker(text)


def test_ifndef_synthesis_guard_is_honoured():
    text = ("module core(input clk);\n"
            "`ifndef SYNTHESIS\n  initial $dumpvars(0, core);\n`endif\n"
            "endmodule\n")
    assert not unguarded_testbench_marker(text)


def test_an_unguarded_display_still_rejects(tmp_path):
    text = ("module core(input clk);\n" + BODY + "\n"
            "  always @(posedge clk) $display(\"trace\");\n"
            "endmodule\n")
    assert unguarded_testbench_marker(text)
    ok, reason = file_is_candidate(_write(tmp_path, "core.v", text))
    assert not ok and reason == "testbench_marker"


def test_nested_ifdef_does_not_close_the_guard_early():
    text = ("module core(input clk);\n"
            "`ifdef DEBUG\n"
            "  `ifdef VERBOSE\n    initial $display(\"a\");\n  `endif\n"
            "  initial $display(\"b\");\n"
            "`endif\n"
            "endmodule\n")
    assert not unguarded_testbench_marker(text)
    assert "$display" not in strip_synthesis_off_regions(text)


def test_code_outside_the_guard_survives_the_strip():
    text = ("module core();\n`ifdef DEBUG\n initial $display(\"x\");\n`endif\n"
            "  wire keep_me = 1;\nendmodule\n")
    stripped = strip_synthesis_off_regions(text)
    assert "keep_me" in stripped and "endmodule" in stripped


# --- macro-indirect closure (P1-FE-VAL-01) -----------------------------------

ZIPCORE = """
module zipcore(input i_clk);
`ifdef OPT_DIVIDE
	`DIVIDE_MODULE thedivide(i_clk, 1'b0, 1'b0);
`endif
	`MPYOP thempy(i_clk, 1'b0);
endmodule
"""
CPUDEFS = "`define\tDIVIDE_MODULE\tdiv\n`define\tMPYOP\tmpyop\n"


def _zip_repo(tmp_path: Path) -> tuple[dict, dict]:
    _write(tmp_path, "rtl/core/zipcore.v", ZIPCORE)
    _write(tmp_path, "rtl/cpudefs.vh", CPUDEFS)
    _write(tmp_path, "rtl/core/div.v", "module div(input i_clk, a, b); endmodule\n")
    _write(tmp_path, "rtl/core/mpyop.v", "module mpyop(input i_clk, a); endmodule\n")
    defines = collect_defines(list(tmp_path.rglob("*.v")) + list(tmp_path.rglob("*.vh")))
    module_to_path = {
        "zipcore": tmp_path / "rtl/core/zipcore.v",
        "div": tmp_path / "rtl/core/div.v",
        "mpyop": tmp_path / "rtl/core/mpyop.v",
    }
    return defines, module_to_path


def test_defines_are_collected_from_headers(tmp_path):
    defines, _ = _zip_repo(tmp_path)
    assert defines["DIVIDE_MODULE"] == "div"
    assert defines["MPYOP"] == "mpyop"


def test_macro_instantiations_are_detected():
    assert extract_macro_instantiations(ZIPCORE) == {"DIVIDE_MODULE", "MPYOP"}


def test_preprocessor_directives_are_not_mistaken_for_modules():
    text = "`ifdef FOO\n`include \"x.vh\"\nassign y = 1;\n`endif\n"
    assert extract_macro_instantiations(text) == set()


def test_macro_refs_resolve_to_real_local_dependencies(tmp_path):
    defines, module_to_path = _zip_repo(tmp_path)
    resolved, unresolved = resolve_macro_refs(
        extract_macro_instantiations(ZIPCORE), defines, module_to_path)
    assert resolved == {"div", "mpyop"}
    assert unresolved == []


def test_a_defined_macro_with_no_local_module_is_an_honest_gap(tmp_path):
    defines, module_to_path = _zip_repo(tmp_path)
    del module_to_path["div"]
    resolved, unresolved = resolve_macro_refs(
        extract_macro_instantiations(ZIPCORE), defines, module_to_path)
    assert resolved == {"mpyop"}
    assert unresolved == ["DIVIDE_MODULE"]


def test_an_undefined_macro_is_ignored_not_reported(tmp_path):
    """A vendor/assertion macro the repo never defines must not flood every
    bundle with false closure gaps."""
    defines, module_to_path = _zip_repo(tmp_path)
    resolved, unresolved = resolve_macro_refs({"SOME_VENDOR_MACRO"}, defines, module_to_path)
    assert resolved == set() and unresolved == []


def test_chained_defines_resolve():
    defines = {"A": "`B", "B": "`C", "C": "real_module"}
    assert resolve_macro("A", defines) == "real_module"


def test_a_define_cycle_terminates():
    assert resolve_macro("A", {"A": "`B", "B": "`A"}) is None


def test_closure_incomplete_failure_is_a_retry_not_an_exclusion():
    note = ("closure_incomplete=macro; unresolved_macros=DIVIDE_MODULE | "
            "ERROR: module `div' not found")
    assert classify("/src/zipcore.v", note) == ("retry", "closure_incomplete")
