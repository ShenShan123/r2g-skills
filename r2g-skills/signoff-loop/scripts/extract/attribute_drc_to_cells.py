#!/usr/bin/env python3
"""Attribute signoff-deck DRC violations to the PLACED CELL MASTER they sit inside.

Sibling of map_antenna_nets.py: that one maps the deck's lyrdb polygons back onto
the DEF's *nets*, this one maps them back onto the DEF's *instances* and then onto
the master those instances share.

Why this exists.  A DRC class the ROUTER cannot have created -- a contact or
poly/diffusion rule, where the only geometry on the layer belongs to the standard
cells themselves -- is not a routing-congestion problem and no amount of density
relief or pin rebalancing touches it.  It is one cell master's own layout failing
the rule, repeated once per placed instance.  The signature is geometric and
checkable: every violation sits INSIDE an instance of the same master, at the same
offset IN THE MASTER'S OWN COORDINATE FRAME (so once placements are
orientation-normalized, the offsets collapse onto one point).  Measured on gf180
2026-10-15: 46/46 CO.6a violations across 11 designs landed inside
gf180mcu_fd_sc_mcu9t5v0__aoi221_2 at cell-frame (6.67, 0.81) -- one master, one
offset.  Banning that master cleared 47 -> 0 on 12 designs for <=2.3% area.

The honest boundary: this module only *measures*.  It reports the dominant master
with its offset spread and whether a same-function sibling survives the ban, and
says `eligible=false` with a reason when the evidence does not hold.  It never
edits a config.  A violation the DEF cannot attribute to any instance (router
metal between cells) is counted as unattributed, never guessed onto the nearest
cell -- "nearest instance below-left" is how the first attempt at this got the
wrong master entirely (offsets exceeded the cell height, because rows alternate
N/FS and the violation was inside the FLIPPED cell of the row above).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

# Cells synthesis never chooses, so DONT_USE_CELLS cannot remove them: the flow's
# own insertion passes (TAPCELL_TCL, FILL_CELLS, repair_antennas, endcaps) place
# them after mapping.  Fail closed rather than offer a ban that provably no-ops.
_NON_SYNTH_MASTER = re.compile(
    r"(?:^|_)(?:fill|fillcap|tap|tapcell|decap|antenna|endcap|diode|welltap)",
    re.IGNORECASE)

# Drive-strength suffix: the trailing _<digits> that distinguishes siblings of one
# logic function (gf180 ..._aoi221_2 vs ..._aoi221_4; nangate45 AOI221_X1 vs _X2 is
# handled by the _X form).
_DRIVE_SUFFIX = re.compile(r"_(?:[xX])?\d+$")


def function_base(master: str) -> str:
    """The master name with its drive-strength suffix stripped."""
    return _DRIVE_SUFFIX.sub("", master)


def parse_def_components(def_text: str) -> tuple[list[dict], float]:
    """Parse the DEF COMPONENTS section into placed instances (microns).

    Returns (components, dbu_per_micron).  Each component is
    {name, master, x, y, orient} with x/y the placement origin in microns.
    Unplaced components (no PLACED/FIXED/COVER) are dropped -- they have no
    geometry to own a violation.
    """
    units = re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", def_text)
    scale = float(units.group(1)) if units and int(units.group(1)) > 0 else 1000.0
    start = re.search(r"^\s*COMPONENTS\s+\d+\s*;", def_text, re.M)
    end = re.search(r"^\s*END\s+COMPONENTS", def_text, re.M)
    if not start or not end or end.start() <= start.end():
        return [], scale
    section = def_text[start.end():end.start()]
    out = []
    for rec in re.finditer(r"^\s*-\s+(\S+)\s+(\S+)(.*?);", section, re.S | re.M):
        name, master, body = rec.group(1), rec.group(2), rec.group(3)
        place = re.search(
            r"(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*([A-Z]+)",
            body)
        if not place:
            continue
        out.append({"name": name, "master": master,
                    "x": int(place.group(1)) / scale,
                    "y": int(place.group(2)) / scale,
                    "orient": place.group(3)})
    return out, scale


def parse_lef_macro_sizes(lef_paths) -> tuple[dict, list]:
    """master -> (width_um, height_um) over every LEF given.

    Returns (sizes, conflicts).  A master declared with two different SIZEs across
    LEFs (two track options of one platform both on the search path) is reported as
    a conflict and LEFT OUT -- guessing a cell bbox is how an attribution silently
    lands on the wrong instance.
    """
    sizes, conflicts = {}, []
    for path in lef_paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for mac in re.finditer(r"^\s*MACRO\s+(\S+)(.*?)^\s*END\s+\1\s*$",
                               text, re.S | re.M):
            name, body = mac.group(1), mac.group(2)
            size = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;", body, re.M)
            if not size:
                continue
            wh = (float(size.group(1)), float(size.group(2)))
            prev = sizes.get(name)
            if prev is not None and prev != wh:
                conflicts.append(name)
                continue
            sizes[name] = wh
    for name in set(conflicts):
        sizes.pop(name, None)
    return sizes, sorted(set(conflicts))


def cell_frame_offset(vx: float, vy: float, comp: dict,
                      size: tuple[float, float]) -> tuple[float, float]:
    """Map an absolute point into the instance's OWN (unrotated) coordinate frame.

    Without this, the same master-layout defect reads a different offset in every
    orientation and the cluster test fails on a design whose rows alternate N/FS.
    DEF orientations for standard cells: N (R0), S (R180), FN (MY), FS (MX).  The
    90-degree set (E/W/FE/FW) swaps width and height; included for completeness so
    a macro-bearing DEF does not silently mis-map.
    """
    w, h = size
    dx, dy = vx - comp["x"], vy - comp["y"]
    o = comp["orient"]
    if o == "N":
        return dx, dy
    if o == "FS":                      # mirror about the horizontal axis (MX)
        return dx, h - dy
    if o == "FN":                      # mirror about the vertical axis (MY)
        return w - dx, dy
    if o == "S":                       # R180
        return w - dx, h - dy
    if o == "E":
        return dy, w - dx
    if o == "W":
        return h - dy, dx
    if o == "FE":
        return dy, dx
    if o == "FW":
        return h - dy, w - dx
    return dx, dy


def _instance_bbox(comp: dict, size: tuple[float, float]):
    w, h = size
    if comp["orient"] in ("E", "W", "FE", "FW"):
        w, h = h, w
    return comp["x"], comp["y"], comp["x"] + w, comp["y"] + h


def attribute(violations, components, sizes, *,
              offset_tolerance_um: float = 0.05,
              min_attributed_fraction: float = 0.8,
              min_master_fraction: float = 0.8,
              dont_use_patterns=()) -> dict:
    """Decide whether one cell master owns this violation set.

    Pure function over already-parsed inputs so it is unit-testable without a DEF,
    a LEF or KLayout.
    """
    placed = [(c, sizes[c["master"]]) for c in components if c["master"] in sizes]
    result = {
        "violations": len(violations),
        "placed_with_size": len(placed),
        "components": len(components),
        "attributed": 0,
        "unattributed": 0,
        "per_master": {},
        "eligible": False,
        "reason": None,
    }
    if not violations:
        result["reason"] = "no_coordinate_bearing_violations"
        return result
    if not placed:
        result["reason"] = "no_placed_instance_has_a_known_cell_size"
        return result

    # Bucket instances by row (origin y) so a lookup scans one row, not the whole
    # DEF: a 160K-cell design times out otherwise.
    rows: dict[float, list] = {}
    for comp, size in placed:
        rows.setdefault(round(comp["y"], 4), []).append((comp, size))
    row_keys = sorted(rows)
    row_pitch = min((b - a for a, b in zip(row_keys, row_keys[1:]) if b > a),
                    default=None)

    owners = {}
    for idx, vio in enumerate(violations):
        x0, y0, x1, y1 = vio["bbox"]
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        # Candidate rows: the violation center can only be inside an instance whose
        # origin y is at or below it, within one row pitch (taller macros are
        # handled by widening to every row whose origin is below cy).
        hit = None
        for key in reversed(row_keys):
            if key > cy:
                continue
            if row_pitch is not None and cy - key > row_pitch * 8:
                break           # past any standard-cell row; macros fall through
            for comp, size in rows[key]:
                bx0, by0, bx1, by1 = _instance_bbox(comp, size)
                if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                    hit = (comp, size)
                    break
            if hit:
                break
        if hit is None:
            result["unattributed"] += 1
            continue
        comp, size = hit
        ox, oy = cell_frame_offset(cx, cy, comp, size)
        result["attributed"] += 1
        entry = owners.setdefault(comp["master"], {"count": 0, "offsets": [],
                                                   "orients": {}})
        entry["count"] += 1
        entry["offsets"].append((round(ox, 4), round(oy, 4)))
        entry["orients"][comp["orient"]] = entry["orients"].get(comp["orient"], 0) + 1

    for master, entry in owners.items():
        xs = [o[0] for o in entry["offsets"]]
        ys = [o[1] for o in entry["offsets"]]
        result["per_master"][master] = {
            "count": entry["count"],
            "offset_spread_um": [round(max(xs) - min(xs), 4),
                                 round(max(ys) - min(ys), 4)],
            "offset_median_um": [round(sorted(xs)[len(xs) // 2], 4),
                                 round(sorted(ys)[len(ys) // 2], 4)],
            "orientations": entry["orients"],
        }

    if not owners:
        result["reason"] = "every_violation_is_outside_every_cell"
        return result
    master = max(owners, key=lambda m: owners[m]["count"])
    stats = result["per_master"][master]
    result["dominant_master"] = master
    attributed_fraction = result["attributed"] / result["violations"]
    master_fraction = stats["count"] / result["attributed"]
    result["attributed_fraction"] = round(attributed_fraction, 4)
    result["master_fraction"] = round(master_fraction, 4)

    if attributed_fraction < min_attributed_fraction:
        result["reason"] = (f"only {result['attributed']}/{result['violations']} "
                            "violations sit inside a cell (router geometry)")
        return result
    if master_fraction < min_master_fraction:
        result["reason"] = (f"{master} owns only {stats['count']}/"
                            f"{result['attributed']} -- not one master's layout")
        return result
    if max(stats["offset_spread_um"]) > offset_tolerance_um:
        result["reason"] = (f"{master}'s offsets spread "
                            f"{stats['offset_spread_um']} um -- a context-dependent "
                            "interaction, not a fixed defect in the master")
        return result
    if _NON_SYNTH_MASTER.search(master):
        result["reason"] = (f"{master} is inserted by the flow, not chosen by "
                            "synthesis -- DONT_USE_CELLS cannot remove it")
        return result

    banned = list(dont_use_patterns) + [master]
    base = function_base(master)
    siblings = sorted(
        m for m in sizes
        if m != master and function_base(m) == base
        and not any(fnmatch.fnmatch(m, p) for p in banned))
    result["function_base"] = base
    result["surviving_siblings"] = siblings
    if not siblings:
        result["reason"] = (f"banning {master} would leave no {base} cell for "
                            "synthesis to map onto")
        return result

    result["eligible"] = True
    return result


def discover_lefs(platform_dir: Path) -> list:
    return sorted(p for p in (platform_dir / "lef").glob("*.lef") if p.is_file())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lyrdb", required=True)
    ap.add_argument("--def", dest="def_file", required=True)
    ap.add_argument("--lef", action="append", default=[],
                    help="LEF with MACRO SIZE records (repeatable)")
    ap.add_argument("--platform-dir", help="adds <dir>/lef/*.lef")
    ap.add_argument("--rule", help="only attribute this DRC category "
                                   "(default: the most frequent one)")
    ap.add_argument("--dont-use", default="",
                    help="current DONT_USE_CELLS value (patterns, space-separated)")
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
    import render_drc_violation

    lefs = list(args.lef)
    if args.platform_dir:
        lefs += [str(p) for p in discover_lefs(Path(args.platform_dir))]
    sizes, conflicts = parse_lef_macro_sizes(lefs)
    try:
        comps, _ = parse_def_components(
            Path(args.def_file).read_text(encoding="utf-8", errors="ignore"))
    except OSError as exc:
        print(f"ERROR: cannot read DEF: {exc}", file=sys.stderr)
        return 2

    vios = render_drc_violation.parse_lyrdb_violations(Path(args.lyrdb))
    rule = args.rule
    if rule is None:
        counts = {}
        for v in vios:
            key = str(v.get("category", "")).strip("'\"")
            counts[key] = counts.get(key, 0) + 1
        rule = max(counts, key=lambda k: counts[k], default="")
    selected = [v for v in vios
                if str(v.get("category", "")).strip("'\"").lower() == rule.lower()]

    out = attribute(selected, comps, sizes,
                    dont_use_patterns=args.dont_use.split())
    out["rule"] = rule
    out["lef_size_conflicts"] = conflicts
    out["lefs_read"] = len(lefs)
    text = json.dumps(out, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out.get("eligible") else 1


if __name__ == "__main__":
    sys.exit(main())
