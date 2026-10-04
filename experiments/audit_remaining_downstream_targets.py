"""Audit hold-slack and post-route RC regression targets before formal training."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import torch


TARGETS = {
    'hold_slack': ('pin', 'hold_slack_ns'),
    'coupling_cap': (('net', 'rc_coupling', 'net'), 'coupling_cap_pF'),
    'effective_resistance': (('pin', 'rc_resistance', 'pin'), 'effective_resistance_ohm'),
}


def node_values(graph, node_type, column):
    store = graph[node_type]
    index = store.y_schema.index(column)
    values = store.y[:, index].double()
    mask = store.y_valid_mask[:, index].bool() & torch.isfinite(values)
    return values[mask]


def edge_values(graph, relation, column):
    store = graph[relation]
    index = store.edge_y_schema.index(column)
    values = store.edge_y[:, index].double()
    mask = store.edge_y_mask[:, index].bool() & torch.isfinite(values)
    return store.edge_index[:, mask].long(), values[mask]


def reverse_pair_audit(edges, values, node_count):
    if torch.any(edges[0] == edges[1]):
        raise ValueError('Coupling supervision contains self edges')
    keys = edges[0] * node_count + edges[1]
    reverse = edges[1] * node_count + edges[0]
    ordered_keys, order = torch.sort(keys)
    positions = torch.searchsorted(ordered_keys, reverse)
    if torch.any(positions >= len(ordered_keys)):
        raise ValueError('Coupling supervision has a missing reverse edge')
    if not torch.equal(ordered_keys[positions], reverse):
        raise ValueError('Coupling supervision has a missing reverse edge')
    reverse_values = values[order[positions]]
    if not torch.allclose(values, reverse_values, rtol=1e-6, atol=1e-12):
        raise ValueError('Reverse coupling edges disagree in value')
    canonical = edges[0] < edges[1]
    if int(canonical.sum()) * 2 != edges.size(1):
        raise ValueError('Coupling supervision is not exactly bidirectional')
    return canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    summary = {
        target: {split: {'designs': 0, 'valid': 0, 'zero_valid_designs': 0,
                         'minimum': None, 'maximum': None}
                 for split in ('train', 'validation', 'test')}
        for target in TARGETS
    }
    for record in manifest['records']:
        graph = torch.load(record['graphs']['route']['path'], map_location='cpu', weights_only=False)
        split = record['split']
        for target, (entity, column) in TARGETS.items():
            row = summary[target][split]
            row['designs'] += 1
            if isinstance(entity, tuple):
                edges, values = edge_values(graph, entity, column)
                if target == 'coupling_cap' and len(values):
                    values = values[reverse_pair_audit(edges, values, graph[entity[0]].num_nodes)]
            else:
                values = node_values(graph, entity, column)
            row['valid'] += len(values)
            if not len(values):
                row['zero_valid_designs'] += 1
                continue
            lo, hi = float(values.min()), float(values.max())
            row['minimum'] = lo if row['minimum'] is None else min(row['minimum'], lo)
            row['maximum'] = hi if row['maximum'] is None else max(row['maximum'], hi)
    result = {
        'schema': 'r2g_remaining_downstream_target_audit_v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS',
        'manifest': str(args.manifest.resolve()),
        'coupling_policy': 'one canonical src<dst record per exactly matching bidirectional pair',
        'edge_task_scope': 'attribute regression conditioned on frozen endpoint pairs; target edges are not message-passing edges',
        'targets': summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
