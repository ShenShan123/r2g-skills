#!/usr/bin/env python3
"""
Compare metrics between the last two ORFS runs.
"""
from pathlib import Path
import json
import sys


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def delta(a, b):
    if a is None or b is None:
        return None
    try:
        return b - a
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print('usage: build_run_compare.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project = Path(sys.argv[1])
    out = Path(sys.argv[2])
    history = load_json(project / 'reports' / 'run-history.json', {'runs': []})
    runs = history.get('runs', [])

    result = {'baseline': None, 'current': None, 'delta': {}}

    if len(runs) >= 1:
        result['baseline'] = runs[-1]
        result['current'] = runs[-1]

    if len(runs) >= 2:
        a = runs[-2]
        b = runs[-1]
        result['baseline'] = a
        result['current'] = b
        for key in ['utilization', 'design_area_um2']:
            result['delta'][key] = delta(a.get(key), b.get(key))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
