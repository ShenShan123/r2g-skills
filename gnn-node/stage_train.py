"""Four-stage node-task entry point; legacy GraphHead remains the GNN backbone."""
import argparse
import json
import random
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from torch_geometric.loader import LinkNeighborLoader, NeighborLoader

from model import GraphHead
from stage_data import (CUTOFFS, FEATURE_ABLATIONS, TARGETS, TARGET_EDGES, TARGET_LEVELS, TARGET_NODES,
                        Preprocessor, SchemaFeatureEncoder, digest, labels, load_graph,
                        save_json, split_families, validate_manifest)


def prepare(args):
    cohort = json.loads(Path(args.cohort).read_text())
    source = {r['task_id']: r for rows in cohort['splits'].values() for r in rows}
    records, excluded = [], []
    for folder in sorted(Path(args.graph_root).iterdir()):
        if not folder.is_dir() or folder.name not in source:
            continue
        root = folder / 'generated'
        stats_file = root / 'statistics/four_stage_data_statistics.json'
        if not stats_file.exists():
            excluded.append({'design_id': folder.name, 'reason': 'missing_statistics'})
            continue
        stats = json.loads(stats_file.read_text())
        validation_file = root / 'four_stage.validation.json'
        validation = json.loads(validation_file.read_text()) if validation_file.exists() else {}
        if stats['status'] != 'PASS' or validation.get('status') != 'PASS':
            excluded.append({'design_id': folder.name, 'reason': 'statistics_not_pass'})
            continue
        row = source[folder.name]
        graphs = {}
        for stage in CUTOFFS:
            path = root / 'stages' / stage / 'heterograph.pt'
            graphs[stage] = {'path': str(path.resolve()), 'sha256': digest(path),
                             'validation_path': str(validation_file.resolve()),
                             'validation_sha256': digest(validation_file)}
        repo = row['source_repo_url'].lower().rstrip('/')
        repo = repo.removesuffix('.git')
        records.append({'design_id': folder.name, 'family_id': repo,
                        'source_sha256': row['normalized_source_sha256'],
                        'platform': row['platform'], 'mapped_cells': row['mapped_cells'],
                        'graphs': graphs,
                        'congestion_labels': {'path': str((root/'labels/gate_con_IR.csv').resolve()),
                                              'sha256': digest(root/'labels/gate_con_IR.csv')},
                        'node_counts': stats['stages']['route']['node_counts'],
                        'label_counts': [{k: c[k] for k in ('entity_type', 'column', 'total_count', 'mask_valid_count')}
                                         for c in stats['column_statistics']
                                         if c['stage'] == 'route' and c['tensor'] in ('y', 'edge_y')]})
    # Union repositories sharing an exact normalized source closure.
    parents = {r['family_id']: r['family_id'] for r in records}
    def root(family):
        while parents[family] != family:
            family = parents[family]
        return family
    by_source = {}
    for row in records:
        family = root(row['family_id'])
        previous = by_source.setdefault(row['source_sha256'], family)
        left, right = sorted((root(previous), family))
        parents[right] = left
    for row in records:
        row['family_id'] = root(row['family_id'])
    if args.pilot_designs:
        records = sorted(records, key=lambda r: (r['mapped_cells'], r['design_id']))[:args.pilot_designs]
    records = split_families(records, args.split_seed)
    manifest = {'schema': 'r2g_downstream_inventory_v1', 'family_reviewed': False,
                'family_rule': 'repository_plus_identical_source_closure; manual review pending',
                'pilot_subset': bool(args.pilot_designs), 'split_seed': args.split_seed,
                'records': records, 'excluded': excluded,
                'input_cohort_sha256': digest(args.cohort)}
    validate_manifest(manifest, pilot=True)
    if Path(args.output).exists():
        raise FileExistsError('Refusing to replace existing manifest')
    save_json(args.output, manifest)
    print(json.dumps({'designs': len(records), 'families': len({r['family_id'] for r in records}),
                      'splits': {s: sum(r['split'] == s for r in records) for s in ('train', 'validation', 'test')},
                      'manifest': args.output}))


def metrics(pred, truth):
    pred, truth = np.asarray(pred, dtype=float), np.asarray(truth, dtype=float)
    if not len(truth) or not np.isfinite(pred).all() or not np.isfinite(truth).all():
        raise ValueError('Empty or nonfinite evaluation')
    residual = pred-truth
    ss = float(np.square(truth-truth.mean()).sum())
    return {'n': len(truth), 'mae': float(np.abs(residual).mean()),
            'rmse': float(np.sqrt(np.square(residual).mean())),
            'r2': None if ss == 0 or len(truth) < 2 else float(1-np.square(residual).sum()/ss)}


