"""Named-schema adapter for trusted local four-stage R2G artifacts.

Only logical incidence edges enter the encoder. Labels, RC topology and final
layout geometry are never imported as input. Split and training statistics are
explicit, independent artifacts; no legacy processed-data cache is consulted.
"""
import csv
import hashlib
import json
import random
from pathlib import Path

import torch
from torch import nn
from torch_geometric.data import Data

NODE_TYPES = ('gate', 'net', 'io_pin', 'pin')
RELATIONS = (('gate', 'has', 'pin'), ('pin', 'connects_to', 'net'),
             ('io_pin', 'connects_to', 'net'))
TARGETS = {'wirelength': ('routed_wirelength_um', 'um'),
           'ground_cap': ('ground_cap_pF', 'pF'),
           'congestion': ('cell_congestion', 'dimensionless'),
           'setup_slack': ('setup_slack_ns', 'ns'),
           'hold_slack': ('hold_slack_ns', 'ns'),
           'coupling_cap': ('coupling_cap_pF', 'pF'),
           'effective_resistance': ('effective_resistance_ohm', 'ohm')}
TARGET_NODES = {'wirelength': 'net', 'ground_cap': 'net', 'congestion': 'gate',
                'setup_slack': 'pin', 'hold_slack': 'pin'}
TARGET_EDGES = {
    'coupling_cap': ('net', 'rc_coupling', 'net'),
    'effective_resistance': ('pin', 'rc_resistance', 'pin'),
}
TARGET_LEVELS = {target: 'edge' if target in TARGET_EDGES else 'node'
                 for target in TARGETS}
SIGNED_TARGETS = {'setup_slack', 'hold_slack'}


def label_forward(values, target):
    if target in SIGNED_TARGETS:
        return torch.sign(values) * torch.log1p(torch.abs(values))
    return torch.log1p(values)


def label_inverse(values, target):
    if target in SIGNED_TARGETS:
        return torch.sign(values) * torch.expm1(torch.abs(values))
    return torch.expm1(values)
CUTOFFS = {
    'floorplan': 'post_yosys',
    'placement': 'floorplan',
    'cts': 'placement',
    'route': 'cts',
}
FEATURES = {
    'gate': ['cell_type_id', 'cell_function_id', 'is_sequential_cell',
             'is_buffer_cell', 'is_inverter_cell', 'drive_strength',
             'cell_area_um2', 'x_um', 'y_um', 'placement_valid'],
    'net': ['net_type_id', 'pin_count', 'fanout', 'num_drivers', 'num_sinks',
            'connects_macro_flag', 'is_clock_net', 'total_sink_cap_fF',
            'net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um', 'hpwl_valid'],
    'io_pin': ['pin_direction_id', 'pin_role_id', 'is_clock_port',
               'pin_x_um', 'pin_y_um', 'pin_position_valid',
               'clock_period_ns', 'clock_constraint_valid',
               'input_delay_ns', 'output_delay_ns', 'io_constraint_valid'],
    'pin': ['pin_type_id', 'pin_role_id', 'pin_direction_id', 'cell_type_id',
            'pin_cap_fF', 'owner_drive_strength', 'is_clock_pin', 'is_data_pin',
            'pin_x_um', 'pin_y_um', 'pin_position_valid'],
}
GLOBALS = ['die_width_um', 'die_height_um', 'die_area_um2',
           'num_logical_cells', 'num_logical_nets']

FEATURE_ABLATIONS = ('none', 'no_physical_geometry')
PHYSICAL_GEOMETRY_FEATURES = {
    'gate': {'x_um', 'y_um', 'placement_valid'},
    'net': {'net_bbox_width_um', 'net_bbox_height_um', 'hpwl_um', 'hpwl_valid'},
    'io_pin': {'pin_x_um', 'pin_y_um', 'pin_position_valid'},
    'pin': {'pin_x_um', 'pin_y_um', 'pin_position_valid'},
    'global': {'die_width_um', 'die_height_um', 'die_area_um2'},
}


def selected_features(node_type, feature_ablation='none'):
    if feature_ablation not in FEATURE_ABLATIONS:
        raise ValueError('Unknown feature ablation: ' + feature_ablation)
    names = FEATURES[node_type] + GLOBALS
    if feature_ablation == 'no_physical_geometry':
        removed = PHYSICAL_GEOMETRY_FEATURES[node_type] | PHYSICAL_GEOMETRY_FEATURES['global']
        names = [name for name in names if name not in removed]
    return names


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def split_families(records, seed=20260917):
    """Repository groups are conservative proxies, pending family review."""
    groups = sorted({r['family_id'] for r in records})
    if len(groups) < 3:
        raise ValueError('Need at least three independent family groups')
    random.Random(seed).shuffle(groups)
    n = max(1, int(len(groups) * .2))
    assignment = {g: 'test' if i < n else 'validation' if i < 2*n else 'train'
                  for i, g in enumerate(groups)}
    return [dict(r, split=assignment[r['family_id']]) for r in records]


