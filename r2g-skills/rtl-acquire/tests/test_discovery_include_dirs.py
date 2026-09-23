"""A `include'd header that lives outside the bundle's directories must be findable.

Regression (wave-3 E5L): discovery set include_dirs to the parent directories of
the bundle's RTL files only. A header kept in its own directory
(`rtl/core/include/defines.v`, `pulpino/rtl/includes/axi_bus.sv`) was never on the
search path, so synthesis failed with "Can't open include file" although the
file is in the repo (4 E5L sources: 5b21200bb514, 7240ac61156f, 7c0f60056792,
e2c25306fdb1). A header is added only when its location is certain: every
repo file matching the include path has identical bytes.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acquire" / \
    "discover_download_candidates.py"

BODY = "\n".join(f"  wire [7:0] w{i} = d + {i};" for i in range(45))


def _top(name: str, include: str) -> str:
    return (f'`include "{include}"\n'
            f"module {name}(input clk, input [7:0] d, output reg [7:0] q);\n"
            f"{BODY}\n  always @(posedge clk) q <= d + `WIDTH;\nendmodule\n")


def _discover(tmp: Path) -> dict[str, dict[str, str]]:
    out_csv = tmp / "candidates.csv"
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONPATH"):
        env.pop(name, None)
    env.update({
        "R2G_ACQUIRE_ROOT": str(tmp / "acq"),
        "R2G_ACQUIRE_WORKSPACE": str(tmp / "workspace"),
        "R2G_ACQUIRE_OUT": str(tmp / "external"),
        "R2G_ACQUIRE_SEED_ROOT": str(tmp / "orfs"),
    })
    subprocess.run(
        [sys.executable, str(SCRIPT), "--downloads-root", str(tmp / "downloads"),
         "--out-csv", str(out_csv), "--scan-state-json", str(tmp / "state.json"),
         "--no-sync-upstream"],
        env=env, check=True, capture_output=True, text=True)
    return {r["expected_top"]: r
            for r in csv.DictReader(out_csv.open(encoding="utf-8", newline=""))}


def test_header_in_a_sibling_directory_joins_include_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "downloads" / "repo_a"
    (repo / "rtl" / "core").mkdir(parents=True)
    (repo / "rtl" / "include").mkdir(parents=True)
    (repo / "rtl" / "include" / "defines.v").write_text("`define WIDTH 8\n", encoding="utf-8")
    (repo / "rtl" / "core" / "core_top.v").write_text(_top("core_top", "defines.v"),
                                                      encoding="utf-8")
    row = _discover(tmp_path)["core_top"]
    assert str((repo / "rtl" / "include").resolve()) in row["include_dirs"].split(";")


def test_identical_copies_resolve_but_divergent_copies_do_not(tmp_path: Path) -> None:
    repo = tmp_path / "downloads" / "repo_b"
    for d in ("rtl/top", "rtl/perips", "models", "cfg_a", "cfg_b"):
        (repo / d).mkdir(parents=True)
    for d in ("rtl/perips", "models"):
        (repo / d / "mem_defs.v").write_text("`define WIDTH 8\n", encoding="utf-8")
    (repo / "cfg_a" / "cfg.vh").write_text("`define WIDTH 8\n", encoding="utf-8")
    (repo / "cfg_b" / "cfg.vh").write_text("`define WIDTH 16\n", encoding="utf-8")
    (repo / "rtl" / "top" / "mem_top.v").write_text(_top("mem_top", "mem_defs.v"),
                                                     encoding="utf-8")
    (repo / "rtl" / "top" / "cfg_top.v").write_text(_top("cfg_top", "cfg.vh"),
                                                    encoding="utf-8")
    rows = _discover(tmp_path)
    mem_dirs = set(rows["mem_top"]["include_dirs"].split(";"))
    assert mem_dirs & {str((repo / "rtl" / "perips").resolve()),
                       str((repo / "models").resolve())}
    cfg_dirs = set(rows["cfg_top"]["include_dirs"].split(";"))
    assert not cfg_dirs & {str((repo / "cfg_a").resolve()), str((repo / "cfg_b").resolve())}


def test_subdirectory_include_path_maps_to_its_root(tmp_path: Path) -> None:
    repo = tmp_path / "downloads" / "repo_c"
    (repo / "rtl" / "top").mkdir(parents=True)
    (repo / "rtl" / "inc" / "bus").mkdir(parents=True)
    (repo / "rtl" / "inc" / "bus" / "axi.vh").write_text("`define WIDTH 8\n",
                                                          encoding="utf-8")
    (repo / "rtl" / "top" / "bus_top.v").write_text(_top("bus_top", "bus/axi.vh"),
                                                    encoding="utf-8")
    row = _discover(tmp_path)["bus_top"]
    assert str((repo / "rtl" / "inc").resolve()) in row["include_dirs"].split(";")