def loader_for(data, args, training):
    if TARGET_LEVELS[args.target] == 'edge':
        indices = torch.arange(data.edge_label.size(0))
        if args.pilot and training:
            indices = indices[:min(len(indices), 128)]
        if not len(indices):
            raise ValueError('No valid target edges in a selected design')
        return LinkNeighborLoader(
            data,
            edge_label_index=data.edge_label_index[:, indices],
            edge_label=data.edge_label[indices],
            num_neighbors=[args.neighbors if training else -1]*args.layers,
            batch_size=args.batch_size,
            shuffle=training,
            num_workers=0,
        )
    seeds = torch.where(data.valid_mask)[0]
    if args.pilot and training:
        seeds = seeds[:min(len(seeds), 128)]
    if not len(seeds):
        raise ValueError('No valid targets in a selected design')
    return NeighborLoader(data, input_nodes=seeds,
                          num_neighbors=[args.neighbors if training else -1]*args.layers,
                          batch_size=args.batch_size, shuffle=training, num_workers=0)


def grid_values(pred, truth, grids):
    """Each occupied target grid contributes once, within one design only."""
    if not (len(pred) == len(truth) == len(grids)):
        raise ValueError('Grid evaluation length mismatch')
    groups = {}
    for p, y, cell in zip(pred, truth, grids):
        groups.setdefault(tuple(cell), []).append((p, y))
    predicted, observed = [], []
    for values in groups.values():
        p, y = np.asarray(values, dtype=float).T
        if not np.allclose(y, y[0], rtol=1e-6, atol=1e-8):
            raise ValueError('Inconsistent targets within one congestion grid')
        predicted.append(float(p.mean()))
        observed.append(float(y[0]))
    return predicted, observed


def fit_constants(records, data):
    """Train-only reference predictors for the gate and occupied-grid views."""
    gate, grid = [], []
    for record in records:
        if record['split'] != 'train':
            raise ValueError('Constant fitting accepts training designs only')
        d = data[record['design_id']]
        y = d.y_raw[d.valid_mask].tolist()
        _, gy = grid_values(y, y, d.target_grid[d.valid_mask].tolist())
        gate.extend(y)
        grid.extend(gy)
    if not gate or not np.isfinite(gate).all():
        raise ValueError('Missing finite training targets')
    return {view: {'mean': float(np.mean(values)), 'median': float(np.median(values)),
                   'n': len(values)} for view, values in (('gate', gate), ('occupied_grid', grid))}


def aggregate_metrics(per_design, pred, truth):
    return {'pooled': metrics(pred, truth), 'per_design': per_design,
            'macro_mae': float(np.mean([v['mae'] for v in per_design.values()]))}


def paired_hpwl(pred, truth, hpwl):
    pred, truth, hpwl = (np.asarray(a, dtype=float) for a in (pred, truth, hpwl))
    if not (pred.shape == truth.shape == hpwl.shape):
        raise ValueError('Paired HPWL identities/lengths differ')
    mask = np.isfinite(hpwl) & (hpwl >= 0)
    if not np.isfinite(pred).all() or not np.isfinite(truth).all():
        raise ValueError('Nonfinite supervised prediction/target')
    return pred[mask], truth[mask], hpwl[mask]