def validate_manifest(manifest, pilot=False):
    records = manifest['records']
    if not pilot and not manifest.get('family_reviewed', False):
        raise ValueError('Formal training requires reviewed family groups')
    ids, family_splits, source_splits = set(), {}, {}
    for row in records:
        if row['design_id'] in ids:
            raise ValueError('Duplicate design')
        ids.add(row['design_id'])
        if row['split'] not in ('train', 'validation', 'test'):
            raise ValueError('Unknown split')
        for key, groups in [('family_id', family_splits), ('source_sha256', source_splits)]:
            value = row[key]
            if not value:
                raise ValueError('Missing identity: ' + key)
            if value in groups and groups[value] != row['split']:
                raise ValueError('Family/source overlaps between splits')
            groups[value] = row['split']
    if {r['split'] for r in records} != {'train', 'validation', 'test'}:
        raise ValueError('Empty split')


def load_graph(record, stage, target=None):
    info = record['graphs'][stage]
    path = Path(info['path'])
    if digest(path) != info['sha256']:
        raise ValueError('Graph changed: ' + str(path))
    validation_path = info['validation_path']
    if digest(validation_path) != info['validation_sha256']:
        raise ValueError('Validation evidence changed')
    if json.loads(Path(validation_path).read_text()).get('status') != 'PASS':
        raise ValueError('Stage validation did not pass')
    graph = torch.load(path, map_location='cpu', weights_only=False)
    if graph.prediction_stage != stage or graph.feature_cutoff != CUTOFFS[stage]:
        raise ValueError('Incorrect prediction cutoff')
    if graph.topology_source != 'verilog' or not graph.y_is_raw_physical_value:
        raise ValueError('Expected canonical topology and raw labels')
    if graph.normalization_applied or graph.leakage_warning:
        raise ValueError('Unsupported normalization or leakage warning')
    if target == 'congestion':
        info = record['congestion_labels']
        if digest(info['path']) != info['sha256']:
            raise ValueError('Congestion label sidecar changed')
        with open(info['path'], newline='') as stream:
            rows = list(csv.DictReader(stream))
        by_name = {row['inst_name']: row for row in rows}
        names = list(graph['gate'].inst_name)
        if len(by_name) != len(rows) or set(by_name) != set(names):
            raise ValueError('Congestion gate identity mismatch')
        y, mask = labels(graph, target)
        grid = torch.zeros((len(y), 2), dtype=torch.long)
        for i, name in enumerate(names):
            row = by_name[name]
            if bool(int(row['congestion_valid'])) != bool(mask[i]):
                raise ValueError('Congestion sidecar mask mismatch')
            if mask[i]:
                if row['congestion_label_unit'] != 'dimensionless' or row['congestion_label_transform'] != 'none':
                    raise ValueError('Congestion sidecar unit/transform mismatch')
                value = torch.tensor(float(row['cell_congestion']), dtype=torch.float64)
                if not torch.isclose(value, y[i], rtol=1e-6, atol=1e-8):
                    raise ValueError('Congestion sidecar value mismatch')
                grid[i] = torch.tensor([int(row['congestion_grid_x']), int(row['congestion_grid_y'])])
        # Label-only metadata: never added to features or message-passing edges.
        graph['gate'].target_grid = grid
    return graph


def features(graph, node_type, names=None):
    store = graph[node_type]
    names = list(names or selected_features(node_type))
    node_names = [name for name in names if name in FEATURES[node_type]]
    cols = [store.x_schema.index(name) for name in node_names]
    x = store.x[:, cols].double().clone()
    if torch.isinf(x).any():
        raise ValueError('Infinite feature')
    # A finite placeholder is still unavailable when its explicit validity is 0.
    validity = {'x_um': 'placement_valid', 'y_um': 'placement_valid',
                'pin_x_um': 'pin_position_valid', 'pin_y_um': 'pin_position_valid',
                'net_bbox_width_um': 'hpwl_valid', 'net_bbox_height_um': 'hpwl_valid',
                'hpwl_um': 'hpwl_valid', 'clock_period_ns': 'clock_constraint_valid',
                'input_delay_ns': 'io_constraint_valid', 'output_delay_ns': 'io_constraint_valid'}
    for name, mask in validity.items():
        if name in node_names and mask in node_names:
            x[x[:, node_names.index(mask)] != 1, node_names.index(name)] = float('nan')
    globals_ = graph.global_features.reshape(-1)
    global_names = [name for name in names if name in GLOBALS]
    values = globals_[[graph.global_feature_schema.index(name) for name in global_names]]
    by_name = {name: x[:, i:i+1] for i, name in enumerate(node_names)}
    by_name.update({name: values[i:i+1].double().expand(len(x), 1)
                    for i, name in enumerate(global_names)})
    return torch.cat([by_name[name] for name in names], dim=1)


