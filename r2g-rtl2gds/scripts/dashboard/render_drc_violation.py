#!/usr/bin/env python3
"""Render the neighborhood of a DRC violation (or cluster) to a PNG for the
vision-assisted DRC channel.

Win 4 (paper-absorption-2026-06-16.md): when the text DRC-fix path under-determines
a fix — i.e. `diagnose_signoff_fix` returns a low-confidence / `catalog_exhausted`
residual — and `R2G_VISION_DRC=1`, render the violation neighborhood to an image so a
vision-capable escalation model can look at the actual geometry. PostEDA-Bench's
`vision_query_with_pts` is the source idea (KLayout-rendered violation crops); its
"never harmful; largest lift where text-only is weak" result was measured on **ASAP7
only**, so for sky130 this is a *hypothesis to validate on r2g-bench*, NOT a proven
transfer. Treat it accordingly.

Design (matches repo conventions: pure-function core, I/O at edges, env-gated, fail-soft):

  * Pure core (`parse_lyrdb_violations`, `crop_regions`) computes the list of crop
    regions (bbox + margin per violation cluster) from the KLayout report DB. No tool
    needed — unit-tested directly.
  * KLayout is a SOFT dependency. The driver (`render_regions`) mirrors
    render_gds_preview.py's invocation pattern (KLAYOUT_CMD/klayout, headless `-b -nc -r`).
    If KLayout is absent it returns cleanly with a clear message — no crash.
  * The escalation hook (`attach_vision_artifacts`) is **off by default**: with
    `R2G_VISION_DRC` unset it is a no-op and the text path is byte-for-byte unchanged.

IMPORTANT coordinate reality (verified against the real corpus, 2026-06-16):
`reports/drc.json` (extract_drc.py) carries only per-CATEGORY counts/descriptions —
it has NO per-violation coordinates. The coordinates live in the KLayout report DB
(`drc/6_drc.lyrdb`) inside each `<item>`'s `<value>` tag, e.g.
`edge-pair: (191.596,92.645;192.15,92.645)|...` or `polygon: (x,y;x,y;...)` in microns.
So this module reads the lyrdb directly for geometry; `crop_regions` accepts the parsed
violation list. When no lyrdb / no coordinate-bearing items exist, it degrades
gracefully (see `coordinate_status` / the full-GDS fallback note in `render_regions`).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

# A coordinate tuple inside a <value> geometry string, e.g. "(191.596,92.645;192.15,92.645)".
# Geometry value forms seen in the sky130/nangate45 corpus: "edge-pair:" and "polygon:".
# Non-geometry annotation values ("[#agate] float: ...", "text: '(0)'") carry no layout
# coordinates and are skipped — we only mine forms whose payload is coordinate tuples.
_COORD_NUM = r"-?\d+(?:\.\d+)?"
_COORD_PAIR_RE = re.compile(rf"({_COORD_NUM})\s*,\s*({_COORD_NUM})")
_GEOM_VALUE_RE = re.compile(r"^\s*(edge-pair|edge|polygon|box|path|point)\s*:", re.I)


def _value_bbox(value_text: str) -> tuple[float, float, float, float] | None:
    """(xmin, ymin, xmax, ymax) over all coordinate pairs in a geometry <value>, or
    None if the value is not a coordinate-bearing geometry (e.g. a float/text annotation)."""
    if value_text is None:
        return None
    if not _GEOM_VALUE_RE.match(value_text):
        return None
    xs: list[float] = []
    ys: list[float] = []
    for m in _COORD_PAIR_RE.finditer(value_text):
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def parse_lyrdb_violations(lyrdb_path: Path) -> list[dict]:
    """Parse a KLayout report DB into a flat list of coordinate-bearing violations.

    Returns a list of dicts: {category, cell, bbox:(xmin,ymin,xmax,ymax)} in microns.
    Items with no parseable geometry (pure float/text annotations) are skipped. This is
    pure I/O over one file; the geometry math lives in `_value_bbox` / `crop_regions`.
    """
    lyrdb_path = Path(lyrdb_path)
    if not lyrdb_path.exists():
        return []
    try:
        root = ET.parse(str(lyrdb_path)).getroot()
    except ET.ParseError:
        return []

    out: list[dict] = []
    for item in root.iter("item"):
        cat_el = item.find("category")
        cell_el = item.find("cell")
        category = cat_el.text.strip() if cat_el is not None and cat_el.text else ""
        cell = cell_el.text.strip() if cell_el is not None and cell_el.text else ""
        # Union the bbox across every coordinate-bearing <value> in the item.
        bbox = None
        for val in item.iter("value"):
            vb = _value_bbox(val.text)
            if vb is None:
                continue
            bbox = vb if bbox is None else (
                min(bbox[0], vb[0]), min(bbox[1], vb[1]),
                max(bbox[2], vb[2]), max(bbox[3], vb[3]),
            )
        if bbox is None:
            continue
        out.append({"category": category, "cell": cell, "bbox": bbox})
    return out


def _expand(bbox: tuple[float, float, float, float], margin_um: float) -> tuple[float, float, float, float]:
    return (bbox[0] - margin_um, bbox[1] - margin_um, bbox[2] + margin_um, bbox[3] + margin_um)


def _overlaps(a: tuple, b: tuple) -> bool:
    """Axis-aligned bbox overlap (touching counts as overlapping)."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def crop_regions(violations: list[dict], margin_um: float = 2.0,
                 max_clusters: int | None = 24) -> list[dict]:
    """Pure: a list of violations -> a list of crop regions, one per spatial cluster.

    Each violation's bbox is expanded by `margin_um`; violations whose expanded boxes
    overlap (and that share a DRC category) are merged into one cluster so the rendered
    crop shows the whole interacting neighborhood rather than N near-identical tiles.
    The returned region bbox is the union of member bboxes plus the margin.

    Returns dicts: {cluster, category, bbox:(xmin,ymin,xmax,ymax), n_violations, cells}.
    `cluster` is a stable slug usable as a PNG filename. With no coordinate-bearing
    violations, returns []. `max_clusters` caps the count (render cost is bounded — Win 4
    only fires on escalation, but a pathological design can still emit thousands of
    markers); None disables the cap.
    """
    if margin_um < 0:
        raise ValueError("margin_um must be >= 0")

    # Group by category first (a fix is decided per rule), then cluster spatially within.
    by_cat: dict[str, list[dict]] = {}
    for v in violations or []:
        if not isinstance(v.get("bbox"), (tuple, list)) or len(v["bbox"]) != 4:
            continue
        by_cat.setdefault(v.get("category", ""), []).append(v)

    clusters: list[dict] = []
    for category in sorted(by_cat):
        members = by_cat[category]
        # Single-linkage clustering on margin-expanded boxes. O(n^2) but n is small after
        # the escalation gate; deterministic order (sorted by bbox) keeps slugs stable.
        expanded = [(_expand(tuple(v["bbox"]), margin_um), v) for v in members]
        expanded.sort(key=lambda ev: ev[0])
        used = [False] * len(expanded)
        for i in range(len(expanded)):
            if used[i]:
                continue
            box_i, _ = expanded[i]
            group_idx = [i]
            used[i] = True
            # Iteratively absorb any not-yet-used box that overlaps the growing union.
            grew = True
            cur = box_i
            while grew:
                grew = False
                for j in range(len(expanded)):
                    if used[j]:
                        continue
                    if _overlaps(cur, expanded[j][0]):
                        used[j] = True
                        group_idx.append(j)
                        bj = expanded[j][0]
                        cur = (min(cur[0], bj[0]), min(cur[1], bj[1]),
                               max(cur[2], bj[2]), max(cur[3], bj[3]))
                        grew = True
            member_vs = [expanded[k][1] for k in group_idx]
            cells = sorted({m.get("cell", "") for m in member_vs if m.get("cell")})
            clusters.append({
                "category": category,
                "bbox": tuple(round(c, 4) for c in cur),
                "n_violations": len(member_vs),
                "cells": cells,
            })

    # Largest (most violations) first — that's where vision adds the most signal.
    clusters.sort(key=lambda c: (-c["n_violations"], c["category"], c["bbox"]))
    if max_clusters is not None:
        clusters = clusters[:max_clusters]
    for n, c in enumerate(clusters):
        c["cluster"] = f"{_slug(c['category'])}_{n:03d}"
    return clusters