def evaluate(model, records, data, processor, args, device, output=None, constants=None):
    model.eval()
    by_design, predictions, identities = {}, [], []
    all_pred, all_true, losses = [], [], []
    grid_design, grid_pred, grid_true, grid_ids = {}, [], [], []
    hpwl_model, hpwl_baseline, paired_pred, paired_true, paired_base = {}, {}, [], [], []
    constant_design = {view: {name: {} for name in ('mean', 'median')}
                       for view in ('gate', 'occupied_grid')}
    with torch.no_grad():
        for record in records:
            key = record['design_id']
            graph = data[key]
            predicted, truth, seen = [], [], []
            for batch in loader_for(graph, args, False):
                if TARGET_LEVELS[args.target] == 'edge':
                    indices = batch.input_id.tolist()
                    raw = graph.edge_label_raw[indices].double()
                else:
                    n = batch.batch_size
                    selected = batch.valid_mask[:n]
                    indices = batch.n_id[:n][selected].tolist()
                    raw = batch.y_raw[:n][selected].double()
                batch = batch.to(device)
                pred, _, y = model(batch)
                pred = pred.reshape(-1)
                losses.extend(torch.nn.functional.smooth_l1_loss(pred, y, reduction='none').cpu().tolist())
                restored = processor.inverse(pred.cpu())
                if not torch.isfinite(restored).all():
                    raise ValueError('Nonfinite inverse prediction')
                predicted.extend(restored.tolist())
                truth.extend(raw.tolist())
                seen.extend(indices)
            expected = (list(range(graph.edge_label.size(0)))
                        if TARGET_LEVELS[args.target] == 'edge'
                        else torch.where(graph.valid_mask)[0].tolist())
            if sorted(seen) != expected or len(seen) != len(set(seen)):
                raise ValueError('Evaluation targets missing or counted more than once')
            by_design[key] = metrics(predicted, truth)
            all_pred.extend(predicted)
            all_true.extend(truth)
            predictions.extend(predicted)
            if TARGET_LEVELS[args.target] == 'edge':
                identities.extend((key, tuple(map(int, graph.edge_label_index[:, i]))) for i in seen)
            else:
                identities.extend((key, int(graph.entity_index[i])) for i in seen)
            if args.target == 'wirelength':
                pp, yy, bb = paired_hpwl(predicted, truth, graph.hpwl[seen].tolist())
                if len(yy):
                    hpwl_model[key], hpwl_baseline[key] = metrics(pp, yy), metrics(bb, yy)
                    paired_pred.extend(pp); paired_true.extend(yy); paired_base.extend(bb)
            if args.target == 'congestion':
                grids = graph.target_grid[seen].tolist()
                gp, gy = grid_values(predicted, truth, grids)
                grid_design[key] = metrics(gp, gy)
                grid_pred.extend(gp)
                grid_true.extend(gy)
                grid_ids.extend(grids)
                if constants:
                    for view, observed in (('gate', truth), ('occupied_grid', gy)):
                        for name in ('mean', 'median'):
                            constant_design[view][name][key] = metrics(
                                np.full(len(observed), constants[view][name]), observed)
    result = {'unit': TARGETS[args.target][1], 'loss_scaled': float(np.mean(losses)),
              'pooled': metrics(all_pred, all_true), 'per_design': by_design,
              'macro_mae': float(np.mean([v['mae'] for v in by_design.values()]))}
    if grid_design:
        result['occupied_grid'] = {'pooled': metrics(grid_pred, grid_true),
                                  'per_design': grid_design,
                                  'macro_mae': float(np.mean([v['mae'] for v in grid_design.values()])),
                                  'aggregation': 'mean_prediction_per_design_postroute_gate_origin_grid',
                                  'scope': 'grids containing valid canonical gates, not all die grids'}
    if paired_true:
        result['paired_hpwl'] = {
            'scope': 'identical valid target and finite nonnegative input-stage HPWL subset',
            'excluded_valid_targets': len(all_true)-len(paired_true),
            'model': aggregate_metrics(hpwl_model, paired_pred, paired_true),
            'hpwl': aggregate_metrics(hpwl_baseline, paired_base, paired_true)}
    if constants:
        result['train_only_constants'] = {'fitted': constants}
        for view, observed in (('gate', all_true), ('occupied_grid', grid_true)):
            result['train_only_constants'][view] = {
                name: aggregate_metrics(constant_design[view][name],
                    np.full(len(observed), constants[view][name]), observed)
                for name in ('mean', 'median')}
    if output:
        if TARGET_LEVELS[args.target] == 'edge':
            entity = '|'.join(TARGET_EDGES[args.target])
            endpoint = np.asarray([i[1] for i in identities], dtype=np.int64)
            extra = {'source_index': endpoint[:, 0], 'destination_index': endpoint[:, 1]}
        else:
            entity = TARGET_NODES[args.target]
            extra = {entity+'_index': np.array([i[1] for i in identities])}
        if grid_ids:
            extra['target_grid'] = np.asarray(grid_ids)
        np.savez_compressed(output, pred=np.asarray(predictions), truth=np.asarray(all_true),
                            design_id=np.array([i[0] for i in identities]),
                            entity_type=np.array(entity), **extra)
    return result


