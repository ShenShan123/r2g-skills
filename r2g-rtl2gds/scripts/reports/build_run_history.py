#!/usr/bin/env python3
"""
Build run history from ORFS backend runs.
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


def summarize_run(run_dir: Path) -> dict:
    meta = load_json(run_dir / 'run-meta.json', {})
    has_gds = bool(list(run_dir.rglob('*.gds')))
    has_def = bool(list(run_dir.rglob('*.def')))

    # Try to extract PPA from reports
    ppa = {}
    for rpt_file in run_dir.rglob('*.rpt'):
        text = rpt_file.read_text(encoding='utf-8', errors='ignore')
        if 'Design area' in text:
            import re
            m = re.search(r'Design area\s+([\d.]+)\s+u\^2\s+([\d.]+)%', text)
            if m:
                ppa['design_area_um2'] = float(m.group(1))
                ppa['utilization'] = float(m.group(2)) / 100.0

    return {
        'run': run_dir.name,
        'path': str(run_dir),
        'has_gds': has_gds,
        'has_def': has_def,
        'design_name': meta.get('design_name'),
        'platform': meta.get('platform'),
        'make_status': meta.get('make_status'),
        'utilization': ppa.get('utilization'),
        'design_area_um2': ppa.get('design_area_um2'),
        'status': 'pass' if meta.get('make_status') == 0 else ('fail' if meta.get('make_status') else 'unknown'),
    }


def main():
    if len(sys.argv) < 3:
        print('usage: build_run_history.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project = Path(sys.argv[1])
    out = Path(sys.argv[2])

    backend = project / 'backend'
    history = {'runs': []}

    if backend.exists():
        for run_dir in sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')]):
            history['runs'].append(summarize_run(run_dir))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
