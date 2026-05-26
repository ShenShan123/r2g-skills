#!/usr/bin/env python3
from pathlib import Path
import json
import sys


def tail(path: Path, n: int = 20):
    if not path.exists():
        return None
    lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
    return lines[-n:]


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    summary = {
        'status': 'PARTIAL',
        'artifacts': {},
        'hints': []
    }

    for rel in ['lint/lint.log', 'sim/sim.log', 'synth/synth.log']:
        p = root / rel
        if p.exists():
            summary['artifacts'][rel] = str(p)
            summary[f'{rel}_tail'] = tail(p)

    if (root / 'synth' / 'synth_output.v').exists():
        summary['status'] = 'SYNTH_PASS'
    elif (root / 'sim' / 'sim.log').exists():
        summary['hints'].append('simulation exists but synthesis netlist missing')

    # Check backend
    backend = root / 'backend'
    if backend.exists():
        runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
        if runs:
            latest = runs[-1]
            if list(latest.rglob('*.gds')):
                summary['status'] = 'PASS'
                summary['hints'].append('GDS generated successfully')
            else:
                summary['status'] = 'BACKEND_FAIL'
                summary['hints'].append('Backend ran but no GDS found')

    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
