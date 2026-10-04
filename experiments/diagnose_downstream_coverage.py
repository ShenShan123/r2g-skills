#!/usr/bin/env python3
"""Read-only coverage diagnosis; never fabricate labels or change eligibility."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gnn-node'))
from congestion_audit import section
from stage_data import digest, save_json


def canonical(name):
    return name.replace('\\', '').replace('/', '.').strip()


def components(path):
    result = {}
    for name, body in section(path.read_text(), 'COMPONENTS'):
        key = canonical(name)
        if key in result:
            raise ValueError('Duplicate canonical component: ' + key)
        result[key] = {'master': body.split()[0],
                       'placed': bool(re.search(r'\+\s*(PLACED|FIXED)\s*\(', body))}
    return result


def gate_transition(gates, floor, place, final):
    base = {r['inst_name']: r['master'] for r in gates}
    if len(base) != len(gates):
        raise ValueError('Duplicate gate label rows')
    removed = sorted(set(base).intersection(floor).difference(place))
    absent_floor = sorted(set(base).difference(floor))
    for row in gates:
        obj = final.get(row['inst_name'])
        valid = bool(obj and obj['placed'])
        if bool(int(row['congestion_valid'])) != valid:
            raise ValueError('Final component/label mask mismatch: ' + row['inst_name'])
    return {'base_gates': len(base), 'absent_at_floorplan': absent_floor,
            'absent_at_floorplan_masters': dict(Counter(base[n] for n in absent_floor)),
            'removed_between_floorplan_and_place': removed,
            'removed_masters': dict(Counter(floor[n]['master'] for n in removed)),
            'present_at_place': len(set(base).intersection(place)),
            'present_at_final': len(set(base).intersection(final)),
            'valid_final_targets': sum(int(r['congestion_valid']) for r in gates),
            'mask_matches_final_geometry': True}


def net_summary(rows):
    counts = Counter()
    examples = {}
    for row in rows:
        if int(row['wirelength_valid']):
            reason = 'valid'
        elif int(row['route_segment_net_count']) == 0:
            reason = 'no_stage_net_assignment'
        elif not int(row['route_lineage_valid']):
            reason = 'nonunique_lineage'
        else:
            reason = 'missing_routed_length'
        counts[reason] += 1
        examples.setdefault(reason, [])
        if len(examples[reason]) < 5:
            examples[reason].append(row['net_name'])
    return {'total': len(rows), 'counts': dict(counts), 'examples': examples}


def diagnose(root, row):
    checkpoint = root / 'checkpoints' / (row['task_id'] + '.json')
    method = root / 'methods/r2g-frozen-v3' / row['task_id']
    config_path = method / 'method_config.json'
    cfg = json.loads(config_path.read_text())
    paths = {s: Path(cfg[k]) for s, k in [('floor', 'floorplan_def'),
             ('place', 'place_def'), ('cts', 'cts_def'), ('final', 'label_def')]}
    stages = {s: components(p) for s, p in paths.items()}
    gate_path = method / 'generated/labels/gate_con_IR.csv'
    net_path = method / 'generated/labels/net_wirelength_Cg.csv'
    with gate_path.open(newline='') as f:
        gates = list(csv.DictReader(f))
    with net_path.open(newline='') as f:
        nets = list(csv.DictReader(f))
    transition = gate_transition(gates, stages['floor'], stages['place'], stages['final'])
    log_path = Path(row['run_dir']) / 'logs/3_3_place_gp.log'
    log_evidence = []
    if log_path.is_file():
        for lineno, text in enumerate(log_path.read_text().splitlines(), 1):
            if re.search(r'Removed \d+ buffers', text):
                log_evidence.append({'line': lineno, 'text': text})
    used = [checkpoint, config_path, gate_path, net_path, *paths.values()]
    if log_path.is_file():
        used.append(log_path)
    return {'task_id': row['task_id'], 'gate_transition': transition,
            'stage_components': {s: {'count': len(v),
                'masters': dict(Counter(x['master'] for x in v.values()))} for s, v in stages.items()},
            'wirelength': net_summary(nets),
            'placement_log': {'path': str(log_path), 'evidence': log_evidence},
            'congestion_disposition': 'no_valid_supervision' if not transition['valid_final_targets']
                else 'partial_supervision_keep_valid_mask_pending_cohort_rule',
            'wirelength_disposition': 'partial_supervision_pending_lineage_and_numeric_review',
            'eligibility_finalized': False,
            'input_sha256': {str(p): digest(p) for p in used}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve earlier diagnosis; use a new output directory')
    root = args.root.resolve()
    plan_path = root / 'plan.json'
    plan = json.loads(plan_path.read_text())
    records = []
    for row in plan['rows']:
        path = root / 'checkpoints' / (row['task_id'] + '.json')
        if not path.is_file():
            continue
        state = json.loads(path.read_text())
        if state.get('readiness') == 'review_label_coverage':
            records.append(diagnose(root, row))
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'plan_sha256': digest(plan_path), 'diagnostic_code_sha256': digest(__file__),
              'records': records, 'labels_modified': False, 'training_started': False,
              'limits': 'Coverage diagnosis only, not a proof of all numeric labels or functional equivalence.'}
    save_json(args.output / 'report.json', report)
    lines = ['# Label Coverage Diagnosis', '',
        'Original exports, masks and checkpoints are unchanged. No final cohort cutoff is assigned.', '',
        '| Design | Base gates | Removed before placement | Final valid gates | Valid wirelength nets |',
        '| --- | ---: | ---: | ---: | ---: |']
    for r in records:
        t = r['gate_transition']
        lines.append(f"| {r['task_id']} | {t['base_gates']} | {len(t['removed_between_floorplan_and_place'])} | "
                     f"{t['valid_final_targets']} | {r['wirelength']['counts'].get('valid', 0)}/{r['wirelength']['total']} |")
    lines += ['', 'Buffer deletion can merge canonical nets. Nonunique lineage stays masked; do not assign',
              'the same physical length to multiple original nets without a validated target definition.',
              'Missing gates have no final position. Missing congestion is not zero congestion.',
              'See report.json for exact component names, masters, source hashes and placement log lines.']
    (args.output / 'README.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'reviewed': len(records), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
