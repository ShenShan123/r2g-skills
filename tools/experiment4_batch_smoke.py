#!/usr/bin/env python3
"""CPU-only batching and masked message-passing smoke, not a learning benchmark."""
import argparse
import json
from pathlib import Path
import time

import torch
from torch_geometric.data import Batch

from experiment4_semantic_audit import CORE_EDGES, NODES, STAGES, digest, save


def validate_batch(graphs):
    for node in NODES:
        if list(graphs[0][node].x_schema) != list(graphs[1][node].x_schema):
            raise ValueError('different feature schema across designs')
        if list(graphs[0][node].y_schema) != list(graphs[1][node].y_schema):
            raise ValueError('different label schema across designs')
    batch = Batch.from_data_list(graphs)
    unbatched = batch.to_data_list()
    for original, restored in zip(graphs, unbatched):
        for node in NODES:
            for field in ('x', 'y', 'y_valid_mask'):
                a, b = original[node][field], restored[node][field]
                if a.shape != b.shape or not torch.allclose(a, b, equal_nan=True):
                    raise ValueError('batch round-trip changed node values')
        for rel in CORE_EDGES:
            if not torch.equal(original[rel].edge_index, restored[rel].edge_index):
                raise ValueError('batch round-trip changed core edges')
    parameters, hidden = [], {}
    for node in NODES:
        x = batch[node].x.float()
        if x.ndim != 2 or torch.isinf(x).any():
            raise ValueError('invalid features')
        valid = torch.isfinite(x)
        # Missingness is supplied separately; labels are never used as features.
        values = torch.where(valid, x, 0.)
        scale = values.abs().amax(dim=0).clamp_min(1.) if len(values) else torch.ones(x.shape[1])
        features = torch.cat([values / scale, valid.float()], dim=1)
        layer = torch.nn.Linear(features.shape[1], 8)
        parameters.extend(layer.parameters())
        hidden[node] = torch.tanh(layer(features))
    for rel in CORE_EDGES:
        src, dst = rel[0], rel[2]
        index = batch[rel].edge_index
        if index.dtype != torch.int64 or index.ndim != 2 or index.shape[0] != 2:
            raise ValueError('invalid edge index')
        if index.numel() and (index.min() < 0 or index[0].max() >= len(hidden[src]) or index[1].max() >= len(hidden[dst])):
            raise ValueError('out-of-range batch edge')
        if not torch.equal(batch[src].batch[index[0]], batch[dst].batch[index[1]]):
            raise ValueError('batch edge joins different designs')
        messages = torch.zeros_like(hidden[dst]).index_add(0, index[1], hidden[src][index[0]])
        degree = torch.zeros(len(hidden[dst])).index_add(0, index[1], torch.ones(index.shape[1]))
        hidden[dst] = torch.tanh(hidden[dst] + messages / degree.clamp_min(1.)[:, None])
    losses, label_count = [], 0
    for node in NODES:
        y, mask = batch[node].y.float(), batch[node].y_valid_mask
        if y.ndim != 2 or mask.dtype != torch.bool or y.shape != mask.shape or not torch.equal(torch.isfinite(y), mask):
            raise ValueError('invalid target masks')
        if mask.any():
            readout = torch.nn.Linear(8, y.shape[1])
            parameters.extend(readout.parameters())
            selected = y[mask]
            target = selected / selected.abs().max().clamp_min(1.)
            losses.append(torch.nn.functional.mse_loss(readout(hidden[node])[mask], target))
            label_count += int(mask.sum())
    if not losses:
        return {'status': 'UNASSESSABLE', 'batch_status': 'PASS', 'forward_status': 'PASS',
                'backward_status': 'UNASSESSABLE', 'reason': 'no finite labels for backward check'}
    loss = sum(losses)
    loss.backward()
    grads = [p.grad for p in parameters if p.grad is not None]
    if not torch.isfinite(loss) or not grads or any(not torch.isfinite(g).all() for g in grads):
        raise ValueError('nonfinite loss or gradients')
    return {'status': 'PASS', 'batch_status': 'PASS', 'forward_status': 'PASS', 'backward_status': 'PASS',
            'valid_label_count': label_count,
            'nodes': {n: len(hidden[n]) for n in NODES}}


def main(args):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    cohort = json.loads((args.campaign / 'cohort.json').read_text())
    tasks = sorted(t['task_id'] for t in cohort['splits']['hidden_test'])
    if len(tasks) % 2:
        raise ValueError('pairing requires an even task count')
    rows = []
    for method in ('r2g-frozen-v3', 'llm-gpt-frozen', 'llm-claude-frozen', 'llm-qwen-frozen'):
        for stage in STAGES:
            for a, b in zip(tasks[::2], tasks[1::2]):
                paths = [args.campaign / 'methods' / method / t / 'generated/stages' / stage / 'heterograph.pt' for t in (a, b)]
                started = time.monotonic()
                binding = {str(p): digest(p) if p.exists() else None for p in paths}
                try:
                    result = validate_batch([torch.load(p, weights_only=False, map_location='cpu') for p in paths])
                except Exception as exc:
                    result = {'status': 'FAIL', 'error': str(exc)[:500]}
                rows.append(dict(method=method, stage=stage, tasks=[a, b], inputs=binding,
                                 elapsed_seconds=time.monotonic() - started, **result))
    save(args.output, {'role': 'retrospective usability smoke; not model accuracy',
         'policy': 'same finite-mask encoding, per-batch scaling, 8-dimensional message pass over core edges only',
         'excluded_from_message_passing': 'all auxiliary edges, especially label-derived edges',
         'source_sha256': digest(__file__), 'cohort_sha256': digest(args.campaign / 'cohort.json'),
         'rows': rows})
    from collections import Counter
    print(json.dumps({m: dict(Counter(r['status'] for r in rows if r['method'] == m))
                      for m in sorted({r['method'] for r in rows})}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    main(p.parse_args())
