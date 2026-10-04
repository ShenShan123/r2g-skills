"""A package-only file must join the closure of every module that imports it.

Regression (wave-3 E5L, 2026-09-23): `file_is_candidate` rejects a file with no
`module` as `no_module`, and the discovery loop then skipped any rejected file
without module definitions BEFORE registering its package names. `import pkg::*`
therefore never resolved, the bundle shipped without the package, and synthesis
failed as a compile-closure error on RTL whose package sat in the same repo. Worse,
with no resolved local reference a compact importer looked like a standalone leaf
and was dropped from discovery entirely.
This runs the real discovery entry point, not `bundle_closure` with a
hand-built symbol table, because the defect lived in the loop that builds it.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "acquire" / "discover_download_candidates.py"


def _discover(root: Path, downloads: Path) -> list[dict[str, str]]:
    out_csv = root / "candidates.csv"
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONPATH"):
        env.pop(name, None)
    env.update({
        "R2G_ACQUIRE_ROOT": str(root / "acq"),
        "R2G_ACQUIRE_WORKSPACE": str(root / "workspace"),
        "R2G_ACQUIRE_OUT": str(root / "external"),
        "R2G_ACQUIRE_SEED_ROOT": str(root / "orfs"),
    })
    subprocess.run(
        [sys.executable, str(SCRIPT), "--downloads-root", str(downloads),
         "--out-csv", str(out_csv), "--scan-state-json", str(root / "state.json"),
         "--no-sync-upstream"],
        env=env, check=True, capture_output=True, text=True)
    with out_csv.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_package_only_file_is_in_the_importers_rtl_files(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    rtl = downloads / "repo_pkg" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "types_pkg.sv").write_text(
        "package types_pkg;\n"
        "  typedef logic [7:0] byte_t;\n"
        "  localparam int WIDTH = 8;\n"
        "endpackage\n", encoding="utf-8")
    body = "\n".join(f"  byte_t r{i};" for i in range(60))
    (rtl / "packet_engine.sv").write_text(
        "module packet_engine import types_pkg::*; (\n"
        "  input logic clk, input byte_t d, output byte_t q);\n"
        f"{body}\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "endmodule\n", encoding="utf-8")

    rows = _discover(tmp_path, downloads)

    engine = [r for r in rows if r["expected_top"] == "packet_engine"]
    assert engine, f"importer was not emitted as a candidate: {rows}"
    files = {Path(p).name for p in engine[0]["rtl_files"].split(";")}
    assert "types_pkg.sv" in files, (
        "package-only file dropped from the closure: " + engine[0]["rtl_files"])
    # The package file is a dependency, never a design candidate of its own.
    assert not any(Path(r["source_path"]).name == "types_pkg.sv" for r in rows)