def _slug(text: str) -> str:
    """Filesystem-safe slug for a DRC category, e.g. "'m3.2'" -> "m3_2"."""
    s = re.sub(r"[^0-9A-Za-z]+", "_", str(text)).strip("_").lower()
    return s or "drc"


def coordinate_status(lyrdb_path: Path) -> dict:
    """Honest report of whether vision rendering is possible for this run.

    Returns {available: bool, n_coordinate_violations: int, reason: str}. The escalation
    payload includes this so the agent knows WHY no images were attached (no lyrdb vs.
    lyrdb present but only annotation values) rather than silently seeing nothing.
    """
    lyrdb_path = Path(lyrdb_path)
    if not lyrdb_path.exists():
        return {"available": False, "n_coordinate_violations": 0,
                "reason": "no lyrdb (drc/6_drc.lyrdb); reports/drc.json carries no "
                          "per-violation coordinates, so a bbox crop is impossible — "
                          "fall back to a full-GDS preview"}
    vios = parse_lyrdb_violations(lyrdb_path)
    if not vios:
        return {"available": False, "n_coordinate_violations": 0,
                "reason": "lyrdb present but no coordinate-bearing items (annotation-only "
                          "values, e.g. antenna float/text); no bbox to crop"}
    return {"available": True, "n_coordinate_violations": len(vios), "reason": ""}


