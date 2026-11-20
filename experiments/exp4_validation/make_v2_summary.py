#!/usr/bin/env python3
"""Aggregate V2 rebuild comparisons (graphs: two runtimes; images) into v2_summary.json."""
import glob, json, sys
from collections import Counter
from pathlib import Path
W = Path(sys.argv[1]); out = {}
for tag, desc in (('py39', 'Python 3.9.23 + torch 2.5.1+cpu + torch_geometric 2.6.1 + numpy 2.0.2 + scipy 1.13.1 (conda-forge/pip, /data)'),
                  ('main', 'Python 3.13.5 + torch 2.13.0+cpu + torch_geometric 2.8.0 + numpy 2.5.1 + scipy 1.18.1 (graph-venv + scipy)')):
    rows = {}; fields = Counter(); maxdiff = 0.0
    for f in sorted(glob.glob(str(W / f'v2/{tag}/cmp/*.json'))):
        r = json.load(open(f)); rows[r['summary']['design']] = r['summary']
        for g in r['graphs'].values():
            for k, m in (g.get('fields_differing') or {}).items():
                fields[k] += 1
                if 'max_abs=' in m:
                    maxdiff = max(maxdiff, float(m.split('max_abs=')[1]))
    out[tag] = dict(runtime=desc, designs=len(rows), graphs=sum(v['graphs'] for v in rows.values()),
                    byte_identical=sum(v['byte_identical'] for v in rows.values()),
                    value_identical=sum(v['value_identical'] for v in rows.values()),
                    inputs_identical=sum(v['inputs_identical'] for v in rows.values()),
                    release_matches_manifest=sum(v['release_matches_manifest'] for v in rows.values()),
                    differing_fields=dict(fields), max_abs_value_difference=maxdiff, per_design=rows)
im = json.load(open(W / 'v2img/v2img_cmp.json'))
out['images'] = dict(runtime='shipped dump_design.py/ir_design.tcl on memlab203 (OpenROAD 26Q3-318-g6b9d7fb806, ORFS nangate45 platform) + shipped rasterize.py on 208 with the original r2g-cnn venv (numpy 2.5.4)',
                     designs=len(im), byte_identical=sum(v['byte_identical'] for v in im.values()),
                     value_identical=sum(v['value_identical'] for v in im.values()), per_design=im)
(W / 'v2_summary.json').write_text(json.dumps(out, indent=1))
print(json.dumps({k: {kk: v[kk] for kk in v if kk not in ('per_design',)} for k, v in out.items()}, indent=1))
