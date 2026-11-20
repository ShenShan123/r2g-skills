"""Prepare any explicitly named top for the unchanged official ASAP7 decks."""
from pathlib import Path
import hashlib
import json
import os
import pya

root = Path(__file__).resolve().parent
project_name = os.environ['QUAL_PROJECT']
top_name = os.environ['QUAL_TOP']
project = root / project_name
run = sorted((project / 'backend').glob('RUN_*'))[-1]
source = run / 'results/6_final.gds'
out = root / (project_name + '_input')
out.mkdir(exist_ok=True)
layout = pya.Layout()
layout.read(str(source))
top = layout.cell(top_name)
assert top is not None and top.is_top(), f'Missing layout top {top_name}'
top.name = 'TOPCELL'
layout.write(str(out / 'drc.gds'))
top.name = top_name
labels = 0
for metal in (19, 20, 30, 40, 50, 60, 70, 80, 90):
    index = layout.find_layer(metal, 2)
    if index is None:
        continue
    for shape in list(top.shapes(index).each()):
        if shape.is_text():
            top.shapes(layout.layer(metal, 251)).insert(shape.text)
            labels += 1
layout.write(str(out / 'lvs.gds'))
(out / 'provenance.json').write_text(json.dumps({
    'original_gds': str(source), 'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'geometry_scale': 1, 'top_level_pin_labels_copied': labels,
    'drc_top_name': 'TOPCELL', 'lvs_top_name': top_name,
}, indent=2) + '\n')
print(str(out))
