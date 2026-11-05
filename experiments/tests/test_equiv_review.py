"""Equivalence review: source lists with spaces in their paths (no model, Yosys optional)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bare_agent"))
import equiv_review as er  # noqa: E402

YOSYS = shutil.which("yosys") or "/opt/OpenROAD/oss-cad-suite/bin/yosys"


def test_paths_rejoins_names_that_contain_spaces(tmp_path):
    d = tmp_path / "src" / "RSIC-V core"
    d.mkdir(parents=True)
    (d / "Extend.v").write_text("module x; endmodule\n")
    (tmp_path / "top.v").write_text("module top; endmodule\n")
    spaced = str(d / "Extend.v")
    assert er.paths("%s %s" % (spaced, tmp_path / "top.v")) == [spaced, str(tmp_path / "top.v")]
    assert er.paths(spaced.replace(" ", "\\ ")) == [spaced]                 # make-escaped space
    assert er.paths("/no/such/a.v") == ["/no/such/a.v"]                       # kept, reported by Yosys
    assert er.q(spaced) == '"%s"' % spaced


@pytest.mark.skipif(not Path(YOSYS).exists(), reason="needs yosys")
def test_a_spaced_source_is_read_and_proven_equivalent(tmp_path):
    d = tmp_path / "rtl dir"
    d.mkdir()
    (d / "top.v").write_text("module top(input a, b, output y); assign y = a & b; endmodule\n")
    (tmp_path / "gate.v").write_text("module top(input a, b, output y); assign y = ~(~a | ~b); endmodule\n")
    gold = {"top": "top", "files": [str(d / "top.v")], "incdirs": [str(d)], "defines": []}
    gate = {"top": "top", "files": [str(tmp_path / "gate.v")], "incdirs": [], "defines": []}
    ys = tmp_path / "e.ys"
    ys.write_text(er.script(gold, gate))
    p = subprocess.run([YOSYS, "-q", "-s", str(ys)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
