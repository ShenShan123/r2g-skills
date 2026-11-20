#!/usr/bin/env python3
"""Read-only source/run inventory for the requested unresolved-RTL handoff."""
import argparse
import json
from pathlib import Path


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('host', choices=['203', '216'])
    ap.add_argument('candidates', type=Path)
    a = ap.parse_args()
    candidates = load(a.candidates)['rows']
    result = {'host': a.host, 'rows': {}, 'live_recipe_reruns': {}}
    projects_by_case = {}
    arm_roots = ([Path('/data/yangao/r2g_full_ds_20261101'), Path('/data/yangao/r2g_full_gf180_20261101')]
                 if a.host == '203' else [Path('/proj/workarea/yangao/bare216')])
    wanted = {r['id'] for r in candidates}
    for parent in arm_roots:
        arms = list(parent.glob('deepseek_*')) if a.host == '203' else [parent / 'r2g_gf180_216']
        for arm in arms:
            for case in (arm / 'measure/cases').glob('*'):
                if case.name not in wanted:
                    continue
                for receipt in case.glob('*.job.json'):
                    job = load(receipt) or {}
                    for design in job.get('designs', []):
                        if design.get('project'):
                            projects_by_case.setdefault(case.name, []).append({
                                'host': a.host, 'path': design['project'], 'updated': job.get('updated', 0),
                                'stage': design.get('stage'), 'detail': design.get('detail'),
                                'job_receipt': str(receipt)})
    if a.host == '203':
        base = Path('/data/yangao')
        manifests = {}
        for folder in ['r2g_full_ds_20261101', 'r2g_full_gf180_20261101']:
            for p in sorted((base / folder).glob('full*.json')):
                x = load(p)
                if isinstance(x, dict):
                    for row in x.get('candidates', []):
                        manifests.setdefault(row['id'], []).append(row)
        for row in candidates:
            cid = row['id']
            matches = manifests.get(cid, [])
            available = [m for m in matches if Path(m.get('input_root', '')).is_dir()]
            chosen = available[-1] if available else (matches[-1] if matches else {})
            root = Path(chosen.get('input_root', '/nonexistent'))
            files = [p for p in root.rglob('*') if p.is_file()] if root.is_dir() else []
            result['rows'][cid] = {'candidate': chosen, 'input_exists': root.is_dir(),
                'input_bytes': sum(p.stat().st_size for p in files), 'input_files': len(files)}
        for pl in ['nangate45', 'sky130hd', 'gf180']:
            root = base / 'recipe_reuse_20261108' / pl
            for p in (root / 'measure/cases').glob('*/summary.json'):
                x = load(p) or {}
                designs = x.get('designs') or []
                result['live_recipe_reruns'][p.parent.name] = {
                    'summary_path': str(p),
                    'dual_clean': bool(designs and (designs[0].get('signoff') or {}).get('dual_clean')),
                    'stage': designs[0].get('stage') if designs else None}
    else:
        root = Path('/proj/workarea/yangao')
        roots = {'nangate45': root / 'rerun_nangate45_20261018/runs',
                 'sky130hd': root / 'rerun_sky130hd_20261022/runs',
                 'gf180': root / 'rerun_gf180_20261026/runs'}
        indexed = {pl: {p.name.split('__')[0]: p for p in base.iterdir() if p.is_dir()}
                   for pl, base in roots.items()}
        for row in candidates:
            cid = row['id']; platform, candidate = cid.split('_', 1)
            p = indexed[platform].get(candidate)
            x = load(p / 'result.json') if p else None
            result['rows'][cid] = {'baseline_dir': str(p) if p else None, 'baseline': x,
                'baseline_files': [f.name for f in p.iterdir() if f.is_file()] if p else []}
    # Inventory the recorded R2G stop state, not an LLM-modified project.
    for row in candidates:
        entry = result['rows'].setdefault(row['id'], {})
        projects = []
        options = projects_by_case.get(row['id'], []) or row.get('r2g_projects', [])
        for project in sorted(options, key=lambda x: x.get('updated', 0), reverse=True)[:1]:
            if project['host'] != a.host:
                continue
            p = Path(project['path'])
            if p.is_dir():
                reports = {f.name: load(f) for f in (p / 'reports').glob('*.json')
                           if f.stat().st_size < 2 * 1024 * 1024}
                projects.append({**project, 'path': str(p), 'reports': reports,
                                 'constraints': [str(f.relative_to(p)) for f in (p / 'constraints').glob('*') if f.is_file()]})
        entry['r2g_projects'] = projects
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
