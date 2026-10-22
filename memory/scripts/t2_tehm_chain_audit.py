#!/usr/bin/env python3
"""Read-only inventory of an executed NB-first Table 2 memory consumer.

This is an alignment/audit tool, NOT a repair runner or a knowledge promoter.
It never writes into the package, calls a model, or starts EDA. An optional
probe runs the original router and rule index from EACH fold's frozen code.
Probe output is explicitly counterfactual, never an execution receipt.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import os


def load(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_store(path):
    """Missing tables are UNKNOWN, not measured zero; never perform DDL."""
    path = Path(path).resolve()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: conn.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0]
                  for t in sorted(tables) if t.startswith('tehm_')}
        required = ('tehm_attempts', 'tehm_transitions', 'tehm_views', 'tehm_rules',
                    'tehm_mechanism_knowledge', 'tehm_causal_paths', 'tehm_assets')
        counts.update({t: None for t in required if t not in tables})
        rules = [dict(r) for r in conn.execute(
            'SELECT rule_id,validity_status,before_pattern_json,validity_profile_json FROM tehm_rules')]
        views = dict(conn.execute('SELECT view_type,count(*) FROM tehm_views GROUP BY view_type'))
        return {'counts': counts, 'views': views, 'rules': rules}
    finally:
        conn.close()


PROBE = r'''
import json,sqlite3,sys
from pathlib import Path
fold=Path(sys.argv[1]);sys.path[:0]=[str(fold/'code/memory'),str(fold/'code/memory/scripts')]
from contracts import MemoryQuery
from tehm.retrieval.index import build_index
from tehm.retrieval.memory_router import route_memory
from tehm import evidence
db=fold/'store/tehm.sqlite'
conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
conn.row_factory=sqlite3.Row;conn.execute('pragma query_only=ON')
index=build_index(conn,lifecycle_statuses=frozenset({'candidate','promoted'}))
query=MemoryQuery(query_plan={'check':'drc','design_id':fold.name,'platform':'sky130hd'})
decision=route_memory(conn,query,persist_state=False,commit=False)
print(json.dumps({'runtime_index_size':len(index),'index_rejected':index.rejected,
 'rule_evidence':{r[0]:evidence.rule_evidence(conn,r[0]) for r in conn.execute('select rule_id from tehm_rules')},
 'synthetic_query':query.query_plan,'decision':decision.to_dict(),
 'scope':'OFFLINE_SYNTHETIC_QUERY_NOT_A_HISTORICAL_ROUTE_RECEIPT'}))
conn.close()
'''


def frozen_probe(fold, python):
    proc = subprocess.run([str(python), '-c', PROBE, str(fold)], check=True,
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                               'TEHM_EVIDENCE_CALIBER': 'main',
                               'TEHM_D1_REQUIRE_VERIFIED_POSITIVE': '1', 'CUDA_VISIBLE_DEVICES': ''})
    return json.loads(proc.stdout)


def protected_files(root, result):
    """Bind results, raw episodes/costs, stores, representations and private code."""
    paths = {root/'table2-results.json', root/'table2-matrix.csv', root/'queue-registration.json',
             root/'dispatch-state.json'}
    for entry in result['episodes']:
        ep = Path(entry['episode'])
        paths.add(ep)
        for f in ep.parent.rglob('*'):
            if f.is_file() and (f.parent == ep.parent or 'reports' in f.relative_to(ep.parent).parts
                               or '.tehm-store' in f.relative_to(ep.parent).parts):
                paths.add(f)
    for fold in (root/'folds').iterdir():
        if not fold.is_dir():
            continue
        for f in fold.rglob('*'):
            if not f.is_file() or '__pycache__' in f.parts:
                continue
            rel = f.relative_to(fold)
            if rel.parts[0] != 'episodes':
                paths.add(f)
    return {str(p): sha(p) for p in sorted(paths) if p.is_file()}


def verify_protected(protected):
    changed = [p for p, h in protected.items() if not Path(p).is_file() or sha(p) != h]
    if changed:
        raise ValueError('Protected evidence changed: ' + ', '.join(changed[:5]))


def episode_summary(entry):
    p = Path(entry['episode'])
    record = load(p)
    turns = record.get('repair_turns', [])
    events = rows(p.parent/'reports/resource_ledger.jsonl')
    log = p.parent/'reports/fix_log.jsonl'
    fixes = rows(log) if log.is_file() else []
    memory_fixes = [r for r in fixes if str(r.get('strategy', '')).startswith('tehm_')
                    or r.get('memory_rule')]
    requests = sorted(p.parent.glob('repair-turn-*.try0.request.json'))
    injected = []
    for f in requests:
        body = load(f)
        injected.append(any('Frozen historical memory (not current-task evidence):' in m.get('content', '')
                            for m in body.get('messages', [])))
    return {'design': entry['design'], 'status': record['status'],
            'repair_generations': len(turns),
            'flows_launched_by_repair_generations': sum(bool(t.get('flow_launched')) for t in turns),
            'turn_statuses': dict(collections.Counter(t.get('status') for t in turns)),
            'executed_memory_fix_rows': len(memory_fixes),
            'route_event_count': sum('routing' in str(e.get('type', '')).lower()
                                     or e.get('type') in ('no_skill', 'memory_route') for e in events),
            'retrieval_event_count': sum(e.get('type') == 'memory_retrieval' for e in events),
            'historical_prompt_injection': injected,
            'source': str(p)}


def audit(root, *, probe_python=None):
    root = Path(root).resolve()
    result = load(root/'table2-results.json')
    if not result.get('complete') or len(result['episodes']) != 56:
        raise ValueError('Audit requires the complete 56-cell NB-first batch')
    protected = protected_files(root, result)
    folds = []
    for fold in sorted((root/'folds').iterdir()):
        if not fold.is_dir():
            continue
        units = rows(fold/'shared/shared_units.jsonl')
        excluded = load(fold/'exclusions.json')
        expected = load(fold/'shared/manifest.json')['units_sha256']
        if sha(fold/'shared/shared_units.jsonl') != expected:
            raise ValueError('Shared history digest mismatch: ' + fold.name)
        if any(u['source_handoff_id'] in excluded['excluded_origins'] for u in units):
            raise ValueError('Excluded source present: ' + fold.name)
        store = read_store(fold/'store/tehm.sqlite')
        controls = {}
        for arm, key in (('RET', 'trajectories'), ('RECIPE', 'recipes'), ('EXPEL', 'rules'),
                         ('AWM', 'workflows'), ('AMEM', 'notes')):
            c = load(fold/'controls'/f'{arm}.json')
            if c.get('units_sha256') != expected:
                raise ValueError('Arm does not bind shared history: ' + fold.name + '/' + arm)
            controls[arm] = {'representation': key, 'entries': len(c[key]),
                             'trajectory_entries': len(c.get('trajectories', []))}
        item = {'target': fold.name, 'units': len(units),
                'admitted_positive_units': sum(u['shared_label']['admitted'] for u in units),
                'origins': sorted({u['source_handoff_id'] for u in units}),
                'shared_sha256': expected, 'store': store, 'controls': controls,
                'import_parity': load(fold/'store/store_receipt.json')['parity_passed']}
        if probe_python is not None:
            item['frozen_offline_probe'] = frozen_probe(fold, probe_python)
        folds.append(item)
    episodes = [episode_summary(e) for e in result['episodes'] if e['arm'] == 'TM0']
    verify_protected(protected)
    return {'schema': 'tehm-executed-chain-audit-v1', 'package': str(root),
            'model_requests_started': 0, 'eda_flows_started': 0,
            'historical_results_modified': False, 'protected_files_unchanged': True,
            'full_agent_chain_validated': False,
            'boundary': 'An alignment audit, not a repaired runtime, new model result, or promotion receipt.',
            'folds': folds, 'tehm_episodes': episodes, 'protected_sha256': protected}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--probe-python', type=Path)
    args = ap.parse_args()
    root, out = args.root.resolve(), args.out.resolve()
    if out.is_relative_to(root) or out.exists():
        raise ValueError('Output must be new and outside the retained experiment package')
    report = audit(root, probe_python=args.probe_python)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as f:
        json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
    print(json.dumps({'receipt': str(out), 'sha256': sha(out), 'protected': len(report['protected_sha256']),
                      'folds': len(report['folds']), 'episodes': len(report['tehm_episodes'])}))


if __name__ == '__main__':
    main()
