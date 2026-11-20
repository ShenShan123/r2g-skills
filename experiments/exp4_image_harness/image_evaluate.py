#!/usr/bin/env python3
"""Private image reference from raw DEF/LEF; imports no R2G conversion code.

The reference uses the independent exp4_validation raw-file parsers. In the
evaluator container mount /design, /platform, /reference, /production and /score,
plus /private/{exp4_validation,exp4_image_harness}. Never mount private code or
reference arrays in a model development or production container.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
from pathlib import Path
import pickle
import re
import sys
import zipfile

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp4_validation"))
from gt_raw import (def_header, orient_point, oriented_size, parse_components,
                    parse_lef, parse_nets, parse_pins)
from platforms_v2 import files as platform_files
from image_scoring import FEATURES, score_design


class RasterGrid:
    def __init__(self, dbu, die, sx, sy):
        self.dbu = int(dbu)
        self.die = tuple(int(v) for v in die)
        self.sx, self.sy = int(sx), int(sy)
        x0, y0, x1, y1 = self.die
        if self.dbu <= 0 or sx <= 0 or sy <= 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("Non-positive DEF units, die area or GCell steps")
        self.shape = (math.ceil((x1 - x0) / sx), math.ceil((y1 - y0) / sy))
        self.x_edges = np.minimum(x0 + np.arange(self.shape[0] + 1) * sx, x1) / dbu
        self.y_edges = np.minimum(y0 + np.arange(self.shape[1] + 1) * sy, y1) / dbu
        self.area = np.outer(np.diff(self.x_edges), np.diff(self.y_edges))

    @classmethod
    def from_def(cls, path):
        dbu, die, _ = def_header(path)
        steps = {}
        with open(path) as stream:
            for line in stream:
                match = re.match(r"\s*GCELLGRID\s+([XY])\s+.*?\bSTEP\s+(\d+)", line)
                if match:
                    steps[match[1]] = int(match[2])
                if line.lstrip().startswith("COMPONENTS"):
                    break
        return cls(dbu, die, steps["X"], steps["Y"])

    def point(self, x_um, y_um):
        # Round to 1e-6 DBU BEFORE binning: 14.25 um / 2.85 um is an
        # exact boundary mathematically, despite binary floating-point floor.
        x, y = round(x_um * self.dbu, 6), round(y_um * self.dbu, 6)
        x0, y0, x1, y1 = self.die
        if x < x0 or x > x1 or y < y0 or y > y1:
            return None
        i, j = int((x - x0) // self.sx), int((y - y0) // self.sy)
        return min(i, self.shape[0] - 1), min(j, self.shape[1] - 1)

    def splat(self, array, rectangle, density):
        xa, ya, xb, yb = rectangle
        # Independent vectorized overlap calculation, with no point-bin call.
        ox = np.maximum(0, np.minimum(xb, self.x_edges[1:]) - np.maximum(xa, self.x_edges[:-1]))
        oy = np.maximum(0, np.minimum(yb, self.y_edges[1:]) - np.maximum(ya, self.y_edges[:-1]))
        ix, iy = np.flatnonzero(ox), np.flatnonzero(oy)
        if ix.size and iy.size:
            array[np.ix_(ix, iy)] += density * np.outer(ox[ix], oy[iy])

    def metadata(self):
        return {"dbu": self.dbu, "die_dbu": self.die,
                "step_dbu": [self.sx, self.sy], "shape": list(self.shape), "index_order": "xy"}


def reference_features(place_def, grid, macros):
    dbu, die, _ = def_header(place_def)
    if any(abs(a / dbu - b / grid.dbu) > 1e-9 for a, b in zip(die, grid.die)):
        raise ValueError("Placement and final DEF have different physical die rectangles")
    components, ports = parse_components(place_def), parse_pins(place_def)
    nets, _, _ = parse_nets(place_def)
    if any(net["routed"] for net in nets.values()):
        raise ValueError("Placement feature input contains routed regular nets")
    result = {name: np.zeros(grid.shape, dtype=np.float64) for name in FEATURES}
    for component in components.values():
        macro = macros.get(component["master"])
        if component["x"] is None or not macro or macro["w"] is None or macro["h"] is None:
            continue
        w, h = oriented_size(macro["w"], macro["h"], component["orient"])
        x, y = component["x"] / dbu, component["y"] / dbu
        channel = "macro_region" if macro["cls"] == "BLOCK" else "cell_density"
        grid.splat(result[channel], (x, y, x + w, y + h), 1.0)
    result["cell_density"] /= grid.area
    result["macro_region"] /= grid.area

    def point(instance, pin):
        if instance == "PIN":
            port = ports.get(pin)
            return (port["x"] / dbu, port["y"] / dbu) if port and port["x"] is not None else None
        comp = components.get(instance)
        if not comp or comp["x"] is None:
            return None
        macro = macros.get(comp["master"])
        rectangles = macro.get("pins", {}).get(pin, {}).get("rects", []) if macro else []
        offset = (0.0, 0.0)
        if rectangles:
            px = sum((r[0] + r[2]) / 2 for r in rectangles) / len(rectangles)
            py = sum((r[1] + r[3]) / 2 for r in rectangles) / len(rectangles)
            offset = orient_point(px, py, macro["w"], macro["h"], comp["orient"])
        return comp["x"] / dbu + offset[0], comp["y"] / dbu + offset[1]

    for net in nets.values():
        if net["use"] in ("POWER", "GROUND"):
            continue
        connections = net["conns"] + [("PIN", port) for port in net["ios"]]
        points = [p for p in (point(*connection) for connection in connections) if p is not None]
        if len(points) < 2:
            continue
        xa, ya = np.min(points, axis=0)
        xb, yb = np.max(points, axis=0)
        w, h = max(xb - xa, grid.sx / grid.dbu), max(yb - ya, grid.sy / grid.dbu)
        cx, cy = (xa + xb) / 2, (ya + yb) / 2
        density = (w + h) / (w * h)
        grid.splat(result["rudy"], (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), density)
        for p in points:
            index = grid.point(*p)
            if index is not None:
                result["pin_rudy"][index] += density
                result["pin_density"][index] += 1
    result["rudy"] /= grid.area
    return result


def smooth_reflect(array):
    """Separable unit Gaussian, radius four, independent of scipy implementation."""
    kernel = np.exp(-np.arange(-4, 5, dtype=np.float64) ** 2 / 2)
    kernel /= kernel.sum()
    result = np.asarray(array, dtype=np.float64)
    for axis in (0, 1):
        padding = [(0, 0), (0, 0)]
        padding[axis] = (4, 4)
        padded = np.pad(result, padding, mode="symmetric")
        result = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), axis, padded)
    return result


def reference_labels(final_def, grid, layers):
    _, segments, anomalies = parse_nets(final_def, with_routes=True)
    if anomalies.get("non_manhattan"):
        raise ValueError("This raster contract requires Manhattan route segments")
    demands = [np.zeros(grid.shape, dtype=np.float64), np.zeros(grid.shape, dtype=np.float64)]
    origin = grid.die[:2]
    steps = (grid.sx, grid.sy)
    for rows in segments.values():
        for _, ax, ay, bx, by in rows:
            axis = 0 if ay == by else 1
            start, finish = ((ax, bx) if axis == 0 else (ay, by))
            low, high = sorted((start - origin[axis], finish - origin[axis]))
            fixed = ((ay if axis == 0 else ax) - origin[1 - axis]) // steps[1 - axis]
            if not 0 <= fixed < grid.shape[1 - axis] or low == high:
                continue
            lo = max(0, low // steps[axis])
            hi = min(grid.shape[axis], (high + steps[axis] - 1) // steps[axis])
            bins = np.arange(lo, hi, dtype=np.int64)
            lengths = np.minimum(high, (bins + 1) * steps[axis]) - np.maximum(low, bins * steps[axis])
            if axis == 0:
                demands[axis][bins, fixed] += lengths / grid.dbu
            else:
                demands[axis][fixed, bins] += lengths / grid.dbu
    cell_area = grid.sx * grid.sy / grid.dbu ** 2
    capacities = [sum(cell_area / layer["pitch_um"] for layer in layers.values()
                      if layer["dir"] == direction and layer["pitch_um"] and layer["pitch_um"] > 0)
                  for direction in ("HORIZONTAL", "VERTICAL")]
    if min(capacities) <= 0:
        raise ValueError("Technology must provide positive horizontal and vertical routing capacity")
    raw = np.maximum(demands[0] / capacities[0], demands[1] / capacities[1])
    return {"congestion_raw": raw, "congestion": smooth_reflect(raw)}


def build_reference(design_dir, platform_dir, platform, design):
    design_dir = Path(design_dir)
    layers, macros = parse_lef(platform_files(platform, platform_dir)["lefs"])
    grid = RasterGrid.from_def(design_dir / "6_final.def")
    arrays = {**reference_features(design_dir / "3_place.def", grid, macros),
              **reference_labels(design_dir / "6_final.def", grid, layers)}
    return {"schema": 1, "design": design, "platform": platform, "grid": grid.metadata(), "arrays": arrays}


def save_preview(reference, output_dir, destination):
    """Keep the preselected figure's small arrays after production archiving."""
    channels = ("cell_density", "rudy", "congestion_raw")
    arrays = {"reference_" + name: reference["arrays"][name] for name in channels}
    try:
        with np.load(Path(output_dir) / "labels.npz", allow_pickle=False) as archive:
            predicted = archive["congestion_raw"]
        expected_shape = reference["arrays"]["congestion_raw"].shape
        if predicted.shape == expected_shape:
            arrays["predicted_congestion_raw"] = predicted
            status = "available"
        else:
            status = f"missing: shape {predicted.shape}, expected {expected_shape}"
    except (OSError, ValueError, TypeError, EOFError, KeyError, zipfile.BadZipFile) as exc:
        status = f"missing: {type(exc).__name__}"
    arrays["prediction_status"] = np.asarray(status)
    np.savez_compressed(destination, **arrays)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--truth-only", action="store_true")
    parser.add_argument("--save-preview", action="store_true")
    args = parser.parse_args()
    truth = Path("/reference/image_truth.pkl")
    with open("/reference/image_build.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not truth.exists():
            reference = build_reference("/design", "/platform", args.platform, args.design)
            pending = truth.with_suffix(".building.pkl")
            with pending.open("wb") as stream:
                pickle.dump(reference, stream, protocol=4)
            pending.replace(truth)
    if args.truth_only:
        return
    with truth.open("rb") as stream:
        reference = pickle.load(stream)
    if reference["design"] != args.design or reference["platform"] != args.platform:
        raise ValueError("Reference identity differs from requested design")
    run = json.loads(Path("/production/run.json").read_text())
    result = score_design(reference, Path("/production/out"), run)
    Path("/score/score.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if args.save_preview:
        save_preview(reference, Path("/production/out"), Path("/score/preview.npz"))
    print(json.dumps({key: result[key] for key in ("design", "strict_full_pass", "raster_accuracy", "failures")}))


if __name__ == "__main__":
    main()