# --------------------------------------------------------------------------- #
# KLayout driver (the ONLY part that needs the tool). Mirrors render_gds_preview.py.
# --------------------------------------------------------------------------- #

def klayout_cmd() -> str | None:
    """Resolve the KLayout binary the same way scripts/flow/_env.sh does:
    honor $KLAYOUT_CMD if it points at an executable, else PATH, else the usual prefixes.
    Returns None if KLayout is not installed (soft dependency)."""
    env_cmd = os.environ.get("KLAYOUT_CMD")
    if env_cmd and (shutil.which(env_cmd) or (Path(env_cmd).is_file() and os.access(env_cmd, os.X_OK))):
        return env_cmd
    found = shutil.which("klayout")
    if found:
        return found
    for cand in ("/usr/local/bin/klayout", "/usr/bin/klayout"):
        if Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def _render_script(gds: Path, regions: list[dict], out_dir: Path, size: int) -> str:
    """Build the headless KLayout Python script that loads the GDS once and saves one
    cropped PNG per region (zoom_box to bbox+margin, full hierarchy)."""
    lines = [
        "import pya",
        "view = pya.LayoutView()",
        f'view.load_layout(r"{gds}", 0)',
        "view.max_hier()",
    ]
    for c in regions:
        x0, y0, x1, y1 = c["bbox"]
        png = out_dir / f"{c['cluster']}.png"
        lines += [
            f"view.zoom_box(pya.DBox({x0}, {y0}, {x1}, {y1}))",
            f'view.save_image(r"{png}", {size}, {size})',
        ]
    return "\n".join(lines) + "\n"


def render_regions(gds: Path, regions: list[dict], out_dir: Path, *,
                   size: int = 800, timeout: int = 180) -> dict:
    """Render each crop region from `gds` into `out_dir/<cluster>.png` via headless KLayout.

    Soft dependency: if KLayout is absent OR there are no coordinate-bearing regions,
    returns cleanly ({rendered: [], skipped: <reason>}) without raising. On a missing
    GDS the caller should fall back to render_gds_preview (full layout) — we signal that
    via skipped='no_gds'."""
    gds = Path(gds)
    out_dir = Path(out_dir)
    if not gds.exists():
        return {"rendered": [], "skipped": "no_gds", "out_dir": str(out_dir)}
    if not regions:
        return {"rendered": [], "skipped": "no_coordinate_regions", "out_dir": str(out_dir)}

    kcmd = klayout_cmd()
    if not kcmd:
        return {"rendered": [], "skipped": "klayout_not_installed",
                "message": "KLAYOUT_CMD/klayout not found — vision DRC is a soft dependency; "
                           "install KLayout to enable bbox crops. Text fix path unaffected.",
                "out_dir": str(out_dir)}

    out_dir.mkdir(parents=True, exist_ok=True)
    script = _render_script(gds, regions, out_dir, size)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        subprocess.run([kcmd, "-b", "-nc", "-r", script_path],
                       check=True, capture_output=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"rendered": [], "skipped": "klayout_error", "message": str(e),
                "out_dir": str(out_dir)}
    finally:
        Path(script_path).unlink(missing_ok=True)

    rendered = [str(out_dir / f"{c['cluster']}.png") for c in regions
                if (out_dir / f"{c['cluster']}.png").exists()]
    return {"rendered": rendered, "skipped": None, "out_dir": str(out_dir)}


# --------------------------------------------------------------------------- #
# Helpers to locate the run's artifacts (a project dir -> lyrdb + final GDS).
# --------------------------------------------------------------------------- #

def find_lyrdb(project_dir: Path) -> Path | None:
    p = Path(project_dir) / "drc" / "6_drc.lyrdb"
    return p if p.exists() else None


def find_final_gds(project_dir: Path) -> Path | None:
    """Locate the run's final GDS (6_final.gds in the latest backend RUN), or None."""
    backend = Path(project_dir) / "backend"
    if not backend.is_dir():
        return None
    runs = sorted((d for d in backend.iterdir() if d.is_dir()), reverse=True)
    for run in runs:
        for sub in ("results", "final"):
            g = run / sub / "6_final.gds"
            if g.exists():
                return g
    hits = sorted(backend.rglob("6_final.gds"), reverse=True)
    return hits[0] if hits else None


