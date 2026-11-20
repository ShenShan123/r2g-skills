#!/usr/bin/env python3
"""Exp 4 v2: stage design inputs in the common layout (PROTOCOL_v2.md section 4), on 216.

    stage_v2.py --out ROOT --n45 D1,D2,... [--sky130hd ...] [--gf180 ...]
    stage_v2.py --out ROOT --sample sample_v2.json

Nangate45 from the v1 pilot500 copy ($W/data/designs/<d>); SKY130HD / GF180 from the rerun trees
(<tree>/runs/<d>). Files are hard links (read-only sources), except: Nangate45 6_final.def is a link
of 5_route.def and 6_final.sdc a copy of constraint.sdc; SKY130HD/GF180 3_place.def is written ONCE
here from the run's 3_place.odb (OpenROAD read_db + write_def, in the conversion image). Records
<ROOT>/<d>/STAGED.json with every file's sha256 and origin.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

W = Path("/proj/workarea/yangao/exp4_conversion_20261107")
TREES = {"nangate45": W / "data/designs", "sky130hd": Path("/proj/workarea/yangao/rerun_sky130hd_20261022/runs"),
         "gf180": Path("/proj/workarea/yangao/rerun_gf180_20261026/runs")}
IMAGE = "localhost/exp4c-conv:1"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def link(src, dst, origin):
    if not dst.exists():
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
    origin[dst.name] = str(src)


def stage(root, plat, d):
    src = TREES[plat] / d
    out = root / d
    if (out / "STAGED.json").is_file():
        return "cached"
    out.mkdir(parents=True, exist_ok=True)
    origin = {}
    for f in ("config.mk", "constraint.sdc", "6_final.spef"):
        link(src / f, out / f, origin)
    if plat == "nangate45":
        link(src / "5_route.def", out / "6_final.def", origin)
        shutil.copyfile(src / "constraint.sdc", out / "6_final.sdc"); origin["6_final.sdc"] = "copy of constraint.sdc"
        for f in ("3_place.def", "1_2_yosys.v", "2_floorplan.def", "4_cts.def", "5_route.def"):
            link(src / f, out / f, origin)
    else:
        for f in ("6_final.def", "6_final.sdc"):
            link(src / f, out / f, origin)
        tcl = out / ".export_place.tcl"
        tcl.write_text("read_db /src/3_place.odb\nwrite_def /out/3_place.def\n")
        r = subprocess.run(["podman", "run", "--rm", "--network", "none", "-v", f"{src}:/src:ro,Z", "-v", f"{out}:/out:Z",
                            IMAGE, "openroad", "-no_splash", "-exit", "/out/.export_place.tcl"], capture_output=True, text=True)
        tcl.unlink()
        if r.returncode or not (out / "3_place.def").is_file():
            raise RuntimeError(f"{d}: 3_place.def export failed: {(r.stdout + r.stderr)[-500:]}")
        origin["3_place.def"] = f"write_def of {src}/3_place.odb (sha256 {sha(src / '3_place.odb')})"
    rec = {"design": d, "platform": plat, "files": {f.name: {"sha256": sha(f), "origin": origin.get(f.name)}
                                                     for f in sorted(out.iterdir()) if f.is_file()}}
    (out / "STAGED.json").write_text(json.dumps(rec, indent=1))
    for f in out.iterdir():
        os.chmod(f, 0o444)
    return "staged"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sample", type=Path)
    for p in TREES:
        ap.add_argument("--" + p, default="")
    a = ap.parse_args()
    todo = []
    if a.sample:
        s = json.loads(a.sample.read_text())
        for p in TREES:
            for d in (s.get("platforms", {}).get(p) or s.get(p) or []):
                todo.append((p, d if isinstance(d, str) else d["design"]))
    for p in TREES:
        todo += [(p, d) for d in getattr(a, p).split(",") if d]
    a.out.mkdir(parents=True, exist_ok=True)
    for p, d in todo:
        print(p, d, stage(a.out, p, d), flush=True)


if __name__ == "__main__":
    main()