def labels(graph, target):
    name, unit = TARGETS[target]
    if TARGET_LEVELS[target] == 'edge':
        store = graph[TARGET_EDGES[target]]
        idx = store.edge_y_schema.index(name)
        if store.edge_y_unit[idx] != unit:
            raise ValueError('Unexpected target unit')
        y = store.edge_y[:, idx].double()
        mask = store.edge_y_mask[:, idx].bool()
        if target == 'coupling_cap':
            canonical = store.edge_index[0] < store.edge_index[1]
            y, mask = y[canonical], mask[canonical]
    else:
        store = graph[TARGET_NODES[target]]
        idx = store.y_schema.index(name)
        if store.y_unit[idx] != unit:
            raise ValueError('Unexpected target unit')
        y = store.y[:, idx].double()
        mask = store.y_valid_mask[:, idx].bool()
    if not torch.equal(torch.isfinite(y), mask):
        raise ValueError('Target validity contract violated')
    if target not in SIGNED_TARGETS and (y[mask] < 0).any():
        raise ValueError('Nonnegative target contract violated')
    return y, mask


def target_edge_index(graph, target):
    relation = TARGET_EDGES[target]
    edge = graph[relation].edge_index.long().clone()
    if target == 'coupling_cap':
        edge = edge[:, edge[0] < edge[1]]
    for row, kind in ((0, relation[0]), (1, relation[2])):
        if edge[row].numel() and (edge[row].min() < 0 or edge[row].max() >= graph[kind].num_nodes):
            raise ValueError('Invalid target edge endpoint')
    return edge


