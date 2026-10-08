"""Timing labels must be timed under the sign-off SDC, not a re-created clk port.

Regression (E12-FP, 2026-09-23): extract_timing.tcl never read the design SDC. It
created a clock only on a port named like clk/clock and applied no I/O delays, so
purely combinational designs whose SDC declares a virtual clock (floating_point_adder
4943dea05dcb, ConvKernel 5f8dc547cda0) got every cell INF / label 0 / not in path,
while sign-off STA had finite slack on all of them. The verifier rejected both
datasets, correctly.

Two layers are pinned here:
  * run_labels.sh hands the bound run's 6_final.sdc to the extractor
    (R2G_TIMING_SDC), OpenROAD stubbed so only the orchestration is exercised;
  * extract_timing.tcl, run by the real OpenROAD on a one-gate combinational
    sky130hd layout, turns a virtual-clock SDC into finite slack whose
    Path_Delay uses the SDC's own period (skips when the tools are absent).
"""
from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parents[1]
_FLOW = _SKILL / "scripts" / "flow"
RUN_LABELS = _FLOW / "run_labels.sh"
EXTRACT_TIMING = _SKILL / "scripts" / "extract" / "labels" / "extract_timing.tcl"

_ONE_GATE_DEF = """\
VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN comb ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 20000 20000 ) ;
COMPONENTS 1 ;
 - u1 sky130_fd_sc_hd__nand2_1 + PLACED ( 5520 5440 ) N ;
END COMPONENTS
PINS 3 ;
 - a + NET a + DIRECTION INPUT + USE SIGNAL ;
 - b + NET b + DIRECTION INPUT + USE SIGNAL ;
 - y + NET y + DIRECTION OUTPUT + USE SIGNAL ;
END PINS
NETS 3 ;
 - a ( PIN a ) ( u1 A ) + USE SIGNAL ;
 - b ( PIN b ) ( u1 B ) + USE SIGNAL ;
 - y ( PIN y ) ( u1 Y ) + USE SIGNAL ;
END NETS
END DESIGN
"""

# The shape ORFS writes for a combinational design: a virtual clock plus I/O delays.
_VIRTUAL_CLOCK_SDC = """\
create_clock -name virtual_clk -period 5.0
set_input_delay 1.0 -clock virtual_clk [all_inputs]
set_output_delay 1.0 -clock virtual_clk [all_outputs]
"""


def _discovered_tools() -> dict[str, str]:
    """ORFS_ROOT / OPENROAD_EXE the way the flow scripts resolve them (_env.sh)."""
    out = subprocess.run(
        ["bash", "-c", f'source "{_FLOW}/_env.sh" >/dev/null 2>&1; '
                       'printf "%s\\n%s\\n" "${ORFS_ROOT:-}" "${OPENROAD_EXE:-}"'],
        capture_output=True, text=True, timeout=60).stdout.splitlines()
    return {"ORFS_ROOT": out[0] if out else "", "OPENROAD_EXE": out[1] if len(out) > 1 else ""}


def _stub_openroad(tmp_path: Path) -> tuple[Path, Path]:
    """An 'openroad' that records the SDC it was handed and exits 0."""
    seen = tmp_path / "seen_sdc.txt"
    stub = tmp_path / "openroad"
    stub.write_text("#!/bin/sh\n"
                    f'printf "%s\\n" "${{R2G_TIMING_SDC-<unset>}}" >> "{seen}"\n'
                    "exit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub, seen


def _project_with_run(tmp_path: Path, *, with_sdc: bool) -> tuple[Path, Path]:
    proj = tmp_path / "comb_proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = comb\nexport PLATFORM = sky130hd\n")
    run = proj / "backend" / "RUN_2026-09-23_00-00-00_1_aaaa"
    (run / "results").mkdir(parents=True)
    (run / "results" / "6_final.def").write_text(_ONE_GATE_DEF)
    (run / "results" / "6_final.odb").write_bytes(b"")
    if with_sdc:
        (run / "results" / "6_final.sdc").write_text(_VIRTUAL_CLOCK_SDC)
    return proj, run