def run(args):
    started = time.monotonic()
    torch.set_num_threads(args.cpu_threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.device != 'cpu':
        torch.cuda.manual_seed_all(args.seed)
    if args.layers < 1 or args.epochs < 1:
        raise ValueError('Positive layer/epoch counts required')
    manifest = json.loads(Path(args.manifest).read_text())
    validate_manifest(manifest, pilot=args.pilot)
    if args.target == 'congestion':
        evidence = manifest.get('congestion_audit', {})
        if not evidence or digest(evidence['path']) != evidence['sha256']:
            raise ValueError('Congestion training requires matching independent label audit')
        audit = json.loads(Path(evidence['path']).read_text())
        audited = {r['design_id']: r for r in audit['records']}
        if audit['status'] != 'PASS':
            raise ValueError('Congestion audit did not pass')
        for record in manifest['records']:
            report = audited[record['design_id']]
            sidecar = record['congestion_labels']
            if (report['status'] != 'PASS' or
                report['graph_sha256'] != {s: info['sha256'] for s, info in record['graphs'].items()} or
                report['inputs'].get(sidecar['path']) != sidecar['sha256']):
                raise ValueError('Congestion audit does not cover current artifacts')
    if not args.pilot and manifest.get('pilot_subset'):
        raise ValueError('Pilot subset cannot become a formal dataset')
    if args.finalize and args.pilot:
        raise ValueError('Pilot does not inspect held-out test metrics')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / 'last.pt'
    if any(out.iterdir()) and not args.resume:
        raise FileExistsError('Nonempty run directory: use --resume or a new output')
    if args.resume and not checkpoint.exists():
        raise FileNotFoundError('No checkpoint to resume')
    identity = {k: v for k, v in vars(args).items() if k not in ('resume', 'finalize', 'command', 'output')}
    identity['manifest_sha256'] = digest(args.manifest)
    identity['code_sha256'] = {p.name: digest(p) for p in Path(__file__).parent.glob('*.py')}
    saved = torch.load(checkpoint, map_location='cpu', weights_only=False) if args.resume else None
    if saved and saved['identity'] != identity:
        raise ValueError('Resume configuration/input/code differs')
    records = manifest['records']
    selected = [r for r in records if args.finalize or r['split'] != 'test']
    raw = {r['design_id']: load_graph(r, args.stage, args.target) for r in selected}
    excluded_no_target = []
    eligible = []
    for record in selected:
        _, mask = labels(raw[record['design_id']], args.target)
        if mask.any():
            eligible.append(record)
        else:
            excluded_no_target.append({'design_id': record['design_id'],
                                       'split': record['split'],
                                       'reason': 'no_valid_' + args.target + '_target'})
            del raw[record['design_id']]
    selected = eligible
    splits = {s: [r for r in selected if r['split'] == s] for s in ('train', 'validation', 'test')}
    if not splits['train'] or not splits['validation']:
        raise ValueError('Target eligibility left an empty development split')
    processor = Preprocessor(saved['preprocessor']) if saved else Preprocessor.fit(
        [raw[r['design_id']] for r in splits['train']], args.target,
        getattr(args, 'feature_ablation', 'none'))
    data = {key: processor.transform(g) for key, g in raw.items()}
    del raw
    constants = fit_constants(splits['train'], data) if args.target == 'congestion' else None
    if constants:
        save_json(out / 'train_constants.json', constants)
    model_args = SimpleNamespace(hid_dim=args.hidden, task='regression',
                                 task_level=TARGET_LEVELS[args.target],
                                 model='gine', num_gnn_layers=args.layers if args.model == 'gine' else 0,
                                 src_dst_agg='sumabs' if TARGET_LEVELS[args.target] == 'edge' else 'add',
                                 num_head_layers=2, use_bn=False,
                                 act_fn='relu', dropout=.1, layer_norm=True)
    model = GraphHead(model_args, SchemaFeatureEncoder(processor.state, args.hidden))
    device = torch.device(args.device)
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    best, history, start = float('inf'), [], 0
    if saved:
        model.load_state_dict(saved['model'])
        optimizer.load_state_dict(saved['optimizer'])
        best, history, start = saved['best'], saved['history'], saved['epoch']+1
        torch.set_rng_state(saved['torch_rng'])
        random.setstate(saved['python_rng'])
        np.random.set_state(saved['numpy_rng'])
        if args.device != 'cpu':
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
        torch.save(saved['best_model'], out / 'best.pt')
    save_json(out / 'config.json', identity)
    save_json(out / 'preprocessor.json', processor.state)
    preparation_seconds = time.monotonic()-started
    for epoch in range(start, args.epochs):
        epoch_started = time.monotonic()
        model.train()
        order = list(splits['train'])
        random.shuffle(order)
        losses = []
        for record in order:
            for batch in loader_for(data[record['design_id']], args, True):
                optimizer.zero_grad()
                pred, _, y = model(batch.to(device))
                loss = torch.nn.functional.smooth_l1_loss(pred.reshape(-1), y)
                if not torch.isfinite(loss):
                    raise ValueError('Nonfinite training loss')
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
                optimizer.step()
                losses.append(float(loss.detach()))
            save_json(out / 'status.json', {'phase': 'training', 'epoch': epoch+1,
                      'active_design': record['design_id'], 'batches_completed': len(losses)})
        train_seconds = time.monotonic()-epoch_started
        val = evaluate(model, splits['validation'], data, processor, args, device, constants=constants)
        history.append({'epoch': epoch+1, 'train_loss_batch_mean': float(np.mean(losses)), 'validation': val,
                        'train_seconds': train_seconds, 'epoch_seconds': time.monotonic()-epoch_started})
        selection = val['occupied_grid']['macro_mae'] if args.target == 'congestion' else val['loss_scaled']
        if selection < best:
            best = selection
            torch.save(model.state_dict(), out / 'best.tmp')
            (out / 'best.tmp').replace(out / 'best.pt')
        payload = {'identity': identity, 'preprocessor': processor.state, 'epoch': epoch,
                   'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                   'best_model': torch.load(out / 'best.pt', map_location='cpu', weights_only=True),
                   'best': best, 'history': history, 'torch_rng': torch.get_rng_state(),
                   'python_rng': random.getstate(), 'numpy_rng': np.random.get_state(),
                   'cuda_rng': torch.cuda.get_rng_state_all() if args.device != 'cpu' else []}
        torch.save(payload, out / 'last.tmp')
        (out / 'last.tmp').replace(checkpoint)
        save_json(out / 'history.json', history)
        print(json.dumps({'epoch': epoch+1, 'validation_mae': val['macro_mae'], 'unit': val['unit']}), flush=True)
    model.load_state_dict(torch.load(out / 'best.pt', map_location=device, weights_only=True))
    result = {'status': 'pilot_complete_not_formal' if args.pilot else 'development_complete',
              'checkpoint_selection': 'validation_occupied_grid_macro_mae' if args.target == 'congestion' else 'validation_scaled_loss',
              'parameters': sum(p.numel() for p in model.parameters()), 'test_evaluated': False,
              'target_eligibility': {
                  'eligible_designs': {s: len(rows) for s, rows in splits.items()},
                  'excluded_no_valid_target': excluded_no_target},
              'validation': evaluate(model, splits['validation'], data, processor, args, device,
                                     out / 'validation_predictions.npz', constants=constants)}
    if args.finalize:
        result.update(status='complete', test_evaluated=True,
                      test=evaluate(model, splits['test'], data, processor, args, device,
                                    out / 'test_predictions.npz', constants=constants))
    result['timing'] = {'preparation_seconds': preparation_seconds,
                        'invocation_seconds': time.monotonic()-started,
                        'peak_cuda_allocated_bytes': torch.cuda.max_memory_allocated(device) if device.type=='cuda' else 0}
    save_json(out / 'result.json', result)
    save_json(out / 'status.json', {'phase': result['status'], 'epochs_complete': args.epochs,
                                  'test_evaluated': result['test_evaluated']})
    print(json.dumps({'status': result['status'], 'result': str(out / 'result.json')}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--cohort', required=True)
    p.add_argument('--graph-root', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--split-seed', type=int, default=20260917)
    p.add_argument('--pilot-designs', type=int, default=0)
    p = sub.add_parser('train')
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--stage', choices=CUTOFFS, required=True)
    p.add_argument('--target', choices=TARGETS, required=True)
    p.add_argument('--model', choices=('gine', 'mlp'), default='gine')
    p.add_argument('--device', default='cpu')
    p.add_argument('--epochs', type=int, default=2)
    p.add_argument('--hidden', type=int, default=32)
    p.add_argument('--layers', type=int, default=3)
    p.add_argument('--neighbors', type=int, default=10)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--cpu-threads', type=int, default=2)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--feature-ablation', choices=FEATURE_ABLATIONS, default='none')
    p.add_argument('--pilot', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    prepare(args) if args.command == 'prepare' else run(args)


if __name__ == '__main__':
    main()
