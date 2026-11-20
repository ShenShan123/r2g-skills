from pathlib import Path
from collections import Counter
import json
import pya

root = Path(__file__).resolve().parents[1]
for project in ["gcd_pin_window_v2", "multi_uart_strict", "multi_aes", "multi_picorv32"]:
    ly = pya.Layout()
    ly.read(str(root / (project + "_input/drc.gds")))
    r = pya.Region(ly.top_cell().begin_shapes_rec(ly.find_layer(11, 0))).merged()
    patterns = Counter()
    for p in r.each():
        b = p.bbox()
        patterns[str(p.transformed(pya.Trans(-b.left, -b.bottom)))] += 1
    out = {"project": project, "unique_translation_normalized_active_shapes": len(patterns),
           "polygons": r.count(), "patterns": dict(patterns)}
    (Path(__file__).resolve().parent / "active_geometry" / (project + "_patterns.json")).write_text(json.dumps(out, indent=2) + "\n")
    print(project, len(patterns), r.count(), flush=True)
