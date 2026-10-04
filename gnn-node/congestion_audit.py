"""Independent, bounded DEF/LEF recomputation of the routed-utilization proxy.

This does not import the converter. Supported input is rectilinear exported
OpenROAD DEF, with radius-zero labels. Unsupported geometry fails closed.
It verifies the proxy definition, not actual router overflow or usable capacity.
"""
import argparse
import copy
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from stage_data import digest, labels, load_graph, save_json


def section(text, name):
    match = re.search(r'(?ms)^\s*'+name+r'\s+(\d+)\s*;(.*?)^\s*END\s+'+name+r'\b', text)
    if not match:
        raise ValueError('Missing DEF section: '+name)
    entries = re.findall(r'(?ms)^\s*-\s+(\S+)\s+(.*?);', match[2])
    if len(entries) != int(match[1]):
        raise ValueError('Unsupported or incomplete DEF entries: '+name)
    return entries


def capacities(paths, width):
    layers = {}
    for path in paths:
        text = Path(path).read_text()
        for match in re.finditer(r'(?ms)^\s*LAYER\s+(\S+)\s*\n(.*?)^\s*END\s+\1\s*$', text):
            name, body = match.groups()
            if not re.search(r'\bTYPE\s+ROUTING\s*;', body):
                continue
            direction = re.search(r'\bDIRECTION\s+(HORIZONTAL|VERTICAL)\s*;', body)
            pitch = re.search(r'\bPITCH\s+([\d.eE+\-]+)(?:\s+([\d.eE+\-]+))?\s*;', body)
            if not direction or not pitch:
                raise ValueError('Incomplete routing layer: '+name)
            axis = 0 if direction[1] == 'HORIZONTAL' else 1
            value = float(pitch[2] if axis == 0 and pitch[2] else pitch[1])
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid pitch')
            spec = (axis, value)
            if name in layers and layers[name] != spec:
                raise ValueError('Conflicting routing layer definitions')
            layers[name] = spec
    result = [sum(width*width/pitch for axis, pitch in layers.values() if axis == k) for k in (0, 1)]
    if min(result) <= 0:
        raise ValueError('Missing horizontal or vertical capacity')
    return result


