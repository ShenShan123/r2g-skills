#!/usr/bin/env python3
"""
Collect results from the latest ORFS backend run.
"""
from pathlib import Path
import json
import sys


def main():
    if len(sys.argv) < 2:
        print('usage: collect_orfs_results.py <project-dir>', file=sys.stderr)
        sys.exit(1)

    project_dir = Path(sys.argv[1])
    backend = project_dir / 'backend'

    result = {
        'project_dir': str(project_dir.resolve()),
        'latest_run': None,
        'gds': None,
        'def': None,
        'odb': None,
        'reports': {},
    }

    if not backend.exists():
        print(json.dumps(result, indent=2))
        return

    runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
    if not runs:
        print(json.dumps(result, indent=2))
        return

    latest = runs[-1]
    result['latest_run'] = str(latest)

    # Find GDS
    for gds in latest.rglob('*.gds'):
        result['gds'] = str(gds)
        break

    # Find DEF
    for def_file in latest.rglob('*.def'):
        result['def'] = str(def_file)
        break

    # Find ODB
    for odb in latest.rglob('*.odb'):
        result['odb'] = str(odb)
        break

    # Collect report files
    flow_log = latest / 'flow.log'
    if flow_log.exists():
        result['reports']['flow.log'] = str(flow_log)

    run_meta = latest / 'run-meta.json'
    if run_meta.exists():
        result['reports']['run-meta.json'] = str(run_meta)

    # ORFS reports
    for rpt in latest.rglob('*.rpt'):
        result['reports'][rpt.name] = str(rpt)

    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