# --------------------------------------------------------------------------- #
# Escalation hook (additive, env-gated, OFF BY DEFAULT).
# --------------------------------------------------------------------------- #

def vision_enabled() -> bool:
    """Win 4 gate: R2G_VISION_DRC=1 (anything truthy-ish). Unset/0 -> disabled."""
    return os.environ.get("R2G_VISION_DRC", "").strip().lower() in ("1", "true", "yes", "on")


def _is_drc_residual(plan: dict) -> bool:
    """A plan worth rendering for: a DRC check that the text path could not auto-fix —
    a residual status, an explicit residual_reason, or an exhausted strategy catalog."""
    if not plan or plan.get("check") != "drc":
        return False
    if plan.get("status") == "residual":
        return True
    if plan.get("residual_reason"):
        return True
    # catalog_exhausted: a fail with no remaining auto-applicable strategy.
    if plan.get("status") in ("fail", "failed") and not plan.get("strategies"):
        return True
    return False


def attach_vision_artifacts(plan: dict, project_dir, *, margin_um: float = 2.0,
                            size: int = 800) -> dict:
    """ADDITIVE escalation hook. If `R2G_VISION_DRC=1` AND `plan` is a DRC residual /
    low-confidence / catalog-exhausted case, render the violation neighborhoods and
    attach the artifact manifest under `plan["vision"]`. Returns `plan` (mutated).

    OFF BY DEFAULT and FAIL-SOFT: with the env var unset this is a no-op — `plan` is
    returned unchanged so the text fix path is byte-for-byte identical. KLayout is a
    soft dependency (absent -> a manifest that records *why* nothing rendered). Any
    error degrades to a no-op; vision must never break diagnosis.
    """
    if not vision_enabled():
        return plan                       # off by default: no-op, text path unchanged
    if not _is_drc_residual(plan):
        return plan                       # only escalate the cases the text path can't fix
    try:
        proj = Path(project_dir)
        lyrdb = find_lyrdb(proj)
        cov = coordinate_status(lyrdb) if lyrdb else coordinate_status(proj / "drc" / "6_drc.lyrdb")
        manifest: dict = {"enabled": True, "coordinate_status": cov, "clusters": [],
                          "rendered": [], "skipped": None}
        if cov["available"]:
            vios = parse_lyrdb_violations(lyrdb)
            regions = crop_regions(vios, margin_um=margin_um)
            out_dir = proj / "reports" / "drc_vision"
            res = render_regions(find_final_gds(proj) or out_dir / "_missing.gds",
                                 regions, out_dir, size=size)
            manifest["clusters"] = regions
            manifest["rendered"] = res.get("rendered", [])
            manifest["skipped"] = res.get("skipped")
            if res.get("message"):
                manifest["message"] = res["message"]
        else:
            # No coordinates: fall back to a full-GDS preview so the model still sees the
            # layout (honest degradation, documented in signoff-fixing.md).
            manifest["skipped"] = "no_coordinates_full_gds_fallback"
            gds = find_final_gds(proj)
            if gds:
                manifest["fallback_full_gds"] = str(gds)
        plan["vision"] = manifest
    except Exception as e:                 # fail-soft: never break the text fix path
        plan["vision"] = {"enabled": True, "error": str(e), "rendered": []}
    return plan


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Render DRC violation-neighborhood PNGs (Win 4 vision-assisted DRC).")
    ap.add_argument("project_dir")
    ap.add_argument("--margin-um", type=float, default=2.0)
    ap.add_argument("--size", type=int, default=800)
    ap.add_argument("--regions-only", action="store_true",
                    help="print the computed crop regions as JSON; do not invoke KLayout")
    args = ap.parse_args(argv)

    proj = Path(args.project_dir)
    lyrdb = find_lyrdb(proj)
    cov = coordinate_status(lyrdb) if lyrdb else coordinate_status(proj / "drc" / "6_drc.lyrdb")
    vios = parse_lyrdb_violations(lyrdb) if lyrdb else []
    regions = crop_regions(vios, margin_um=args.margin_um)

    if args.regions_only:
        print(json.dumps({"coordinate_status": cov, "regions": regions}, indent=2))
        return 0

    if not cov["available"]:
        print(json.dumps({"coordinate_status": cov, "rendered": [],
                          "skipped": "no_coordinates"}, indent=2))
        return 0

    gds = find_final_gds(proj)
    if not gds:
        print(json.dumps({"rendered": [], "skipped": "no_gds",
                          "message": "no 6_final.gds under backend/; cannot crop"}, indent=2))
        return 0

    res = render_regions(gds, regions, proj / "reports" / "drc_vision", size=args.size)
    res["regions"] = regions
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
