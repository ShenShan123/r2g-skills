"""Union FIN/GATE at top level while preserving all other geometry and hierarchy."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import time
import pya

root = Path(__file__).resolve().parent.parent
project = os.environ.get("QUAL_PROJECT", "multi_aes")
source = root / f"{project}_input/drc.gds"
out = root / f"{project}_feol_hier_input"
out.mkdir(exist_ok=True)
start = time.monotonic()
a = pya.Layout()
a.read(str(source))
b = pya.Layout()
b.read(str(source))
top = b.top_cell()
assert top.name == "TOPCELL"
summary = {}
for name, layer, axis in [("FIN", 2, "width"), ("GATE", 7, "height")]:
    idx = b.find_layer(layer, 0)
    original = pya.Region(top.begin_shapes_rec(idx))
    original_count = original.count()
    merged = original.merged()
    lengths = Counter(getattr(p.bbox(), axis)() for p in merged.each())
    assert len(lengths) == 1, (name, lengths)
    for cell in b.each_cell():
        cell.shapes(idx).clear()
    top.shapes(idx).insert(merged)
    summary[name] = {"source_polygons": original_count, "top_union_polygons": merged.count(),
                     "equal_length_axis": axis, "length_dbu_counts": dict(lengths)}
dest = out / "drc.gds"
b.write(str(dest))
b = pya.Layout()
b.read(str(dest))
infos = lambda layout: {(layout.get_info(i).layer, layout.get_info(i).datatype)
                        for i in layout.layer_indices()}
assert infos(a) == infos(b)
checks = []
for layer, datatype in sorted(infos(a)):
    left = pya.Region(a.top_cell().begin_shapes_rec(a.find_layer(layer, datatype)))
    right = pya.Region(b.top_cell().begin_shapes_rec(b.find_layer(layer, datatype)))
    assert (left ^ right).is_empty(), (layer, datatype)
    checks.append({"layer": layer, "datatype": datatype, "xor_polygons": 0})

def instances(layout):
    return sorted((cell.name, layout.cell(inst.cell_index).name, str(inst.cplx_trans),
                   str(inst.a), str(inst.b), inst.na, inst.nb)
                  for cell in layout.each_cell() for inst in cell.each_inst())

assert instances(a) == instances(b)
record = {
    "result": "PASS: geometric equivalence, not a DRC verdict",
    "project": project, "source": str(source), "normalized_gds": str(dest),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "normalized_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
    "all_drawing_layers_xor_empty": True, "layers": checks,
    "instances_and_transforms_unchanged": True,
    "cell_count_before": len(list(a.each_cell())), "cell_count_after": len(list(b.each_cell())),
    "dbu_um": b.dbu, "normalization": summary,
    "operation": "Remove FIN/GATE shapes from all cells and insert their exact union at TOPCELL; no other layer or instance modified.",
    "official_basis": "ASAP7 DRM 201207a printed pages 18 and 20: FIN equal x length and GATE not cut by GCUT equal y length for hierarchical DRC.",
    "elapsed_s": time.monotonic() - start,
}
(out / "geometry_verification.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({"project": project, "normalization": summary,
                  "layers_checked": len(checks), "cells": record["cell_count_after"],
                  "elapsed_s": record["elapsed_s"]}))
