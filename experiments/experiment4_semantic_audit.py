#!/usr/bin/env python3
"""Read-only, retrospective graph audit against raw Yosys/OpenDB evidence.

This module does not import a converter or use R2G output as reference truth.
Unimplemented semantic dimensions remain NOT_VERIFIED, never automatic passes.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

import torch

STAGES = ('floorplan', 'placement', 'cts', 'route')
NODES = ('gate', 'pin', 'net', 'io_pin')
CORE_EDGES = (('gate', 'has', 'pin'), ('pin', 'connects_to', 'net'), ('io_pin', 'connects_to', 'net'))
VERIFIED_CORE_GROUPS = ('alignment', 'causal', 'identity', 'label', 'mask', 'numeric', 'topology')
POLICY = {
    'version': 'experimental-semantic-audit-0.3',
    'coordinate_atol_um': 0.001, 'relative_tolerance': 1e-5,
    'hpwl_atol_um': 0.0021,
    'wirelength_atol_um': 0.0011,
    'ground_cap_atol_pf': 1e-9,
    'normalized_coordinate_atol': 1e-6,
    'slack_atol_ns': 0.011,
    'identity': 'Yosys bit identities; signal aliases are equivalent; connected nets only',
    'missing_oracle': 'NOT_VERIFIED',
    'not_verified_dimensions': ['all_liberty_features', 'pin_shape_and_layer_geometry',
        'split_or_renamed_net_wirelength_and_ground_capacitance', 'rc_and_timing_edge_labels',
        'congestion_values', 'execution_level_noninterference', 'batch_training'],
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')
    tmp.replace(path)


def bit_names(name, signal):
    bits = signal['bits']
    offset = int(signal.get('offset', 0))
    is_bus = len(bits) > 1 or signal.get('offset', 0) or signal.get('upto', 0)
    for i, bit in enumerate(bits):
        index = offset + (len(bits) - 1 - i if signal.get('upto', 0) else i)
        yield (f'{name}[{index}]' if is_bus else name), str(bit)


def logical_oracle(module):
    aliases = {}
    for name, signal in module['netnames'].items():
        for alias, bit in bit_names(name, signal):
            aliases[alias] = bit
    for bit in ('0', '1', 'x', 'z'):
        for alias in (bit, f"1'b{bit}", f"1'h{bit}"):
            aliases[alias] = bit
    gates = set(module['cells'])
    pins, pin_edges, io_edges = set(), set(), set()
    for name, cell in module['cells'].items():
        for port, bits in cell['connections'].items():
            for i, bit in enumerate(bits):
                pin = (name, port if len(bits) == 1 else f'{port}[{i}]')
                pins.add(pin)
                pin_edges.add((pin, str(bit)))
    for name, port in module['ports'].items():
        for io, bit in bit_names(name, port):
            io_edges.add((io, bit))
    return {'gate': gates, 'pin': pins, 'io_pin': {a for a, _ in io_edges},
            'net': {b for _, b in pin_edges | io_edges}, 'aliases': aliases,
            'edges': {CORE_EDGES[0]: {(a, (a, b)) for a, b in pins},
                      CORE_EDGES[1]: pin_edges, CORE_EDGES[2]: io_edges}}


def identity_metrics(actual, expected):
    counts = Counter(actual)
    observed = set(actual)
    tp = len(observed & expected)
    fp = len(observed - expected) + sum(v - 1 for v in counts.values())
    fn = len(expected - observed)
    precision = tp / (tp + fp) if tp + fp else float(not expected)
    recall = tp / len(expected) if expected else float(not actual)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall,
            'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0,
            'missing_examples': sorted(map(str, expected - observed))[:5],
            'extra_examples': sorted(map(str, observed - expected))[:5]}


def read_slacks(text):
    """Worst reported slack at the last DATA pin, not the capture clock pin.

    This parser supports the frozen OpenSTA full-path format. Unknown blocks
    are counted, not silently converted to zero-valued labels.
    """
    values, unparsed, blocks = {}, 0, 0
    for block in re.split(r'(?m)^Startpoint:', text)[1:]:
        blocks += 1
        arrival = block.find('data arrival time')
        slack = re.search(r'(?m)^\s*([-+0-9.eE]+)\s+slack\s+\((?:MET|VIOLATED)\)', block)
        points = re.findall(r'(?m)^\s*[-+0-9.eE]+\s+[-+0-9.eE]+\s+[\^v]\s+(\S+)', block[:arrival]) if arrival >= 0 else []
        if not points or not slack:
            unparsed += 1
            continue
        value = float(slack.group(1))
        if not math.isfinite(value):
            unparsed += 1
            continue
        endpoint = points[-1].replace('\\', '')
        values[endpoint] = min(values.get(endpoint, value), value)
    return {'values': values, 'blocks': blocks, 'unparsed_blocks': unparsed}


def build_oracles(cfg, work, yosys, openroad):
    work.mkdir(parents=True, exist_ok=True)
    paths = [Path(cfg['yosys_v'])] + [Path(x) for x in cfg['lef']]
    paths += [Path(cfg[k]) for k in ('floorplan_def', 'place_def', 'cts_def', 'route_def', 'spef',
                                     'timing_max_rpt', 'timing_min_rpt')]
    attestation = {'inputs': {str(p): digest(p) for p in paths},
                   'yosys_binary': digest(yosys), 'openroad_binary': digest(openroad),
                   'audit_source': digest(__file__),
                   'oracle_source': digest(Path(__file__).with_name('experiment4_raw_oracle.tcl')),
                   'top_module': cfg['top_module'], 'policy': POLICY}
    cache = work / 'attestation.json'
    if cache.exists() and json.loads(cache.read_text()) == attestation:
        if all((work / x).exists() for x in ('yosys.json', 'physical.json')):
            return attestation
    script = work / 'read.ys'
    # The paths are quoted for Yosys, not interpolated into a shell command.
    script.write_text(f'read_verilog {json.dumps(cfg["yosys_v"])}\nwrite_json {json.dumps(str(work / "yosys.json"))}\n')
    with (work / 'yosys.log').open('w') as log:
        subprocess.run([str(yosys), '-Q', '-T', '-s', str(script)], stdout=log,
                       stderr=subprocess.STDOUT, timeout=120, check=True)
    physical = {}
    for stage, key in [('placement', 'floorplan_def'), ('cts', 'place_def'), ('route', 'cts_def'),
                       ('label_route', 'route_def')]:
        export = work / (stage + '.csv')
        env = dict(os.environ, EXP4_ORACLE_DEF=cfg[key], EXP4_ORACLE_LEFS='\n'.join(cfg['lef']),
                   EXP4_ORACLE_OUTPUT=str(export), OMP_NUM_THREADS='1')
        with (work / (stage + '.log')).open('w') as log:
            subprocess.run([str(openroad), '-no_init', '-no_splash', '-exit', '-threads', '1',
                            str(Path(__file__).with_name('experiment4_raw_oracle.tcl'))], env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=180, check=True)
        with export.open(newline='') as f:
            records = list(csv.DictReader(f))
        die = records[0]
        if float(die['width']) <= 0 or float(die['height']) <= 0:
            raise ValueError('nonpositive die dimensions')
        physical[stage] = {'dbu_per_um': float(die['dbu']), 'die_width_um': float(die['width']),
                           'die_height_um': float(die['height']), 'die_area_um2': float(die['area']),
                           'instances': {}}
        physical[stage]['pins'] = {}
        physical[stage]['io_pins'] = {}
        physical[stage]['nets'] = {}
        for row in records[1:]:
            if row['kind'] == 'iterm':
                inst = row['name'].replace('\\', '')
                pin = row['pin_name'].replace('\\', '')
                if row['valid'] == '1':
                    owner = physical[stage]['pins'].setdefault(inst, {})
                    if pin in owner:
                        raise ValueError('duplicate OpenDB pin identity')
                    owner[pin] = {'pin_x_um': float(row['x_um']), 'pin_y_um': float(row['y_um']),
                        'status': row['status'], 'is_block': row['is_block'] == '1'}
                if row['net_name']:
                    net = physical[stage]['nets'].setdefault(row['net_name'].replace('\\', ''),
                        {'endpoints': [], 'points': [], 'endpoint_count': 0, 'valid_endpoint_count': 0})
                    net['endpoint_count'] += 1
                    net['endpoints'].append('pin:' + json.dumps([inst, pin], separators=(',', ':')))
                    if row['valid'] == '1':
                        net['valid_endpoint_count'] += 1
                        net['points'].append([float(row['x_um']), float(row['y_um'])])
                continue
            if row['kind'] == 'bterm':
                key = row['name'].replace('\\', '')
                if row['valid'] == '1':
                    if key in physical[stage]['io_pins']:
                        raise ValueError('duplicate OpenDB IO pin identity')
                    physical[stage]['io_pins'][key] = {'pin_x_um': float(row['x_um']), 'pin_y_um': float(row['y_um'])}
                if row['net_name']:
                    net = physical[stage]['nets'].setdefault(row['net_name'].replace('\\', ''),
                        {'endpoints': [], 'points': [], 'endpoint_count': 0, 'valid_endpoint_count': 0})
                    net['endpoint_count'] += 1
                    net['endpoints'].append('io:' + key)
                    if row['valid'] == '1':
                        net['valid_endpoint_count'] += 1
                        net['points'].append([float(row['x_um']), float(row['y_um'])])
                continue
            if row['kind'] != 'instance':
                continue
            normalized = row['name'].replace('\\', '')
            if normalized in physical[stage]['instances']:
                raise ValueError('OpenDB identifier normalization collision')
            physical[stage]['instances'][normalized] = {
                'master': row['master'], 'status': row['status'], 'is_block': row['is_block'] == '1',
                **{k: float(row[k]) for k in ('x_um', 'y_um', 'center_x_um', 'center_y_um')}}
            for axis, size in [('x', 'width'), ('y', 'height')]:
                physical[stage]['instances'][normalized]['center_' + axis + '_normalized'] = (
                    float(row['center_' + axis + '_um']) - float(die[axis + '_um'])) / float(die[size])
        for net in physical[stage]['nets'].values():
            points = net.pop('points')
            if points and net['endpoint_count'] == net['valid_endpoint_count']:
                xs, ys = zip(*points)
                net['net_bbox_width_um'] = max(xs) - min(xs)
                net['net_bbox_height_um'] = max(ys) - min(ys)
                net['hpwl_um'] = net['net_bbox_width_um'] + net['net_bbox_height_um']
    save(work / 'physical.json', physical)
    save(cache, attestation)
    return attestation


def node_ids(graph, node, aliases):
    store = graph[node]
    if node == 'pin':
        first, second = store['inst_name'], store['pin_name']
        if len(first) != len(second):
            raise ValueError('pin identity vectors differ in length')
        return [(str(a).replace('\\', ''), str(b).replace('\\', '')) for a, b in zip(first, second)]
    field = {'gate': 'inst_name', 'net': 'net_name', 'io_pin': 'iopin_name'}[node]
    names = [str(x).replace('\\', '') for x in store[field]]
    return [aliases.get(x, 'UNKNOWN:' + x) for x in names] if node == 'net' else names


def column(store, name, label=False):
    schema, tensor = ('y_schema', 'y') if label else ('x_schema', 'x')
    names = list(store[schema])
    if len(set(names)) != len(names) or store[tensor].ndim != 2 or len(names) != store[tensor].shape[1]:
        raise ValueError('ambiguous or malformed feature schema')
    return store[tensor][:, names.index(name)]


def compare_values(observed, expected, atol):
    missing = bad = 0
    errors, examples = [], []
    for key, truth in expected.items():
        value = observed.get(key, float('nan'))
        if not math.isfinite(value):
            missing += 1
        else:
            error = abs(value - truth)
            errors.append(error)
            if not math.isclose(value, truth, abs_tol=atol, rel_tol=POLICY['relative_tolerance']):
                bad += 1
                if len(examples) < 5:
                    examples.append({'key': str(key), 'actual': value, 'expected': truth})
    return {'expected': len(expected), 'matched': len(errors), 'missing': missing,
            'incorrect': bad, 'max_abs_error': max(errors) if errors else None,
            'mae': sum(errors) / len(errors) if errors else None, 'examples': examples}


def aggregate_status(counts):
    for status in ('FAIL', 'UNASSESSABLE', 'PASS'):
        if counts.get(status, 0):
            return status
    return 'NOT_APPLICABLE'


def row_values(names, values):
    if len(names) != len(values) or len(set(names)) != len(names):
        raise ValueError('numeric rows lack unique, aligned identities')
    return dict(zip(names, values))


def direct_net_geometry(truth, physical_nets):
    """Return net geometry only when raw physical and Yosys endpoints agree exactly."""
    expected = {}
    for pin, bit in truth['edges'][CORE_EDGES[1]]:
        expected.setdefault(bit, set()).add('pin:' + json.dumps(list(pin), separators=(',', ':')))
    for io_pin, bit in truth['edges'][CORE_EDGES[2]]:
        expected.setdefault(bit, set()).add('io:' + io_pin)
    candidates = {}
    for name, net in physical_nets.items():
        bit = truth['aliases'].get(name)
        if bit in truth['net']:
            candidates.setdefault(bit, []).append((name, net))
    result = {}
    for bit, nets in candidates.items():
        if len(nets) != 1:
            continue
        physical_name, net = nets[0]
        endpoints = net['endpoints']
        if (len(endpoints) != len(set(endpoints)) or set(endpoints) != expected.get(bit, set())
                or not all(k in net for k in ('net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um'))):
            continue
        result[bit] = dict(net, physical_name=physical_name)
    return result


def read_routed_lengths(path):
    """Independently sum Manhattan segments in the DEF NETS section."""
    text = Path(path).read_text(errors='replace')
    unit = re.search(r'(?m)^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)', text)
    section = re.search(r'(?ms)^\s*NETS\s+\d+\s*;(.*?)^\s*END\s+NETS\b', text)
    if not unit or not section:
        raise ValueError('DEF lacks units or NETS section')
    dbu = float(unit.group(1))
    result = {}
    for entry in re.finditer(r'(?ms)^\s*-\s+(\S+)(.*?);', section.group(1)):
        name, body = entry.group(1).replace('\\', ''), entry.group(2)
        total = 0
        starts = list(re.finditer(r'\b(?:ROUTED|NEW|FIXED)\s+\S+', body))
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
            clause = re.sub(r'\bRECT\s*\([^)]*\)', '', body[start.end():end])
            points = re.findall(r'\(\s*([*]|-?\d+)\s+([*]|-?\d+)(?:\s+[^)]*)?\)', clause)
            current = None
            for x_token, y_token in points:
                if current is None:
                    if '*' not in (x_token, y_token):
                        current = (int(x_token), int(y_token))
                    continue
                x = current[0] if x_token == '*' else int(x_token)
                y = current[1] if y_token == '*' else int(y_token)
                total += abs(x - current[0]) + abs(y - current[1])
                current = (x, y)
        result[name] = total / dbu
    return result


def read_spef_ground_caps(path):
    """Independently sum three-column ground-cap entries in SPEF *CAP blocks."""
    units = {'F': 1e12, 'PF': 1.0, 'FF': 1e-3}
    name_map, result = {}, {}
    in_name_map = in_cap = False
    current = None
    scale = None
    for raw in Path(path).read_text(errors='replace').splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r'\*C_UNIT\s+([-+0-9.eE]+)\s+(\S+)', line)
        if match:
            unit = match.group(2).upper()
            if unit not in units:
                raise ValueError('unsupported SPEF capacitance unit: ' + unit)
            scale = float(match.group(1)) * units[unit]
            continue
        if line == '*NAME_MAP':
            in_name_map = True
            continue
        if in_name_map and re.match(r'^\*\d+\s+', line):
            key, value = line.split(None, 1)
            name_map[key] = value.replace('\\', '')
            continue
        if line.startswith('*D_NET '):
            in_name_map = False
            token = line.split()[1]
            current = name_map.get(token, token).replace('\\', '')
            result[current] = 0.0
            in_cap = False
            continue
        if line == '*CAP':
            in_cap = True
            continue
        if line.startswith(('*RES', '*CONN', '*END')):
            in_cap = False
            if line == '*END':
                current = None
            continue
        if current is not None and in_cap:
            fields = line.split()
            if len(fields) == 3:
                result[current] += float(fields[-1]) * (scale if scale is not None else 1.0)
    if scale is None:
        raise ValueError('SPEF lacks *C_UNIT')
    return result


def audit_graphs(root, truth, physical, slacks, raw_labels=None):
    checks, baseline = [], {}

    def record(stage, group, name, fn):
        try:
            ok, evidence = fn()
            status = ok if isinstance(ok, str) else ('PASS' if ok else 'FAIL')
        except (KeyError, AttributeError, ValueError, IndexError, TypeError, RuntimeError) as exc:
            status, evidence = 'UNASSESSABLE', {'error': str(exc)[:400]}
        checks.append({'stage': stage, 'group': group, 'check': name, 'status': status, 'evidence': evidence})

    for stage in STAGES:
        path = root / 'stages' / stage / 'heterograph.pt'
        try:
            graph = torch.load(path, map_location='cpu', weights_only=False)
        except Exception as exc:
            checks.append({'stage': stage, 'group': 'load', 'check': 'graph_load',
                           'status': 'FAIL', 'evidence': {'error': str(exc)[:400]}})
            continue
        ids = {}
        for node in NODES:
            def entities(node=node):
                names = node_ids(graph, node, truth['aliases'])
                ids[node] = names
                result = identity_metrics(names, truth[node])
                return result['fp'] == result['fn'] == 0, result
            record(stage, 'identity', node, entities)

            def shape_masks(node=node):
                store = graph[node]
                x, y, mask = store.x, store.y, store.y_valid_mask
                ok = x.ndim == y.ndim == mask.ndim == 2 and y.shape == mask.shape
                ok = ok and mask.dtype == torch.bool and x.shape[0] == y.shape[0] == len(ids[node])
                ok = ok and not torch.isinf(x).any().item() and torch.equal(torch.isfinite(y), mask)
                return bool(ok), {'feature_shape': list(x.shape), 'label_shape': list(y.shape),
                                  'valid_labels': int(mask.sum())}
            record(stage, 'mask', node, shape_masks)

            def shared(node=node):
                names = ids[node]
                if len(set(names)) != len(names):
                    raise ValueError('duplicate identity prevents alignment')
                rows = {name: i for i, name in enumerate(names)}
                if stage == 'floorplan':
                    baseline[node] = (rows, graph[node].y.clone(), list(graph[node].y_schema))
                    return True, {'reference_stage': 'floorplan', 'rows': len(rows)}
                ref, y, schema = baseline[node]
                if set(rows) != set(ref) or list(graph[node].y_schema) != schema:
                    return False, {'reason': 'identity/label schema changes across stages'}
                actual = graph[node].y[[rows[name] for name in ref]]
                expected = y[[ref[name] for name in ref]]
                return bool(torch.allclose(actual, expected, rtol=0, atol=0, equal_nan=True)), {'rows': len(rows)}
            record(stage, 'alignment', node, shared)
        for relation in CORE_EDGES:
            def edges(relation=relation):
                index = graph[relation].edge_index
                if index.dtype != torch.int64 or index.ndim != 2 or index.shape[0] != 2:
                    raise ValueError('edge_index must be int64 [2,E]')
                src, dst = ids[relation[0]], ids[relation[2]]
                if index.numel() and (index.min() < 0 or index[0].max() >= len(src) or index[1].max() >= len(dst)):
                    raise ValueError('edge endpoint out of bounds')
                result = identity_metrics([(src[a], dst[b]) for a, b in index.T.tolist()], truth['edges'][relation])
                return result['fp'] == result['fn'] == 0, result
            record(stage, 'topology', '|'.join(relation), edges)

        def causal():
            violations = []
            required = {'gate': ['x_um', 'y_um', 'center_x_um', 'center_y_um'],
                        'net': ['hpwl_um'], 'pin': ['pin_x_um', 'pin_y_um'],
                        'io_pin': ['pin_x_um', 'pin_y_um']}
            for node, fields in required.items():
                for field in fields:
                    values = column(graph[node], field)
                    if stage == 'floorplan' or (stage == 'placement' and node == 'net'):
                        if torch.isfinite(values).any():
                            violations.append(node + '.' + field)
            for relation in graph.edge_types:
                if relation[1] in ('timing_path', 'rc_coupling', 'rc_resistance'):
                    if graph[relation].edge_attr.shape[1] != 0:
                        violations.append('|'.join(relation) + ':labels_in_edge_attr')
                if stage in ('floorplan', 'placement') and relation[1] == 'congestion_geom' and graph[relation].edge_index.numel():
                    violations.append('early_geometry_edges')
            return not violations, {'violations': violations, 'scope': 'selected physical fields and label-edge payload; not full noninterference'}
        record(stage, 'causal', 'selected_cutoff_checks', causal)

        if stage in physical:
            db = physical[stage]
            for field in ('x_um', 'y_um', 'center_x_um', 'center_y_um', 'center_x_normalized', 'center_y_normalized'):
                def coordinates(field=field):
                    eligible = {n: p for n, p in db['instances'].items() if p['status'] in ('PLACED', 'FIRM', 'LOCKED')
                                and (stage != 'placement' or (p['is_block'] and p['status'] in ('FIRM', 'LOCKED')))}
                    expected = {n: eligible[n][field] for n in truth['gate'] if n in eligible}
                    values = column(graph['gate'], field).tolist()
                    observed = row_values(ids['gate'], values)
                    atol = POLICY['normalized_coordinate_atol'] if field.endswith('_normalized') else POLICY['coordinate_atol_um']
                    result = compare_values(observed, expected, atol)
                    extra = [n for n, v in observed.items() if math.isfinite(v) and n not in expected]
                    result['ineligible_finite_rows'] = len(extra)
                    return not (result['missing'] or result['incorrect'] or extra), result
                record(stage, 'numeric', 'gate.' + field, coordinates)
            def die():
                schema = list(graph.global_feature_schema)
                expected = {k: db[k] for k in ('die_width_um', 'die_height_um', 'die_area_um2', 'dbu_per_um')}
                actual = {k: float(graph.global_features[0, schema.index(k)]) for k in expected}
                result = compare_values(actual, expected, POLICY['coordinate_atol_um'])
                return not (result['missing'] or result['incorrect']), result
            record(stage, 'numeric', 'die', die)
            for node, source in [('pin', 'pins'), ('io_pin', 'io_pins')]:
                for field in ('pin_x_um', 'pin_y_um'):
                    def pin_coordinates(node=node, source=source, field=field):
                        raw = db[source]
                        if node == 'pin':
                            raw = {(inst, pin): value for inst, pins in raw.items() for pin, value in pins.items()}
                            raw = {n: p for n, p in raw.items() if p['status'] in ('PLACED', 'FIRM', 'LOCKED')
                                   and (stage != 'placement' or (p['is_block'] and p['status'] in ('FIRM', 'LOCKED')))}
                        expected = {n: p[field] for n, p in raw.items() if n in truth[node]}
                        observed = row_values(ids[node], column(graph[node], field).tolist())
                        result = compare_values(observed, expected, POLICY['coordinate_atol_um'])
                        extras = [n for n, v in observed.items() if math.isfinite(v) and n not in expected]
                        result['ineligible_finite_rows'] = len(extras)
                        result['ineligible_examples'] = list(map(str, extras[:5]))
                        return not (result['missing'] or result['incorrect'] or extras), result
                    record(stage, 'numeric', node + '.' + field, pin_coordinates)
            if stage in ('cts', 'route'):
                direct_nets = direct_net_geometry(truth, db['nets'])
                for field in ('net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um'):
                    def net_geometry(field=field):
                        expected = {bit: value[field] for bit, value in direct_nets.items()}
                        observed = row_values(ids['net'], column(graph['net'], field).tolist())
                        atol = POLICY['hpwl_atol_um'] if field == 'hpwl_um' else POLICY['coordinate_atol_um']
                        result = compare_values(observed, expected, atol)
                        result['direct_endpoint_exact_nets'] = len(direct_nets)
                        result['scope'] = 'physical nets whose OpenDB endpoints exactly equal the Yosys canonical endpoints'
                        if not expected:
                            return 'NOT_APPLICABLE', result
                        return not (result['missing'] or result['incorrect']), result
                    record(stage, 'numeric', 'net.' + field, net_geometry)

            if stage == 'route' and raw_labels is not None:
                direct_nets = direct_net_geometry(truth, physical['label_route']['nets'])
                for field, source, tolerance in (
                        ('routed_wirelength_um', 'wirelength', POLICY['wirelength_atol_um']),
                        ('ground_cap_pF', 'ground_cap', POLICY['ground_cap_atol_pf'])):
                    def net_label(field=field, source=source, tolerance=tolerance):
                        expected = {bit: raw_labels[source][net['physical_name']]
                                    for bit, net in direct_nets.items()
                                    if net['physical_name'] in raw_labels[source]}
                        observed = row_values(ids['net'], column(graph['net'], field, label=True).tolist())
                        result = compare_values(observed, expected, tolerance)
                        result['graph_entity_count'] = len(observed)
                        result['graph_finite_labels'] = sum(math.isfinite(value) for value in observed.values())
                        result['report_expected_labels'] = len(expected)
                        result['scope'] = 'direct route nets with exact physical/logical endpoint identity'
                        if not expected:
                            return 'NOT_APPLICABLE', result
                        return not (result['missing'] or result['incorrect']), result
                    record(stage, 'label', 'net.' + field, net_label)

        for node in ('pin', 'io_pin'):
            for name in ('setup_slack_ns', 'hold_slack_ns'):
                def timing(node=node, name=name):
                    report = slacks[name]
                    source = report['values']
                    expected = {k: source['/'.join(k) if isinstance(k, tuple) else k] for k in truth[node]
                                if ('/'.join(k) if isinstance(k, tuple) else k) in source}
                    observed = row_values(ids[node], column(graph[node], name, label=True).tolist())
                    result = compare_values(observed, expected, POLICY['slack_atol_ns'])
                    result['report_blocks'] = report['blocks']
                    result['unparsed_blocks'] = report['unparsed_blocks']
                    result['graph_entity_count'] = len(observed)
                    result['graph_finite_labels'] = sum(math.isfinite(value) for value in observed.values())
                    result['report_expected_labels'] = len(expected)
                    result['scope'] = 'reported canonical data endpoints only; other labels unverified'
                    if report['unparsed_blocks']:
                        return 'UNASSESSABLE', result
                    if not expected:
                        return 'NOT_APPLICABLE', result
                    return not (result['missing'] or result['incorrect']), result
                record(stage, 'label', node + '.' + name, timing)
    groups = {}
    for group in sorted({c['group'] for c in checks}):
        counts = Counter(c['status'] for c in checks if c['group'] == group)
        groups[group] = {'counts': dict(counts), 'status': aggregate_status(counts)}
    verified_core_status = ('PASS' if set(groups) == set(VERIFIED_CORE_GROUPS)
                            and all(groups[group]['status'] == 'PASS' for group in VERIFIED_CORE_GROUPS)
                            else 'FAIL')
    return {'groups': groups, 'checks': checks, 'verified_core_status': verified_core_status,
            'core_semantic_status': 'NOT_VERIFIED',
            'remaining_dimensions': POLICY['not_verified_dimensions']}


def run(args):
    torch.set_num_threads(1)
    cohort = json.loads((args.campaign / 'cohort.json').read_text())
    tasks = cohort['splits'][args.split]
    if args.task:
        tasks = [x for x in tasks if x['task_id'] in args.task]
        if {x['task_id'] for x in tasks} != set(args.task):
            raise ValueError('requested task absent from selected split')
    methods = args.methods.split(',')
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        task_id = task['task_id']
        config_path = args.campaign / 'methods' / methods[0] / task_id / 'method_config.json'
        cfg = json.loads(config_path.read_text())
        work = args.output / 'oracles' / task_id
        try:
            attestation = build_oracles(cfg, work, args.yosys, args.openroad)
            modules = json.loads((work / 'yosys.json').read_text())['modules']
            truth = logical_oracle(modules[cfg['top_module']])
            physical = json.loads((work / 'physical.json').read_text())
            slacks = {name: read_slacks(Path(cfg[key]).read_text()) for name, key in
                      [('setup_slack_ns', 'timing_max_rpt'), ('hold_slack_ns', 'timing_min_rpt')]}
            raw_labels = {'wirelength': read_routed_lengths(cfg['route_def']),
                          'ground_cap': read_spef_ground_caps(cfg['spef'])}
        except Exception as exc:
            error = {'task': task_id, 'status': 'ORACLE_ERROR', 'error': str(exc), 'not_method_failure': True}
            save(work / 'error.json', error)
            rows.append(error)
            print(json.dumps(error), flush=True)
            continue
        for method in methods:
            root = args.campaign / 'methods' / method / task_id / 'generated'
            method_config_path = root.parent / 'method_config.json'
            method_config = json.loads(method_config_path.read_text())
            if {k: v for k, v in method_config.items() if k != 'output_dir'} != {k: v for k, v in cfg.items() if k != 'output_dir'}:
                raise ValueError('Method input configurations differ: ' + str(method_config_path))
            files = [root / 'stages' / s / 'heterograph.pt' for s in STAGES]
            binding = {'oracle': attestation, 'config_sha256': digest(method_config_path),
                       'outputs': {str(p): digest(p) if p.exists() else None for p in files}}
            result_path = args.output / 'cases' / method / (task_id + '.json')
            if result_path.exists() and json.loads(result_path.read_text()).get('binding') == binding:
                result = json.loads(result_path.read_text())
            else:
                result = audit_graphs(root, truth, physical, slacks, raw_labels)
                result.update(task=task_id, method=method, binding=binding, created_at=datetime.now(timezone.utc).isoformat())
                save(result_path, result)
            row = {k: result[k] for k in
                   ('task', 'method', 'groups', 'verified_core_status', 'core_semantic_status')}
            rows.append(row)
            save(args.output / 'progress.json', {'finished_method_cases': len([x for x in rows if 'method' in x]),
                                                'planned_method_cases': len(tasks) * len(methods), 'last': row})
            print(json.dumps({'task': task_id, 'method': method, 'groups': {k: v['status'] for k, v in result['groups'].items()}}), flush=True)
    save(args.output / 'summary.json', {'schema_version': POLICY['version'], 'policy': POLICY,
         'study_role': args.study_role,
         'campaign': str(args.campaign), 'cohort_sha256': digest(args.campaign / 'cohort.json'),
         'planned_method_cases': len(tasks) * len(methods), 'rows': rows})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split', default='hidden_test')
    parser.add_argument('--task', action='append')
    parser.add_argument('--methods', default='r2g-frozen-v3,llm-gpt-frozen,llm-claude-frozen,llm-qwen-frozen')
    parser.add_argument('--yosys', type=Path, required=True)
    parser.add_argument('--openroad', type=Path, required=True)
    parser.add_argument('--study-role', default='retrospective measurement development; not a new hidden-test score')
    run(parser.parse_args())
