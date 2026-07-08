#!/usr/bin/env python3
"""Materialize a sky130hd project from an existing (nangate45) r2g project.

Reuses the source design's RTL (absolute VERILOG_FILES paths, in place) and SDC
(copied, so timing fixes can bump clk_period without touching the source). Writes
a fresh sky130hd config.mk that follows the skill's floorplan policy (prefer
CORE_UTILIZATION over a nangate45-sized DIE_AREA) and leaves CDL_FILE unset so
run_lvs.sh injects the sky130 macro_sparecell slash-fix automatically.

Exit codes:
  0  project materialized
  2  unportable to sky130hd (hard macros / fakeram) -- caller records honest-final
  1  hard error (missing source artifacts)

usage: mk_sky130_project.py <source_project_dir> <dest_project_dir>
"""
import os
import re
import shutil
import sys
from pathlib import Path


def join_continuations(text: str) -> list[str]:
    """Collapse makefile backslash line-continuations into logical lines."""
    out, buf = [], ""
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
        else:
            out.append(buf + line)
            buf = ""
    if buf:
        out.append(buf)
    return out


def parse_config(path: Path) -> dict:
    """Parse `export KEY = VALUE` / `KEY = VALUE` / `KEY += VALUE` assignments."""
    vals: dict[str, str] = {}
    pat = re.compile(r"^\s*(?:override\s+)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*([:+]?=)\s*(.*)$")
    for line in join_continuations(path.read_text(encoding="utf-8", errors="ignore")):
        if line.lstrip().startswith("#"):
            continue
        m = pat.match(line)
        if not m:
            continue
        key, op, val = m.group(1), m.group(2), m.group(3).strip()
        if op == "+=" and key in vals:
            vals[key] = vals[key] + " " + val
        else:
            vals[key] = val
    return vals


def source_def_pins(src: Path) -> int:
    """Bit-blasted top-level pin count from the source design's ORFS DEF.

    Bus ports are already expanded in the DEF `PINS N` header, so this is the
    true IO-pad demand (the RTL port *declaration* count is far smaller — a
    64-bit AXI stream demux is ~57 declarations but ~1500 pads). Returns 0 when
    no DEF is present (e.g. a source that never reached detailed placement).
    """
    backend = src / "backend"
    if not backend.is_dir():
        return 0
    defs = sorted(backend.rglob("6_final.def")) or sorted(backend.rglob("*.def"))
    for d in reversed(defs):
        try:
            with open(d, errors="ignore") as fh:
                for line in fh:
                    if line.startswith("PINS "):
                        return int(line.split()[1])
        except Exception:
            continue
    return 0


_PHYS_ONLY_RE = re.compile(
    r"(?:^|_)(?:fill|fillcell|tap|tapcell|decap|antenna|endcap)", re.I)