def raw_proxy(cfg):
    if int(cfg.get('congestion_radius', 0)) != 0:
        raise ValueError('Independent audit supports radius zero only')
    path = Path(cfg.get('label_def') or cfg['route_def'])
    text = path.read_text()
    units = re.search(r'\bUNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;', text)
    die = re.search(r'\bDIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;', text)
    if not units or not die:
        raise ValueError('Unsupported DEF units/die rectangle')
    dbu = int(units[1])
    width = float(cfg.get('congestion_grid_um', float(cfg.get('congestion_grid_pitch_um', .46))*int(cfg.get('congestion_grid_tracks', 15))))
    step = round(width*dbu)
    if dbu <= 0 or step <= 0:
        raise ValueError('Invalid grid or units')
    origin = (int(die[1]), int(die[2]))
    cap = capacities(cfg['lef'], step/dbu)
    demand = defaultdict(lambda: [0., 0.])
    segment_count = 0
    for _, body in section(text, 'NETS'):
        starts = list(re.finditer(r'\b(?:ROUTED|NEW|FIXED|COVER)\s+\S+', body))
        for i, start in enumerate(starts):
            clause = body[start.end():starts[i+1].start() if i+1 < len(starts) else len(body)]
            if re.search(r'\b(?:POLYGON|VIRTUAL)\b', clause):
                raise ValueError('Unsupported routed geometry')
            patches = re.findall(r'\bRECT\s*\(([^()]*)\)', clause)
            for patch in patches:
                if len(patch.split()) != 4 or any(not re.fullmatch(r'-?\d+', t) for t in patch.split()):
                    raise ValueError('Unsupported route patch rectangle')
            # Patch rectangles are areas/offsets, not extra wire centerline points.
            clause = re.sub(r'\bRECT\s*\([^()]*\)', '', clause)
            if re.search(r'\bRECT\b', clause):
                raise ValueError('Malformed route patch rectangle')
            previous = None
            for point in re.finditer(r'\(([^()]*)\)', clause):
                tokens = point[1].split()
                if len(tokens) not in (2, 3) or any(not re.fullmatch(r'-?\d+|\*', t) for t in tokens):
                    raise ValueError('Unsupported route point')
                if previous is None and '*' in tokens[:2]:
                    raise ValueError('Wildcard without preceding point')
                current = tuple(previous[a] if tokens[a] == '*' else int(tokens[a]) for a in (0, 1))
                if previous is not None and previous != current:
                    if all(previous[a] != current[a] for a in (0, 1)):
                        raise ValueError('Nonrectilinear route segment')
                    axis = 0 if previous[0] != current[0] else 1
                    low, high = sorted((previous[axis]-origin[axis], current[axis]-origin[axis]))
                    fixed = (current[1-axis]-origin[1-axis])//step
                    # Interval intersections, independently of the converter's cursor walker.
                    for index in range(low//step, (high-1)//step+1):
                        length = min(high, (index+1)*step)-max(low, index*step)
                        key = (index, fixed) if axis == 0 else (fixed, index)
                        demand[key][axis] += length/dbu
                    segment_count += 1
                previous = current
    coords = {}
    for name, body in section(text, 'COMPONENTS'):
        pos = re.search(r'\+\s*(?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)', body)
        name = name.replace('\\', '')
        if name in coords:
            raise ValueError('Duplicate canonical component name')
        coords[name] = tuple((int(pos[a+1])-origin[a])//step for a in (0, 1)) if pos else None
    utilization = {key: max(d[0]/cap[0], d[1]/cap[1]) for key, d in demand.items()}
    return coords, utilization, {'segments': segment_count, 'grid_step_um': step/dbu,
                                'capacity_h_um': cap[0], 'capacity_v_um': cap[1],
                                'routed_grid_count': len(utilization)}


def distribution(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {'n': 0}
    return {'n': len(values), 'zero_fraction': float((values == 0).mean()),
            'over_one_fraction': float((values > 1).mean()),
            'min': float(values.min()), 'median': float(np.median(values)),
            'p95': float(np.quantile(values, .95)), 'max': float(values.max()),
            'std': float(values.std())}


def audit_record(record):
    root = Path(record['graphs']['cts']['path']).parents[2]
    config_path = root.parent/'method_config.json'
    cfg = json.loads(config_path.read_text())
    sidecar = root/'labels/gate_con_IR.csv'
    row = copy.deepcopy(record)
    row['congestion_labels'] = {'path': str(sidecar), 'sha256': digest(sidecar)}
    coords, values, details = raw_proxy(cfg)
    stages, all_values, occupied = {}, [], {}
    with sidecar.open(newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    if any(not math.isclose(float(r[k]), details['grid_step_um'], rel_tol=1e-9)
           for r in csv_rows for k in ('grid_step_x_um', 'grid_step_y_um')):
        raise ValueError('Sidecar grid pitch mismatch')
    reference = None
    for stage in record['graphs']:
        graph = load_graph(row, stage, 'congestion')
        y, mask = labels(graph, 'congestion')
        names = list(graph['gate'].inst_name)
        max_error = 0.
        for i, name in enumerate(names):
            grid = coords.get(name)
            if bool(mask[i]) != (grid is not None):
                raise ValueError('DEF/tensor validity mismatch: '+name)
            if grid is not None:
                expected = values.get(grid, 0.)
                if tuple(graph['gate'].target_grid[i].tolist()) != grid:
                    raise ValueError('DEF/tensor grid mismatch: '+name)
                if not math.isclose(float(y[i]), expected, rel_tol=1e-6, abs_tol=1e-8):
                    raise ValueError('DEF/tensor value mismatch: '+name)
                max_error = max(max_error, abs(float(y[i])-expected))
                if reference is None:
                    all_values.append(float(y[i]))
                    occupied[grid] = expected
        if reference is not None:
            old_names, old_y, old_mask = reference
            if names != old_names or not torch.equal(mask, old_mask) or not torch.allclose(y, old_y, equal_nan=True):
                raise ValueError('Stage target mismatch')
        reference = (names, y, mask)
        stages[stage] = {'valid': int(mask.sum()), 'total': len(mask), 'max_abs_error': max_error}
    input_paths = [config_path, sidecar, Path(cfg.get('label_def') or cfg['route_def']), *map(Path, cfg['lef'])]
    report = {'design_id': record['design_id'], 'status': 'PASS', 'stages': stages,
              'graph_sha256': {s: info['sha256'] for s, info in record['graphs'].items()},
              'gate_distribution': distribution(all_values),
              'occupied_grid_distribution': distribution(list(occupied.values())),
              'raw_geometry': details, 'inputs': {str(p): digest(p) for p in input_paths}}
    return row, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise FileExistsError('Use a new audit directory')
    manifest = json.loads(Path(args.manifest).read_text())
    records, reports = [], []
    for record in manifest['records']:
        row, report = audit_record(record)
        records.append(row)
        reports.append(report)
        print(json.dumps({k: report[k] for k in ('design_id', 'status', 'gate_distribution')}), flush=True)
    summary = {'status': 'PASS', 'definition': 'postroute local routed-length utilization proxy; not routing overflow',
               'scope': 'independent DEF/LEF values, masks, gate-origin grids and cross-stage labels',
               'limits': ['radius zero, rectilinear DEF only; patch metal excluded from centerline length', 'nominal capacity, no blockage or router adjustment model',
                          'occupied canonical-gate grids only for downstream grid metrics'],
               'source_manifest_sha256': digest(args.manifest), 'audit_code_sha256': digest(__file__), 'records': reports,
               'valid_gates': sum(r['gate_distribution']['n'] for r in reports),
               'occupied_grids': sum(r['occupied_grid_distribution']['n'] for r in reports)}
    save_json(out/'audit.json', summary)
    manifest.update(records=records, congestion_audit={'path': str(out/'audit.json'), 'sha256': digest(out/'audit.json')})
    save_json(out/'manifest.json', manifest)


if __name__ == '__main__':
    main()