def _run_labels(proj: Path, stub: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for v in ("R2G_DEF", "R2G_ODB", "R2G_SPEF", "R2G_TIMING_SDC", "R2G_SIGNOFF_GATE"):
        env.pop(v, None)
    env["OPENROAD_EXE"] = str(stub)
    env["LABEL_TIMEOUT"] = "20"
    return subprocess.run(["bash", str(RUN_LABELS), str(proj), "sky130hd"],
                          env=env, capture_output=True, text=True, timeout=300)


def test_run_labels_hands_the_bound_runs_sdc_to_timing(tmp_path: Path) -> None:
    stub, seen = _stub_openroad(tmp_path)
    proj, run = _project_with_run(tmp_path, with_sdc=True)
    r = _run_labels(proj, stub)
    assert seen.exists(), f"timing worker never ran:\n{r.stdout}\n{r.stderr}"
    # first line is the timing worker (IR drop runs after it)
    assert seen.read_text().splitlines()[0] == str(run / "results" / "6_final.sdc")


def test_run_labels_without_final_sdc_keeps_the_clock_port_fallback(tmp_path: Path) -> None:
    stub, seen = _stub_openroad(tmp_path)
    proj, _run = _project_with_run(tmp_path, with_sdc=False)
    r = _run_labels(proj, stub)
    assert seen.exists(), f"timing worker never ran:\n{r.stdout}\n{r.stderr}"
    assert seen.read_text().splitlines()[0] == ""


@pytest.fixture(scope="module")
def one_gate(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """A placed one-NAND2 sky130hd ODB, built by the real OpenROAD."""
    tools = _discovered_tools()
    openroad = tools["OPENROAD_EXE"]
    plat = Path(tools["ORFS_ROOT"] or "/nonexistent") / "flow" / "platforms" / "sky130hd"
    lib = plat / "lib" / "sky130_fd_sc_hd__tt_025C_1v80.lib"
    lefs = [plat / "lef" / "sky130_fd_sc_hd.tlef", plat / "lef" / "sky130_fd_sc_hd_merged.lef"]
    if not (openroad and os.access(openroad, os.X_OK) and lib.is_file()
            and all(p.is_file() for p in lefs)):
        pytest.skip("OpenROAD or the sky130hd platform is not installed")
    work = tmp_path_factory.mktemp("one_gate")
    (work / "t.def").write_text(_ONE_GATE_DEF)
    (work / "mk.tcl").write_text(
        "".join(f"read_lef {{{p}}}\n" for p in lefs)
        + f"read_def {{{work / 't.def'}}}\nwrite_db {{{work / 't.odb'}}}\n")
    subprocess.run([openroad, "-no_splash", "-exit", str(work / "mk.tcl")],
                   env=_tool_env(), capture_output=True, text=True, timeout=300, check=True)
    return {"openroad": openroad, "odb": str(work / "t.odb"), "lib": str(lib)}


def _tool_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}


def _extract_timing(one_gate: dict[str, str], out_dir: Path,
                    sdc: str) -> subprocess.CompletedProcess[str]:
    env = _tool_env()
    env.update(ODB_FILE=one_gate["odb"], R2G_LIB_FILES=one_gate["lib"],
               OUTPUT_CSV=str(out_dir / "timing_features.csv"),
               CLOCK_PERIOD="10.0", CLOCK_PORT="", DESIGN_NAME="comb",
               R2G_TIMING_SDC=sdc)
    return subprocess.run([one_gate["openroad"], "-no_splash", "-exit", str(EXTRACT_TIMING)],
                          env=env, capture_output=True, text=True, timeout=300)


def test_virtual_clock_combinational_design_gets_finite_slack(
        one_gate: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "t.sdc").write_text(_VIRTUAL_CLOCK_SDC)
    r = _extract_timing(one_gate, tmp_path, str(tmp_path / "t.sdc"))
    assert r.returncode == 0, r.stdout + r.stderr

    rows = list(csv.DictReader((tmp_path / "timing_features.csv").open()))
    assert [row["Cell"] for row in rows] == ["u1"]
    row = rows[0]
    assert row["in_sta_path"] == "true", row
    slack = float(row["Cell_Slack_ns"])
    # 5.0 period - 1.0 in - 1.0 out leaves 3.0 ns minus one NAND2 delay
    assert 2.0 < slack < 3.0, row
    # Path_Delay uses the SDC's period (5.0), not CLOCK_PERIOD (10.0)
    assert float(row["Path_Delay_ns"]) == pytest.approx(5.0 - slack, abs=1e-5)


def test_named_but_unreadable_sdc_fails_instead_of_falling_back(
        one_gate: dict[str, str], tmp_path: Path) -> None:
    # Falling back to the clock-port path here would silently ship INF labels again.
    r = _extract_timing(one_gate, tmp_path, str(tmp_path / "missing.sdc"))
    assert r.returncode != 0
    assert "is not readable" in r.stdout
    assert not (tmp_path / "timing_features.csv").exists()