class Preprocessor:
    def __init__(self, state):
        self.state = state

    @classmethod
    def fit(cls, graphs, target, feature_ablation='none'):
        if not graphs:
            raise ValueError('No training graphs')
        encodings = {g.encode_map_sha256 for g in graphs}
        if len(encodings) != 1:
            raise ValueError('Category maps must be harmonized before training')
        if feature_ablation not in FEATURE_ABLATIONS:
            raise ValueError('Unknown feature ablation: ' + feature_ablation)
        state = {'encoding': encodings.pop(), 'target': target,
                 'feature_ablation': feature_ablation, 'nodes': {}}
        for kind in NODE_TYPES:
            names = selected_features(kind, feature_ablation)
            x = torch.cat([features(g, kind, names) for g in graphs])
            columns = []
            for i, name in enumerate(names):
                values = x[:, i][torch.isfinite(x[:, i])]
                if name.endswith('_id'):
                    if not torch.equal(values, values.round()):
                        raise ValueError('Nonintegral category')
                    columns.append({'name': name, 'vocab': sorted(set(values.tolist()))})
                else:
                    mean = values.mean().item() if len(values) else 0.
                    std = values.std(unbiased=False).item() if len(values) else 1.
                    # Constant training columns need unit scaling, not a huge
                    # amplification when a held-out design has a different value.
                    columns.append({'name': name, 'mean': mean, 'std': std if std >= 1e-8 else 1.})
            state['nodes'][kind] = columns
        ys = []
        for g in graphs:
            y, mask = labels(g, target)
            ys.append(label_forward(y[mask], target))
        y = torch.cat(ys)
        if not len(y):
            raise ValueError('No valid training targets')
        std = y.std(unbiased=False).item()
        state['label'] = {'mean': y.mean().item(), 'std': std if std >= 1e-8 else 1.}
        return cls(state)

    def inverse(self, values):
        stats = self.state['label']
        normalized = values.double() * stats['std'] + stats['mean']
        return label_inverse(normalized, self.state['target'])

    def transform(self, graph):
        if graph.encode_map_sha256 != self.state['encoding']:
            raise ValueError('Category map mismatch')
        width = 2 * max(len(v) for v in self.state['nodes'].values())
        xs, types, offsets, total = [], [], {}, 0
        for typ, kind in enumerate(NODE_TYPES):
            names = [column['name'] for column in self.state['nodes'][kind]]
            raw = features(graph, kind, names)
            out = torch.zeros(len(raw), width)
            for i, col in enumerate(self.state['nodes'][kind]):
                values = raw[:, i]
                valid = torch.isfinite(values)
                out[:, 2*i+1] = valid.float()
                if 'vocab' in col:
                    for code, value in enumerate(col['vocab'], 1):
                        out[values == value, 2*i] = code
                else:
                    out[valid, 2*i] = ((values[valid]-col['mean'])/col['std']).float()
            offsets[kind] = total
            total += len(raw)
            xs.append(out)
            types.append(torch.full((len(raw),), typ, dtype=torch.long))
        edges, edge_types = [], []
        for i, relation in enumerate(RELATIONS):
            edge = graph[relation].edge_index.long().clone()
            for row, kind in [(0, relation[0]), (1, relation[2])]:
                if edge[row].numel() and (edge[row].min() < 0 or edge[row].max() >= graph[kind].num_nodes):
                    raise ValueError('Invalid logical endpoint')
                edge[row] += offsets[kind]
            edges.extend([edge, edge.flip(0)])
            edge_types.extend([torch.full((edge.size(1),), 2*i, dtype=torch.long),
                               torch.full((edge.size(1),), 2*i+1, dtype=torch.long)])
        y, mask = labels(graph, self.state['target'])
        stats = self.state['label']
        base = dict(x=torch.cat(xs), node_types=torch.cat(types),
                    edge_index=torch.cat(edges, dim=1), edge_types=torch.cat(edge_types))
        if TARGET_LEVELS[self.state['target']] == 'edge':
            edge = target_edge_index(graph, self.state['target'])
            if edge.size(1) != len(y):
                raise ValueError('Target edge identity/label length mismatch')
            relation = TARGET_EDGES[self.state['target']]
            edge[0] += offsets[relation[0]]
            edge[1] += offsets[relation[2]]
            edge, y = edge[:, mask], y[mask]
            transformed = label_forward(y, self.state['target'])
            y_scaled = ((transformed-stats['mean'])/stats['std']).float()
            result = Data(**base, edge_label_index=edge, edge_label=y_scaled,
                          edge_label_raw=y, target_entity_index=torch.arange(len(y)))
            result.target_level = 'edge'
            result.target_relation = '|'.join(relation)
            return result
        y_raw = torch.full((total,), float('nan'), dtype=torch.float64)
        valid = torch.zeros(total, dtype=torch.bool)
        target_kind = TARGET_NODES[self.state['target']]
        start = offsets[target_kind]
        y_raw[start:start+len(y)] = y
        valid[start:start+len(y)] = mask
        y_scaled = torch.full((total,), float('nan'))
        transformed = label_forward(y_raw[valid], self.state['target'])
        y_scaled[valid] = ((transformed-stats['mean'])/stats['std']).float()
        result = Data(**base, y=y_scaled, y_raw=y_raw, valid_mask=valid)
        result.target_level = 'node'
        result.entity_index = torch.arange(total)-start
        result.hpwl = torch.full((total,), float('nan'), dtype=torch.float64)
        names = graph['net'].x_schema
        hpwl = graph['net'].x[:, names.index('hpwl_um')].double().clone()
        hpwl[graph['net'].x[:, names.index('hpwl_valid')] != 1] = float('nan')
        result.hpwl[offsets['net']:offsets['net']+len(hpwl)] = hpwl
        if self.state['target'] == 'congestion':
            grid = graph['gate'].target_grid
            if grid.shape != (len(y), 2) or grid.dtype != torch.long:
                raise ValueError('Missing or invalid congestion grid metadata')
            result.target_grid = torch.zeros((total, 2), dtype=torch.long)
            result.target_grid[start:start+len(y)] = grid
        return result


class SchemaFeatureEncoder(nn.Module):
    """Type-specific numeric/missing projections and training-vocabulary embeddings."""
    def __init__(self, state, hidden):
        super().__init__()
        self.columns = state['nodes']
        self.numeric = nn.ModuleDict()
        self.categories = nn.ModuleDict()
        self.indices = {}
        self.hidden = hidden
        for kind, columns in self.columns.items():
            indices = [2*i for i, c in enumerate(columns) if 'vocab' not in c]
            indices += [2*i+1 for i in range(len(columns))]
            self.indices[kind] = indices
            self.numeric[kind] = nn.Linear(len(indices), hidden)
            self.categories[kind] = nn.ModuleDict({str(i): nn.Embedding(len(c['vocab'])+1, hidden)
                                                   for i, c in enumerate(columns) if 'vocab' in c})
        self.relations = nn.Embedding(2*len(RELATIONS), hidden)

    def forward(self, batch):
        out = batch.x.new_zeros((batch.num_nodes, self.hidden))
        for typ, kind in enumerate(NODE_TYPES):
            mask = batch.node_types == typ
            x = batch.x[mask]
            values = self.numeric[kind](x[:, self.indices[kind]])
            for idx, embedding in self.categories[kind].items():
                values = values + embedding(x[:, 2*int(idx)].long())
            out[mask] = values
        batch.x = out
        batch.edge_attr = self.relations(batch.edge_types)
        return batch
