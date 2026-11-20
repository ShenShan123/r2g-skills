"""Read-only ACTIVE geometry comparison; run with klayout -b -r this.py.

Does not run a checker or infer its verdict. Original GDS files are unchanged.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import time
import pya

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "active_geometry"
OUT.mkdir(exist_ok=True)


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def polygon_stats(region, dbu):
    widths, heights, vertices, area_bins = Counter(), Counter(), Counter(), Counter()
    y_bottom, y_top = Counter(), Counter()
    nonrect, holes = 0, 0
    examples = []
    for polygon in region.each():
        box = polygon.bbox()
        widths[box.width()] += 1
        heights[box.height()] += 1
        vertices[polygon.num_points()] += 1
        y_bottom[box.bottom] += 1
        y_top[box.top] += 1
        holes += polygon.holes()
        if polygon.area() != box.area():
            nonrect += 1
            if len(examples) < 8:
                examples.append(str(polygon))
        area_bins[round(polygon.area() * dbu * dbu, 9)] += 1
    return {
        "polygons": region.count(), "bbox_dbu": str(region.bbox()),
        "area_um2_sum": sum(p.area() for p in region.each()) * dbu * dbu,
        "bbox_width_dbu_counts": dict(sorted(widths.items())),
        "bbox_height_dbu_counts": dict(sorted(heights.items())),
        "vertex_count_distribution": dict(sorted(vertices.items())),
        "nonrectangular_count": nonrect, "holes": holes,
        "nonrectangular_examples": examples,
        "distinct_polygon_area_count": len(area_bins),
        "distinct_bottom_y": len(y_bottom), "distinct_top_y": len(y_top),
        "top_y_multiplicity": y_top.most_common(8),
    }


rows = []
for project in ["gcd_pin_window_v2", "multi_uart_strict", "multi_aes", "multi_picorv32"]:
    started = time.monotonic()
    source = ROOT / (project + "_input/drc.gds")
    layout = pya.Layout()
    layout.read(str(source))
    top = layout.top_cell()
    idx = layout.find_layer(11, 0)
    assert idx is not None and top.name == "TOPCELL"
    raw = pya.Region(top.begin_shapes_rec(idx))
    merged = raw.merged()
    cells = []
    type_instances = Counter()
    arrays, direct = 0, 0
    for inst in top.each_inst():
        size = inst.na * inst.nb if inst.is_regular_array() else 1
        type_instances[layout.cell(inst.cell_index).name] += size
        arrays += int(inst.is_regular_array())
        direct += 1
    for cell in layout.each_cell():
        shapes = pya.Region(cell.shapes(idx))
        if shapes.is_empty():
            continue
        cells.append({"cell": cell.name, "top_instances": type_instances[cell.name],
                      "active": polygon_stats(shapes, layout.dbu)})
    row = {
        "project": project, "source": str(source), "source_sha256": hash_file(source),
        "dbu_um": layout.dbu, "cells": len(list(layout.each_cell())),
        "top_direct_reference_records": direct, "top_array_records": arrays,
        "top_expanded_instances": sum(type_instances.values()),
        "top_direct_active_shapes": top.shapes(idx).size(),
        "raw": polygon_stats(raw, layout.dbu),
        "union": polygon_stats(merged, layout.dbu),
        "raw_union_xor_empty": (raw ^ merged).is_empty(),
        "cells_with_active": cells,
        "elapsed_s": time.monotonic() - started,
    }
    rows.append(row)
    (OUT / (project + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    print(json.dumps({"project": project, "cells": row["cells"],
                      "instances": row["top_expanded_instances"],
                      "active_raw": row["raw"]["polygons"], "active_union": row["union"]["polygons"],
                      "raw_nonrect": row["raw"]["nonrectangular_count"],
                      "union_nonrect": row["union"]["nonrectangular_count"],
                      "union_heights": row["union"]["bbox_height_dbu_counts"],
                      "union_vertices": row["union"]["vertex_count_distribution"],
                      "seconds": row["elapsed_s"]}), flush=True)

(OUT / "comparison.json").write_text(json.dumps(rows, indent=2) + "\n")
