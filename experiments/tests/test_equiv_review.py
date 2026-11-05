"""Equivalence review: source lists with spaces in their paths (no model, Yosys optional)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bare_agent"))
import equiv_review as er  # noqa: E402

# both versions in use: 208's oss-cad build and the ORFS-pinned build 203/216 review with
YOSYS = [y for y in (shutil.which("yosys"), "/opt/OpenROAD/oss-cad-suite/bin/yosys",
                     "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys")
         if y and Path(y).exists()]


def test_paths_rejoins_names_that_contain_spaces(tmp_path):
    d = tmp_path / "src" / "RSIC-V core"
    d.mkdir(parents=True)
    (d / "Extend.v").write_text("module x; endmodule\n")
    (tmp_path / "top.v").write_text("module top; endmodule\n")
    spaced = str(d / "Extend.v")
    assert er.paths("%s %s" % (spaced, tmp_path / "top.v")) == [spaced, str(tmp_path / "top.v")]
    assert er.paths(spaced.replace(" ", "\\ ")) == [spaced]                 # make-escaped space
    assert er.paths("/no/such/a.v") == ["/no/such/a.v"]                       # kept, reported by Yosys


def test_nospace_maps_spaced_paths_to_symlinks(tmp_path):
    d = tmp_path / "inc dir"; d.mkdir()
    (d / "a.v").write_text("module a; endmodule\n")
    cfg = er.nospace({"top": "a", "files": [str(d / "a.v")], "incdirs": [str(d)], "defines": []}, tmp_path)
    assert all(" " not in p for p in cfg["files"] + cfg["incdirs"])
    assert Path(cfg["files"][0]).resolve() == (d / "a.v").resolve() and Path(cfg["incdirs"][0]).resolve() == d.resolve()


@pytest.mark.skipif(not YOSYS, reason="needs yosys")
@pytest.mark.parametrize("yosys", YOSYS)
def test_a_spaced_design_with_a_spaced_include_dir_is_proven(tmp_path, yosys):
    src = tmp_path / "src dir"; inc = tmp_path / "inc dir"
    src.mkdir(); inc.mkdir()
    (inc / "defs.vh").write_text("`define AND(x, y) ((x) & (y))\n")
    (src / "top.v").write_text('`include "defs.vh"\nmodule top(input a, b, output y); assign y = `AND(a, b); endmodule\n')
    (tmp_path / "gate.v").write_text("module top(input a, b, output y); assign y = ~(~a | ~b); endmodule\n")
    gold = er.nospace({"top": "top", "files": [str(src / "top.v")], "incdirs": [str(inc)], "defines": []}, tmp_path)
    gate = er.nospace({"top": "top", "files": [str(tmp_path / "gate.v")], "incdirs": [], "defines": []}, tmp_path)
    ys = tmp_path / "e.ys"
    ys.write_text(er.script(gold, gate))
    p = subprocess.run([yosys, "-q", "-l", str(tmp_path / "y.log"), "-s", str(ys)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "unproven" in (tmp_path / "y.log").read_text() and "are unproven" in (tmp_path / "y.log").read_text()
    assert " 0 are unproven" in (tmp_path / "y.log").read_text()
