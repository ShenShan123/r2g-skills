#!/usr/bin/env python3
"""V2/V3: rebuild dataset-A graphs for ONE design with the SHIPPED reproduce/ sources.

Runs, in a fresh root, exactly the shipped worker functions:
  build_pilot500.worker  (B graphs, both stages)  -> ROOT1/{stage}/B
  convert_pilot500_views.worker (C-F views)       -> ROOT1/{stage}/{C..F}
  upgrade_release.worker (group-WL v2 labels)     -> ROOT2/{stage}/{B..F}   (= released contract)
Deviations (documented): r2g5.RAW re-pointed with --raw (V3 counterfactuals; default = real raw),
label_core.OLD re-pointed to the shipped runtime_dependencies copy (/data1 unreadable), Python
runtime = graph-venv (frozen py39 env unreadable).
Usage: v2_rebuild.py PROJECT EXT_R2G2 ROOT1 ROOT2 DESIGN [--raw RAWROOT]
"""
import json, sys, time, traceback
from pathlib import Path

project, ext, root1, root2 = (Path(a) for a in sys.argv[1:5])
name = sys.argv[5]
raw = Path(sys.argv[sys.argv.index('--raw') + 1]) if '--raw' in sys.argv else None
sys.path.insert(0, str(project / 'pipeline/scripts'))
sys.path.insert(0, str(project / 'pipeline/group_wirelength_v2'))
import r2g5  # noqa: E402
if raw is not None:
    r2g5.RAW = raw
import build_pilot500, convert_pilot500_views  # noqa: E402
import label_core  # noqa: E402
label_core.OLD = ext
import upgrade_release  # noqa: E402

t0 = time.time(); report = dict(design=name, raw=str(r2g5.RAW))
root1.mkdir(parents=True, exist_ok=True); root2.mkdir(parents=True, exist_ok=True)
r = build_pilot500.worker(name, str(root1)); report['b_build'] = {k: v for k, v in r.items() if k != 'error'}
if not r.get('passed'):
    report['error'] = r.get('error')
else:
    r = convert_pilot500_views.worker(str(root1), name); report['views'] = {k: v for k, v in r.items() if k != 'error'}
    if not r.get('passed'):
        report['error'] = r.get('error')
    else:
        r = upgrade_release.worker(name, str(root1), str(root2))
        report['group_upgrade'] = dict(passed=r.get('passed'), graph_sha256=r.get('graph_sha256'))
        if not r.get('passed'):
            report['error'] = r.get('error')
report['seconds'] = time.time() - t0
(root2 / f'rebuild_{name}.json').write_text(json.dumps(report, indent=1))
print('REBUILD', name, 'OK' if 'error' not in report else 'FAIL', round(report['seconds'], 1))
if 'error' in report:
    print(report['error'][-3000:])
