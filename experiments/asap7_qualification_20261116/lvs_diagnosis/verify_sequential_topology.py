"""Compare sequential transistor networks without relying on Liberty semantics.

Port names, device model, L/W/nfin, gate and bulk terminals are fixed. MOS
source/drain terminals are interchangeable, as in the official LVS setup.
Only strictly parallel MOS devices are merged before exact graph comparison.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from itertools import permutations, product
import hashlib
import json
import re
from derive_oai21_reference import HERE, ROOT, circuits


def number(value):
    match = re.fullmatch(r'([+-]?[\d.]+(?:[eE][+-]?\d+)?)([a-zA-Z]*)', value)
    assert match, value
    scale = {'': '1', 'n': '1e-9', 'u': '1e-6', 'p': '1e-12', 'm': '1e-3'}
    return Decimal(match[1]) * Decimal(scale[match[2].lower()])


def merged_mos(cell):
    merged = {}
    for line in cell['lines']:
        fields = line.split('$', 1)[0].split()
        assert fields[0][0].upper() == 'M', fields
        d, g, s, b = (x.upper() for x in fields[1:5])
        params = {k.lower(): number(v) for k, v in (p.split('=') for p in fields[6:])}
        assert set(params) == {'l', 'w', 'nfin'}
        key = (fields[5].lower(), params['l'], g, b, min(d, s), max(d, s))
        if key not in merged:
            merged[key] = {'model': key[0], 'l': params['l'], 'g': g, 'b': b,
                           'd': min(d, s), 's': max(d, s), 'w': Decimal(0), 'nfin': Decimal(0), 'names': []}
        merged[key]['w'] += params['w']
        merged[key]['nfin'] += params['nfin']
        merged[key]['names'].append(fields[0])
    return list(merged.values())


def graph(mos, ports):
    attrs, edges = {}, defaultdict(list)
    for d in mos:
        for role in ('d', 'g', 's', 'b'):
            net = ('net', d[role])
            attrs[net] = ('net', d[role] if d[role] in ports else 'internal')
    for i, d in enumerate(mos):
        device = ('device', i)
        attrs[device] = ('mos', d['model'], str(d['l'].normalize()), str(d['w'].normalize()), str(d['nfin'].normalize()))
        for role in ('d', 'g', 's', 'b'):
            net = ('net', d[role])
            edge_role = 'diffusion' if role in ('d', 's') else role
            edges[device].append((edge_role, net))
            edges[net].append((edge_role, device))
    return attrs, edges


def paired_colors(a, b):
    attrs = [{('side', i, key): value for key, value in g[0].items()} for i, g in enumerate((a, b))]
    all_attrs = attrs[0] | attrs[1]
    values = {v: i for i, v in enumerate(sorted(set(all_attrs.values())))}
    colors = {k: values[v] for k, v in all_attrs.items()}
    edges = {('side', i, n): [(r, ('side', i, dest)) for r, dest in es] for i, g in enumerate((a, b)) for n, es in g[1].items()}
    for _ in range(len(colors)):
        signatures = {k: (colors[k], tuple(sorted((r, colors[v]) for r, v in edges[k]))) for k in colors}
        palette = {v: i for i, v in enumerate(sorted(set(signatures.values())))}
        new = {k: palette[v] for k, v in signatures.items()}
        if len(set(new.values())) == len(set(colors.values())):
            colors = new
            break
        colors = new
    return [{key: colors[('side', i, key)] for key in g[0]} for i, g in enumerate((a, b))]


def normalized_devices(mos, net_map):
    counts = Counter()
    for d in mos:
        dn, sn = net_map.get(d['d'], d['d']), net_map.get(d['s'], d['s'])
        counts[(d['model'], d['l'], d['w'], d['nfin'], net_map.get(d['g'], d['g']),
                net_map.get(d['b'], d['b']), min(dn, sn), max(dn, sn))] += 1
    return counts


def isomorphic(a, b, ports):
    ga, gb = graph(a, ports), graph(b, ports)
    ca, cb = paired_colors(ga, gb)
    if Counter(ca.values()) != Counter(cb.values()):
        return None
    groups_a, groups_b = defaultdict(list), defaultdict(list)
    for node, color in ca.items():
        if node[0] == 'net':
            groups_a[color].append(node[1])
    for node, color in cb.items():
        if node[0] == 'net':
            groups_b[color].append(node[1])
    if {k: len(v) for k, v in groups_a.items()} != {k: len(v) for k, v in groups_b.items()}:
        return None
    keys = sorted(groups_a)
    target = normalized_devices(b, {})
    for choices in product(*(permutations(groups_b[k]) for k in keys)):
        mapping = {x: y for k, ys in zip(keys, choices) for x, y in zip(groups_a[k], ys)}
        if any(mapping[p] != p for p in ports):
            continue
        if normalized_devices(a, mapping) == target:
            return mapping
    return None


def main():
    old_path = ROOT / 'gcd_calibre_input/gcd_device_complete.cdl'
    new_path = HERE / 'asap7_R_official_pex_full.cdl'
    old, new = circuits(old_path.read_text()), circuits(new_path.read_text())
    usage = Counter(line.split()[-1] for line in old['gcd']['lines'] if line.startswith('X'))
    results = []
    for name, instances in sorted(usage.items()):
        if not name.startswith(('DFF', 'SDF', 'LATCH')):
            continue
        a, b = merged_mos(old[name.lower()]), merged_mos(new[name.lower()])
        ports = {p.upper() for p in old[name.lower()]['ports']}
        assert ports == {p.upper() for p in new[name.lower()]['ports']}
        mapping = isomorphic(a, b, ports)
        assert mapping is not None, name
        bad_gate = deepcopy(b)
        output_device = next(d for d in bad_gate if d['model'].startswith('nmos') and 'QN' in (d['d'], d['s']))
        output_device['g'] = 'CLK'
        assert isomorphic(a, bad_gate, ports) is None
        bad_width = deepcopy(b)
        bad_width[0]['w'] *= 2
        assert isomorphic(a, bad_width, ports) is None
        results.append({'cell': name, 'instances': instances, 'status': 'PASS',
                        'original_mos': len(old[name.lower()]['lines']), 'official_pex_mos': len(new[name.lower()]['lines']),
                        'original_merged_mos': len(a), 'official_pex_merged_mos': len(b),
                        'port_identity_fixed': sorted(ports), 'internal_node_mapping_original_to_pex': {x:y for x,y in mapping.items() if x not in ports},
                        'model_L_W_nfin_gate_bulk_connectivity_exact': True,
                        'negative_control_changed_output_gate': 'REJECTED', 'negative_control_doubled_width': 'REJECTED'})
    assert results
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {'status': 'PASS', 'method': 'Exact transistor graph isomorphism after strictly parallel MOS aggregation. Ports fixed; gate/bulk fixed; source/drain swappable. All model/L/W/nfin values exact.',
              'original_sha256': digest(old_path), 'reference_sha256': digest(new_path),
              'scope': 'Proves that the replacement sequential transistor reference preserves the original CDL network. Separate Liberty formal LEC proves RTL/netlist logic, and does not directly validate transistor sequential semantics.',
              'sequential_cell_types': len(results), 'sequential_instances': sum(x['instances'] for x in results), 'cells': results}
    (HERE / 'sequential_topology_verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
