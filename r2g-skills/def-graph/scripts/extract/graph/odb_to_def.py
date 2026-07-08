#!/usr/bin/env python3
"""Dump a DEF from an OpenDB .odb via OpenROAD (read_db + write_def).

Utility ported unmodified from RTL2Graph/odb2def (2026-07-05); handy when a
run kept only .odb stage snapshots (e.g. 5_route.odb) but a DEF-consuming
extractor needs that stage. The production feature/label stages read the
ORFS-written 6_final.def directly and do not need this.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_openroad_exe(orfs_root: str) -> str:
    if os.environ.get("OPENROAD_EXE"):
        exe = os.environ["OPENROAD_EXE"]
        if Path(exe).is_file():
            return exe

    if orfs_root:
        cand = Path(orfs_root) / "tools" / "install" / "OpenROAD" / "bin" / "openroad"
        if cand.is_file():
            return str(cand)

    exe = shutil.which("openroad")
    if exe:
        return exe

    raise SystemExit(
        "openroad not found. Set OPENROAD_EXE or ensure 'openroad' is in PATH "
        "(or pass --orfs-root that contains tools/install/OpenROAD/bin/openroad)."
    )


def write_tcl(*, odb: Path, out_def: Path) -> str:
    odb_q = str(odb).replace("\\", "/")
    def_q = str(out_def).replace("\\", "/")
    return "\n".join(
        [
            f'read_db "{odb_q}"',
            f'write_def "{def_q}"',
        ]
    ) + "\n"


def convert_one(*, openroad_exe: str, odb: Path, out_def: Path) -> None:
    out_def.parent.mkdir(parents=True, exist_ok=True)

    tcl_text = write_tcl(odb=odb, out_def=out_def)
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False, encoding="utf-8") as f:
        tcl_path = Path(f.name)
        f.write(tcl_text)

    try:
        subprocess.run(
            [openroad_exe, "-no_splash", "-exit", str(tcl_path)],
            check=True,
        )
    finally:
        try:
            tcl_path.unlink()
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert OpenDB .odb to DEF using OpenROAD.")
    ap.add_argument("odb", nargs="+", help="Input .odb file(s).")
    ap.add_argument("--def", dest="def_out", default="", help="Output .def path (only valid when one input is given).")
    ap.add_argument("--out-dir", default="", help="Directory to place output .def files (keeps input basenames).")
    ap.add_argument("--orfs-root", default=os.environ.get("ORFS_ROOT", ""), help="Optional ORFS root for tool discovery.")
    args = ap.parse_args()

    odb_files = [Path(p).expanduser().resolve() for p in args.odb]
    for p in odb_files:
        if not p.is_file():
            raise SystemExit(f"input odb not found: {p}")
        if p.suffix.lower() != ".odb":
            raise SystemExit(f"input is not .odb: {p}")

    if args.def_out and len(odb_files) != 1:
        raise SystemExit("--def can only be used with a single input .odb")

    openroad_exe = find_openroad_exe(args.orfs_root)

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for odb in odb_files:
        if args.def_out:
            out_def = Path(args.def_out).expanduser().resolve()
        elif out_dir:
            out_def = out_dir / (odb.stem + ".def")
        else:
            out_def = odb.with_suffix(".def")

        convert_one(openroad_exe=openroad_exe, odb=odb, out_def=out_def)
        print(out_def)


if __name__ == "__main__":
    main()