def source_def_components(src: Path) -> int:
    """Logic-cell instance count from the source design's ORFS DEF.

    Fallback cell-count for die sizing when the source `ppa.json` lacks
    `cell_count` (older runs predate that field — e.g. April-2026 corpus). The
    DEF `COMPONENTS N` header counts *all* placed instances **including
    fillers/taps**, which on a low-utilization nangate45 run is the majority
    (iccad2015_unit14_in1: 9986 components, 6880 of them fill/tap). Counting raw
    components would massively over-estimate area; under-counting by trusting a
    missing cell_count (=> 0) instead floors a large design into the tiny PDN die
    and detailed placement aborts at ~100% utilization (DPL-0036). So walk the
    COMPONENTS section and count only the logic cells (exclude fill/tap/decap/
    antenna/endcap). Returns 0 when no DEF is present.
    """
    backend = src / "backend"
    if not backend.is_dir():
        return 0
    defs = sorted(backend.rglob("6_final.def")) or sorted(backend.rglob("*.def"))
    for d in reversed(defs):
        try:
            n = 0
            in_components = False
            with open(d, errors="ignore") as fh:
                for line in fh:
                    s = line.strip()
                    if not in_components:
                        if s.startswith("COMPONENTS "):
                            in_components = True
                        continue
                    if s.startswith("END COMPONENTS"):
                        break
                    # Each instance entry: "- <inst_name> <MACRO> + ... ;"
                    if s.startswith("-"):
                        parts = s.split()
                        if len(parts) >= 3 and not _PHYS_ONLY_RE.search(parts[2]):
                            n += 1
            if n > 0 or in_components:
                return n
        except Exception:
            continue
    return 0


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: mk_sky130_project.py <source_project_dir> <dest_project_dir>", file=sys.stderr)
        return 1
    src = Path(sys.argv[1]).resolve()
    dest = Path(sys.argv[2]).resolve()
    src_cfg = src / "constraints" / "config.mk"
    src_sdc = src / "constraints" / "constraint.sdc"
    if not src_cfg.is_file():
        print(f"ERROR: no config.mk at {src_cfg}", file=sys.stderr)
        return 1

    cfg = parse_config(src_cfg)
    design = cfg.get("DESIGN_NAME", "").strip()
    if not design:
        print(f"ERROR: DESIGN_NAME not found in {src_cfg}", file=sys.stderr)
        return 1

    # --- macro / hard-memory portability gate -------------------------------
    blob = "\n".join(f"{k}={v}" for k, v in cfg.items())
    macro_markers = ["ADDITIONAL_LEFS", "MACRO_PLACEMENT_TCL", "fakeram", "GDS_ALLOW_EMPTY"]
    if any(mk in cfg for mk in ("ADDITIONAL_LEFS", "MACRO_PLACEMENT_TCL", "GDS_ALLOW_EMPTY")) or "fakeram" in blob.lower():
        print(f"UNPORTABLE: {design} uses hard macros (nangate45 fakeram/LEF) -> needs sky130 SRAM, skip", file=sys.stderr)
        return 2

    verilog = cfg.get("VERILOG_FILES", "").strip()
    if not verilog:
        print(f"ERROR: VERILOG_FILES not found in {src_cfg}", file=sys.stderr)
        return 1
    # Resolve relative tokens against the source dir; verify >=1 file exists.
    toks = []
    for t in verilog.split():
        if t.startswith("$"):  # unresolved make var -> bail (likely macro/platform)
            print(f"UNPORTABLE: {design} VERILOG_FILES has make-var token {t}", file=sys.stderr)
            return 2
        p = Path(t)
        if not p.is_absolute():
            p = (src / t)
        toks.append(str(p))
    if not any(Path(t).is_file() for t in toks):
        print(f"ERROR: none of VERILOG_FILES exist for {design}", file=sys.stderr)
        return 1

    # --- materialize dest ---------------------------------------------------
    (dest / "constraints").mkdir(parents=True, exist_ok=True)
    for sub in ("rtl", "reports", "drc", "lvs", "rcx", "backend", "input"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    dest_sdc = dest / "constraints" / "constraint.sdc"
    if src_sdc.is_file():
        shutil.copyfile(src_sdc, dest_sdc)
    else:
        # SDC_FILE may point elsewhere; copy whatever the config referenced.
        ref = cfg.get("SDC_FILE", "").strip()
        if ref and Path(ref).is_file():
            shutil.copyfile(ref, dest_sdc)
        else:
            print(f"ERROR: no SDC for {design}", file=sys.stderr)
            return 1

    cu = cfg.get("CORE_UTILIZATION", "").strip()
    try:
        cu_val = int(float(cu)) if cu else 20
    except ValueError:
        cu_val = 20
    pdl = cfg.get("PLACE_DENSITY_LB_ADDON", "").strip()
    try:
        pdl_val = max(0.10, float(pdl)) if pdl else 0.20
    except ValueError:
        pdl_val = 0.20

    # Floorplan policy for sky130hd:
    #   Small cores cannot fit sky130hd's met4/met5 PDN straps -> floorplan aborts
    #   with PDN-0185 "Insufficient width to add straps" REGARDLESS of utilization
    #   (a 65-cell core is ~7um wide; met4 straps need ~15.2um + 13.6um offset).
    #   So small designs get an explicit DIE_AREA floored to a PDN-feasible size
    #   (cordic-validated 200um core), while designs naturally large enough use
    #   CORE_UTILIZATION (auto-sized; avoids the IO-perimeter overflow that an
    #   explicit die risks on high-pin designs).
    #   See references/failure-patterns.md "sky130 small-core PDN strap floor".
    import math
    cell_count = 0
    src_ppa = src / "reports" / "ppa.json"
    if src_ppa.is_file():
        try:
            import json as _json
            _p = _json.loads(src_ppa.read_text())
            # extract_ppa writes the instance count under geometry.instance_count,
            # NOT a top-level cell_count key — reading the wrong key made cell_count
            # silently 0 for *every* design, so use_floor was always true and a
            # large design over-packed the 200um PDN floor (DPL-0036). Prefer the
            # geometry field; keep the top-level read in case it is ever populated.
            cell_count = int(_p.get("cell_count")
                             or (_p.get("geometry") or {}).get("instance_count")
                             or 0)
        except Exception:
            cell_count = 0
    # Final fallback for sources with no ppa.json geometry: count logic cells
    # straight from the source DEF. Without an accurate count a large design is
    # treated as ~0 cells, floored into the 200um PDN die, and aborts detailed
    # placement at ~100% utilization (DPL-0036).
    if cell_count <= 0:
        cell_count = source_def_components(src)
    SKY130_CELL_UM2 = 8.0          # std-cell footprint estimate
    PDN_DIE_FLOOR = 200            # um; cordic-validated minimum for met4 straps
    est_core = max(cell_count, 1) * SKY130_CELL_UM2 / (cu_val / 100.0)
    core_side = math.sqrt(est_core)
    use_floor = core_side < (PDN_DIE_FLOOR - 40)   # design too small -> needs floor

    # IO-pad perimeter demand (PPL-0024). A cell-area-tiny but pin-huge design
    # (wide AXI/bus demuxes, packet routers) overflows the die perimeter even
    # when its logic fits: ORFS aborts floorplan with
    #   [ERROR PPL-0024] Number of IO pins (N) exceeds maximum number of
    #   available positions (718). Increase the die perimeter ...
    # The 200um floor die (800um perimeter) seats ~718 pads; only when the source
    # DEF's pad count exceeds that do we enlarge the die, sized from PPL's own
    # recommended ~1.36um/pad (which already carries its corner margin) plus a
    # safety factor and rounded up to 10um. This is a strict *lower bound* that is
    # a no-op for every <=718-pad design, so all previously-clean designs keep
    # their byte-identical 200um floor. See references/failure-patterns.md
    # "sky130 high-pin-count floorplan (PPL-0024)".
    FLOOR_PIN_CAPACITY = 718
    pins = source_def_pins(src)
    pin_side = (math.ceil(pins * 1.45 / 4 / 10) * 10
                if pins > FLOOR_PIN_CAPACITY else 0)

    # Cell-area die side: when we are forced onto the explicit-DIE path (small core
    # or pin overflow), the die must ALSO be large enough to seat the design's cells
    # at cu_val utilization, else place aborts `[ERROR FLW-0024] Place density
    # exceeds 1.0`. The pin-aware path (PPL-0024 fix) previously sized the die for
    # pads ALONE (max(floor, pin_side)); a design that is BOTH pad-heavy AND
    # cell-dense then over-packs (sha256_stream: 777 pads -> pin_side 290um, but
    # 12083 sky130 cells need a ~700um core -> 290um die = 108% util -> place fail).
    # sky130 std cells are ~4.5x nangate45 area, so a design that fit on nangate45
    # can overflow here. cell_side = core side (sqrt of cell area at cu_val) + the
    # 2x10um CORE_AREA margin, rounded up to 10um; 0 when cell_count is unknown.
    # See references/failure-patterns.md "sky130 die under-sized for cells (FLW-0024)".
    cell_side = (math.ceil((core_side + 20) / 10) * 10) if cell_count > 0 else 0

    lines = [
        f"export DESIGN_NAME = {design}",
        "export PLATFORM    = sky130hd",
        "",
        f"export VERILOG_FILES = {' '.join(toks)}",
        f"export SDC_FILE      = {dest_sdc}",
        "",
    ]
    if use_floor or pin_side:
        # Explicit DIE: the PDN-feasible floor (PDN-0185), raised when needed to
        # seat the IO pads (PPL-0024) AND to hold the cells at cu_val (FLW-0024).
        # max() of all three so a pad-heavy + cell-dense design fits both. (We only
        # reach this path for small cores or pin overflow; a large non-pin design
        # still takes the CORE_UTILIZATION branch below -- cell_side just guards the
        # pin-heavy sub-case where the cell demand exceeds the pad-perimeter demand.)
        side = max(PDN_DIE_FLOOR, pin_side, cell_side)
        if pin_side and cell_side > pin_side:
            why = (f"Pin-heavy ({pins} pads) AND cell-dense: die sized for cells "
                   f"(FLW-0024) since cell area exceeds the pad perimeter need")
        elif pin_side:
            why = f"Pin-heavy ({pins} pads): die enlarged for IO perimeter (PPL-0024)"
        elif cell_side > PDN_DIE_FLOOR:
            why = "Cell-dense small core: die sized to hold cells at util (FLW-0024)"
        else:
            why = "Small/PDN-floored die"
        lines += [
            f"# {why}.",
            f"export DIE_AREA  = 0 0 {side} {side}",
            f"export CORE_AREA = 10 10 {side - 10} {side - 10}",
            f"export PLACE_DENSITY_LB_ADDON = {pdl_val}",
            "export ABC_AREA = 1",
        ]
    else:
        lines += [
            "# Large enough for the PDN grid -> utilization-based floorplan.",
            f"export CORE_UTILIZATION = {cu_val}",
            f"export PLACE_DENSITY_LB_ADDON = {pdl_val}",
            "export ABC_AREA = 1",
        ]
    # Carry over memory / hierarchy / safety knobs when the source needed them.
    for k in ("SYNTH_MEMORY_MAX_BITS", "SYNTH_HIERARCHICAL", "ABC_CLOCK_PERIOD_IN_PS",
              "SKIP_CTS_REPAIR_TIMING", "SKIP_LAST_GASP"):
        if k in cfg and cfg[k].strip():
            lines.append(f"export {k} = {cfg[k].strip()}")
    # Always split port-to-port feedthrough nets (`assign out = in`). ORFS
    # global_place runs remove_buffers, merging both ports onto one net, which
    # SPICE cannot express -> Netgen LVS "Top level cell failed pin matching"
    # (8/13 residuals of the first 50-design wave were feedthrough-free; the 5
    # diode-free ones were ALL this). The hook is a no-op for designs without
    # feedthroughs. See r2g skill references/failure-patterns.md "sky130 LVS".
    fdbuf_hook = (Path(__file__).resolve().parent.parent
                  / "r2g-skills/signoff-loop" / "scripts" / "flow" / "orfs_hooks"
                  / "buffer_port_feedthroughs.tcl")
    lines += [
        "",
        "# Split port-to-port feedthrough nets so Netgen LVS top-level pins match",
        f"export POST_GLOBAL_PLACE_TCL = {fdbuf_hook}",
    ]
    # CDL_FILE intentionally unset -> run_lvs.sh injects the sky130 slash-fix.
    (dest / "constraints" / "config.mk").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK: materialized {dest} (design={design}, CU={cu_val})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
