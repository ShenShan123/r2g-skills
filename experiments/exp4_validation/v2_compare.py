#!/usr/bin/env python3
"""V2/V3: compare rebuilt graphs with released ones (sha256 + field-level diff).

Usage: v2_compare.py RELEASE_ROOT REBUILD_ROOT DESIGN OUT.json [--inputs-only]
--inputs-only restricts the diff verdict to encoder inputs (x, x_valid_mask, edge_index,
edge_attr, edge_attr_valid_mask, u, u_valid_mask, names, schemas, wl readout mapping) — V3.
"""
import hashlib, json, sys
from pathlib import Path

import torch

INPUT_KEYS = {'x', 'x_valid_mask', 'x_schema', 'names', 'edge_index', 'edge_attr', 'edge_attr_valid_mask', 'edge_schema',
              'u', 'u_valid_mask', 'u_schema', 'wl_source_net_group', 'wl_group_id', 'wl_group_names', 'source_net_id',
              'source_gate_id', 'source_pin_id', 'canonical_net_count', 'canonical_gate_count', 'message_passing_edge_types'}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def eq(a, b):
    if torch.is_tensor(a) and torch.is_tensor(b):
        if a.dtype != b.dtype or a.shape != b.shape:
            return False, f'dtype/shape {a.dtype}{tuple(a.shape)} vs {b.dtype}{tuple(b.shape)}'
        if a.is_floating_point():
            same = torch.equal(torch.isnan(a), torch.isnan(b)) and torch.equal(torch.nan_to_num(a, 0.0), torch.nan_to_num(b, 0.0))
            if not same:
                m = ~(torch.isnan(a) & torch.isnan(b))
                d = (a[m] - b[m]).abs()
                return False, f'values differ: n={int((d > 0).sum())} max_abs={float(d.max()) if d.numel() else 0}'
            return True, ''
        return (torch.equal(a, b), '' if torch.equal(a, b) else f'values differ: n={int((a != b).sum())}')
    if type(a) is not type(b):
        return False, f'type {type(a).__name__} vs {type(b).__name__}'
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False, f'len {len(a)} vs {len(b)}'
        bad = [i for i, (x, y) in enumerate(zip(a, b)) if not eq(x, y)[0]]
        return (not bad, '' if not bad else f'{len(bad)} items differ, first {bad[0]}: {repr(a[bad[0]])[:80]} vs {repr(b[bad[0]])[:80]}')
    if isinstance(a, dict):
        if set(a) != set(b):
            return False, f'keys {sorted(set(a) ^ set(b))[:5]}'
        bad = [k for k in a if not eq(a[k], b[k])[0]]
        return (not bad, '' if not bad else f'keys differ {bad[:5]}')
    return (a == b, '' if a == b else f'{repr(a)[:120]} vs {repr(b)[:120]}')


def field_diff(ga, gb):
    out = {}
    stores = [('global', ga._global_store, gb._global_store)]
    sa = {s._key if hasattr(s, '_key') else None: s for s in ga.stores}
    for t in set(ga.node_types) | set(gb.node_types):
        stores.append((t, ga[t] if t in ga.node_types else {}, gb[t] if t in gb.node_types else {}))
    for t in set(ga.edge_types) | set(gb.edge_types):
        stores.append(('|'.join(t), ga[t] if t in ga.edge_types else {}, gb[t] if t in gb.edge_types else {}))
    for name, a, b in stores:
        ka, kb = set(a.keys()), set(b.keys())
        for k in sorted(ka | kb):
            if k not in ka or k not in kb:
                out[f'{name}.{k}'] = 'missing in ' + ('release' if k not in ka else 'rebuild'); continue
            ok, msg = eq(a[k], b[k])
            if not ok:
                out[f'{name}.{k}'] = msg
    return out


def main():
    rel, reb, design, outp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    inputs_only = '--inputs-only' in sys.argv
    manifest = json.load(open(rel / 'analysis/dataset_manifest.json'))['graph_sha256'] if (rel / 'analysis/dataset_manifest.json').exists() else {}
    res = {}
    for st in ('placement', 'route'):
        for v in 'BCDEF':
            key = f'{st}/{v}/{design}.pt'
            pa, pb = rel / key, reb / key
            r = dict(manifest_sha=manifest.get(key), release_sha=sha(pa), rebuild_sha=sha(pb) if pb.exists() else None)
            r['release_matches_manifest'] = r['manifest_sha'] == r['release_sha'] if manifest else None
            r['byte_identical'] = r['rebuild_sha'] == r['release_sha']
            if not r['byte_identical'] and pb.exists():
                ga = torch.load(pa, map_location='cpu', weights_only=False); gb = torch.load(pb, map_location='cpu', weights_only=False)
                d = field_diff(ga, gb)
                r['fields_differing'] = d
                r['value_identical'] = not d
                inp = {k: m for k, m in d.items() if k.split('.')[-1] in INPUT_KEYS}
                r['input_fields_differing'] = inp
                r['inputs_identical'] = not inp
            else:
                r['value_identical'] = r['byte_identical'] or None
                r['inputs_identical'] = r['byte_identical'] or None
            res[key] = r
    summ = dict(design=design, graphs=len(res), byte_identical=sum(bool(r['byte_identical']) for r in res.values()),
                value_identical=sum(bool(r['value_identical']) for r in res.values()),
                inputs_identical=sum(bool(r['inputs_identical']) for r in res.values()),
                release_matches_manifest=sum(bool(r['release_matches_manifest']) for r in res.values()))
    outp.write_text(json.dumps(dict(summary=summ, graphs=res), indent=1, default=str))
    print('CMP', json.dumps(summ))


if __name__ == '__main__':
    main()
