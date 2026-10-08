"""VHDL goes through GHDL (with a -fsynopsys retry) before vhd2vl.

Regression (wave-3 E9, CORRECTIONS #9): the only VHDL path was vhd2vl, which
cannot parse `bit`-typed VHDL, so all 22 ITC'99 designs failed qualification
although the installed Yosys has the GHDL plugin (`yosys -m ghdl` synthesised
20/22 read-only; 3 of them need -fsynopsys). The pass rate measured the
registered tool set, not the corpus.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from common import frontend_capability as fc  # noqa: E402
from execute import expand_candidates as xc  # noqa: E402

# ITC'99 b04 style: std_logic_arith is a Synopsys package, rejected without -fsynopsys.
SYNOPSYS_VHDL = """\
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.std_logic_arith.all;
entity tiny is
  port (clock : in std_logic; d : in std_logic; q : out std_logic);
end tiny;
architecture rtl of tiny is
begin
  process (clock) begin
    if rising_edge(clock) then q <= d; end if;
  end process;
end rtl;
"""


@pytest.fixture(autouse=True)
def _fresh_probe_cache() -> None:
    fc._cache.clear()
    fc._record.clear()
    yield
    fc._cache.clear()
    fc._record.clear()


def _fake_yosys(tmp_path: Path, needs_synopsys: bool) -> tuple[Path, Path]:
    """A yosys stand-in: loads 'ghdl', and writes Verilog only if the flags suffice."""
    calls = tmp_path / "calls.txt"
    exe = tmp_path / "yosys"
    need = "1" if needs_synopsys else "0"
    exe.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        'case "$*" in *"help ghdl"*) exit 0;; esac\n'
        'script=""; while [ $# -gt 0 ]; do [ "$1" = -p ] && script="$2"; shift; done\n'
        f'if [ "{need}" = 1 ]; then case "$script" in *-fsynopsys*) ;; *) exit 1;; esac; fi\n'
        'out="${script##*write_verilog -noattr }"\n'
        'echo "module tiny(); endmodule" > "$out"\n', encoding="utf-8")
    exe.chmod(0o755)
    return exe, calls


def test_ghdl_is_a_registered_frontend_with_a_real_canary(tmp_path: Path,
                                                          monkeypatch) -> None:
    exe, calls = _fake_yosys(tmp_path, needs_synopsys=False)
    monkeypatch.setenv("YOSYS_EXE", str(exe))
    assert fc.frontend_available("ghdl")
    assert "-m ghdl -p help ghdl" in calls.read_text(encoding="utf-8")


def test_synopsys_vhdl_falls_back_to_fsynopsys(tmp_path: Path, monkeypatch) -> None:
    exe, calls = _fake_yosys(tmp_path, needs_synopsys=True)
    monkeypatch.setenv("YOSYS_EXE", str(exe))
    vhd = tmp_path / "tiny.vhd"
    vhd.write_text(SYNOPSYS_VHDL, encoding="utf-8")

    out, argv = xc.run_ghdl(tmp_path / "out", "tiny", [vhd], "tiny")

    assert out is not None and out.is_file()
    assert argv == ["-fsynopsys", str(vhd), "-e", "tiny"]
    ghdl_calls = [c for c in calls.read_text(encoding="utf-8").splitlines()
                  if "write_verilog" in c]
    assert len(ghdl_calls) == 2  # plain first, then -fsynopsys


def test_missing_ghdl_plugin_leaves_vhdl_to_vhd2vl(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "yosys"
    exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("YOSYS_EXE", str(exe))
    vhd = tmp_path / "tiny.vhd"
    vhd.write_text(SYNOPSYS_VHDL, encoding="utf-8")
    assert xc.run_ghdl(tmp_path / "out", "tiny", [vhd], "tiny") == (None, [])


def test_verilog_only_bundle_never_calls_ghdl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(xc, "frontend_available", lambda name: pytest.fail("probed"))
    v = tmp_path / "t.v"
    v.write_text("module t; endmodule\n", encoding="utf-8")
    assert xc.run_ghdl(tmp_path / "out", "t", [v], "t") == (None, [])


def test_ghdl_lineage_is_recorded_for_promotion(tmp_path: Path) -> None:
    vhd = tmp_path / "tiny.vhd"
    vhd.write_text(SYNOPSYS_VHDL, encoding="utf-8")
    conv = tmp_path / "tiny_ghdl.v"
    conv.write_text("module tiny(); endmodule\n", encoding="utf-8")
    man = xc._transformation_manifest([vhd], [conv], "ghdl", [], "tiny",
                                      ghdl_argv=["-fsynopsys", str(vhd), "-e", "tiny"])
    assert man["required"] and man["kind"] == "ghdl"
    assert man["argv"] == ["-m", "ghdl", "-fsynopsys", str(vhd), "-e", "tiny"]


def _real_ghdl_yosys() -> str | None:
    exe = shutil.which("yosys")
    if not exe:
        return None
    res = subprocess.run([exe, "-m", "ghdl", "-p", "help ghdl"],
                         capture_output=True, text=True, check=False)
    return exe if res.returncode == 0 else None


@pytest.mark.skipif(_real_ghdl_yosys() is None, reason="no yosys with the ghdl plugin")
def test_real_ghdl_converts_synopsys_vhdl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YOSYS_EXE", _real_ghdl_yosys() or "")
    vhd = tmp_path / "tiny.vhd"
    vhd.write_text(SYNOPSYS_VHDL, encoding="utf-8")
    out, argv = xc.run_ghdl(tmp_path / "out", "tiny", [vhd], "tiny")
    assert out is not None and argv[0] == "-fsynopsys"
    assert "module tiny" in out.read_text(encoding="utf-8")


def test_ghdl_is_never_written_as_synth_hdl_frontend(tmp_path: Path, monkeypatch) -> None:
    # Review finding (2026-09-23): with its canary registered, select_frontend
    # returned "ghdl" for a row asking for it, and write_project would emit
    # `export SYNTH_HDL_FRONTEND = ghdl`, a value ORFS does not have.
    exe, _calls = _fake_yosys(tmp_path, needs_synopsys=False)
    monkeypatch.setenv("YOSYS_EXE", str(exe))
    chosen, rec = fc.select_frontend("ghdl", needs_sv=False)
    assert chosen is None
    assert rec["available"]          # not reported as a missing tool either


def test_failed_ghdl_synthesis_leaves_the_vhdl_sources_on_record(tmp_path: Path,
                                                                 monkeypatch) -> None:
    # Review finding (2026-09-23): when GHDL converted but its Verilog then failed
    # synthesis, and vhd2vl could not convert either, the synth_failed record named
    # the GHDL output as the design's RTL with ghdl_fallback_used=False.
    import csv
    import json

    vhd = tmp_path / "src" / "tiny.vhd"
    vhd.parent.mkdir()
    vhd.write_text("entity tiny is end tiny;\narchitecture a of tiny is begin end a;\n")
    cand = tmp_path / "cands.csv"
    with cand.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["design", "priority", "expected_top",
                                           "source_path", "rtl_files", "notes"])
        w.writeheader()
        w.writerow({"design": "tiny", "priority": "high", "expected_top": "tiny",
                    "source_path": str(vhd), "rtl_files": str(vhd), "notes": ""})
    ghdl_v = tmp_path / "tiny_ghdl.v"
    ghdl_v.write_text("module tiny(); endmodule\n")
    monkeypatch.setattr(xc, "synthesize", lambda project, design, top: (1, None, None))
    monkeypatch.setattr(xc, "summarize_synth_failure", lambda _p: "synth error")
    monkeypatch.setattr(xc, "run_ghdl", lambda *a, **k: (ghdl_v, ["-fsynopsys"]))
    monkeypatch.setattr(xc, "run_vhd2vl", lambda *a, **k: None)
    monkeypatch.setenv("R2G_ACQUIRE_SKIP_INGEST", "1")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["expand_candidates.py", "--candidate-csv", str(cand),
                                      "--out-root", str(out),
                                      "--projects-root", str(tmp_path / "proj")])
    xc.main()

    meta = json.loads((out / "tiny" / "design_meta.json").read_text())
    assert meta["status"] == "synth_failed"
    assert meta["rtl_files"] == [str(vhd)]
    assert "ghdl_synth_failed" in meta["notes"]
    assert (out / "tiny" / "src_manifest.txt").read_text().split() == [str(vhd)]
    config = next((tmp_path / "proj").rglob("config.mk")).read_text()
    assert str(ghdl_v) not in config
