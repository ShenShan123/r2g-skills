#!/usr/bin/env python3
"""Check that the isolated geometry fix changes only intended node features."""
import argparse
from collections import Counter
import json
from pathlib import Path

import torch

from experiment4_semantic_audit import CORE_EDGES, NODES, STAGES, digest, save

ALLOWED_NODE_COLUMNS = {
    'gate': {'center_x_um', 'center_y_um', 'center_x_normalized', 'center_y_normalized',
             'congestion_pin_density', 'congestion_net_density', 'congestion_rudy', 'congestion_rudy_pin'},
    'pin': {'pin_x_um', 'pin_y_um', 'pin_x_normalized', 'pin_y_normalized',
            'distance_to_die_left_um', 'distance_to_die_right_um',
            'distance_to_die_bottom_um', 'distance_to_die_top_um'},
    'net': {'net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um',
            'stage_segment_total_hpwl_um', 'stage_segment_max_hpwl_um',
            'stage_segment_mean_hpwl_um'},
    'io_pin': set(),
}


def tensor_same(a, b):
    return a.shape == b.shape and a.dtype == b.dtype and torch.allclose(a, b, atol=0, rtol=0, equal_nan=True)


def compare_graphs(old, new):
    errors, changes = [], {}
    for node in NODES:
        a, b = old[node], new[node]
        for field in ('inst_name', 'pin_name', 'net_name', 'iopin_name', 'x_schema', 'y_schema'):
            if field in a or field in b:
                if a.get(field) != b.get(field):
                    errors.append(node + '.' + field)
        for field in ('y', 'y_valid_mask'):
            if not tensor_same(a[field], b[field]):
                errors.append(node + '.' + field)
        if a.x.shape != b.x.shape or a.x.dtype != b.x.dtype or list(a.x_schema) != list(b.x_schema):
            errors.append(node + '.x_shape_or_schema')
            continue
        for i, field in enumerate(a.x_schema):
            changed = ~torch.isclose(a.x[:, i], b.x[:, i], atol=0, rtol=0, equal_nan=True)
            if changed.any():
                changes[node + '.' + field] = int(changed.sum())
                if field not in ALLOWED_NODE_COLUMNS[node]:
                    errors.append(node + '.' + field)
    for relation in CORE_EDGES:
        a, b = old[relation], new[relation]
        for field in ('edge_index', 'edge_attr', 'y', 'y_valid_mask'):
            if field in a or field in b:
                if field not in a or field not in b or not tensor_same(a[field], b[field]):
                    errors.append('|'.join(relation) + '.' + field)
    if list(old.global_feature_schema) != list(new.global_feature_schema) or not tensor_same(old.global_features, new.global_features):
        errors.append('global_features')
    return {'status': 'PASS' if not errors else 'FAIL', 'unexpected_changes': errors, 'node_changes': changes,
            'scope': 'node identity, labels/masks, global features and core edges are invariant; only declared D12/D13-derived feature columns may change; geometric auxiliary edges may change'}


def main(args):
    torch.set_num_threads(1)
    root = args.root
    plan = json.loads((root / 'validation_plan.json').read_text())
    old_root = Path(plan['source'])
    rebuild = json.loads((root / 'rebuild_summary.json').read_text())
    new_audit_path = args.new_audit or root / 'audit/summary.json'
    old_audit_path = args.old_audit or root / 'old_audit_v0_2/summary.json'
    new_audit = json.loads(new_audit_path.read_text())
    old_audit = json.loads(old_audit_path.read_text())
    if new_audit['policy'] != old_audit['policy']:
        raise ValueError('audit policies differ')
    if any(r['status'] != 'completed' for r in rebuild['rows']) or len(rebuild['rows']) != len(plan['tasks']):
        raise ValueError('rebuild incomplete')
    rows = []
    for task in plan['tasks']:
        for stage in STAGES:
            relative = Path(task) / 'generated/stages' / stage / 'heterograph.pt'
            old = old_root / 'methods/r2g-frozen-v3' / relative
            new = root / 'methods/r2g-geometry-fix' / relative
            result = compare_graphs(torch.load(old, weights_only=False, map_location='cpu'),
                                    torch.load(new, weights_only=False, map_location='cpu'))
            rows.append(dict(task=task, stage=stage, old_sha256=digest(old), new_sha256=digest(new), **result))
    method_rows = [r for r in new_audit['rows'] if r.get('method') == 'r2g-geometry-fix']
    if len(method_rows) != len(plan['tasks']):
        raise ValueError('independent audit incomplete')
    counts = {g: dict(Counter(r['groups'][g]['status'] for r in method_rows)) for g in method_rows[0]['groups']}
    outcome = {'role': plan['role'], 'audit_policy': new_audit['policy'], 'task_count': len(plan['tasks']),
               'independent_audit_counts': counts, 'change_scope_counts': dict(Counter(r['status'] for r in rows)),
               'rows': rows, 'full_semantics': 'NOT_VERIFIED',
               'evidence_sha256': {str(p): digest(p) for p in
                    (root / 'validation_plan.json', root / 'rebuild_summary.json', new_audit_path, old_audit_path)}}
    save(root / 'geometry_validation_report.json', outcome)
    print(json.dumps({k: outcome[k] for k in ('task_count', 'independent_audit_counts', 'change_scope_counts')}, indent=2))
    if any(r['status'] != 'PASS' for r in rows) or any(c.get('FAIL', 0) or c.get('UNASSESSABLE', 0) for c in counts.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--new-audit', type=Path)
    p.add_argument('--old-audit', type=Path)
    args = p.parse_args()
    main(args)
