"""Measure old/new label differences without rewriting either dataset."""
import argparse
import json
from pathlib import Path

import torch

from stage_data import digest, labels, load_graph, save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-manifest', required=True)
    parser.add_argument('--new-manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).exists():
        raise FileExistsError('Comparison output exists')
    old = {r['design_id']: r for r in json.loads(Path(args.old_manifest).read_text())['records']}
    new = json.loads(Path(args.new_manifest).read_text())['records']
    results = []
    for record in new:
        key = record['design_id']
        a, b = load_graph(old[key], 'route'), load_graph(record, 'route')
        result = {'design_id': key}
        for target, entity, name_field in [('wirelength', 'net', 'net_name'),
                                          ('congestion', 'gate', 'inst_name'),
                                          ('ground_cap', 'net', 'net_name')]:
            if list(a[entity][name_field]) != list(b[entity][name_field]):
                raise ValueError('Entity order changed')
            av, am = labels(a, target)
            bv, bm = labels(b, target)
            valid = am & bm
            changed = valid & ~torch.isclose(av, bv, rtol=1e-6, atol=1e-8)
            ids = torch.where(changed)[0].tolist()
            ids.sort(key=lambda i: abs(float(av[i]-bv[i])), reverse=True)
            names = a[entity][name_field]
            result[target] = {'joint_valid': int(valid.sum()), 'mask_changes': int((am != bm).sum()),
                              'changed': len(ids), 'max_abs_change': float((av[valid]-bv[valid]).abs().max()) if valid.any() else None,
                              'largest_changes': [{'name': names[i], 'old': float(av[i]), 'new': float(bv[i])} for i in ids[:3]]}
        results.append(result)
    save_json(args.output, {'scope': 'same six pilot designs, route labels only; not an Experiment 4 rescore',
                            'old_manifest_sha256': digest(args.old_manifest),
                            'new_manifest_sha256': digest(args.new_manifest), 'records': results})
    print(json.dumps({t: sum(r[t]['changed'] for r in results) for t in ('wirelength', 'congestion', 'ground_cap')}))


if __name__ == '__main__':
    main()
