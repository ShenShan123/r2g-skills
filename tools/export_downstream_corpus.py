#!/usr/bin/env python3
"""Bounded serial expansion from existing physical artifacts, without ORFS reruns."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import time

import export_downstream_pilot as pilot
import run_experiment4_graph_conversion as conversion
from experiment4_protocol import size_band

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gnn-node'))
from congestion_audit import audit_record
from stage_data import digest, labels, load_graph, save_json

BANDS = ('small_100_499', 'medium_500_1999', 'large_2000_10000')


def now():
    return datetime.now(timezone.utc).isoformat()


def queue_rows(inventory):
    """Interleave sizes and prioritize distinct families, without outcome selection."""
    pending = sorted((r for r in inventory['records'] if r['export_candidate']),
                     key=lambda r: (r['mapped_cells'], r['task_id']))
    queue, seen = [], set()
    while pending:
        for band in BANDS:
            matching = [r for r in pending if size_band(r['mapped_cells']) == band]
            if not matching:
                continue
            row = next((r for r in matching if r['provisional_family_id'] not in seen), matching[0])
            if row['source_verification']['status'] != 'PASS':
                raise ValueError('Unverified source: '+row['task_id'])
            pending.remove(row)
            seen.add(row['provisional_family_id'])
            queue.append(dict(task_id=row['task_id'], mapped_cells=row['mapped_cells'],
                platform='sky130hd', run_dir=row['run_dir'], baseline_result=row['result_path'],
                baseline_result_sha256=row['result_sha256'], source_repo_url=row['repo'],
                source_commit=row['commit'], top_module=row['top_module'],
                normalized_source_sha256=row['normalized_source_sha256'],
                source_closure_sha256=row['source_closure_sha256'],
                structural_source_sha256=row['structural_source_sha256'],
                family_id=row['provisional_family_id'], size_band=band,
                coverage_state=row['coverage_state'], physical_clean_flag=row['physical_clean_flag'],
                timing_label_review_required=row['timing_label_review_required'], artifacts=row['artifacts']))
    return queue


def family_review(rows):
    groups, tops = defaultdict(list), defaultdict(list)
    for row in rows:
        groups[row['family_id']].append(row)
        tops[row['top_module'].lower()].append(row)
    return {'family_reviewed': False,
            'rule': 'same repository OR exact normalized source OR identifier-anonymized exact source',
            'groups': [{'family_id': key, 'design_ids': [r['task_id'] for r in values],
                        'repos': sorted({r['source_repo_url'] for r in values}),
                        'top_modules': sorted({r['top_module'] for r in values}),
                        'review_status': 'pending'} for key, values in sorted(groups.items())],
            'same_top_across_groups_review_only': [
                {'top_module': top, 'design_ids': [r['task_id'] for r in values],
                 'family_ids': sorted({r['family_id'] for r in values})}
                for top, values in sorted(tops.items()) if len({r['family_id'] for r in values}) > 1],
            'note': 'Identical top names alone do not establish source relatedness; no split assigned.'}


def plan(args):
    out = args.output.resolve()
    if out.exists():
        raise FileExistsError('Use a new plan directory')
    inventory = json.loads(args.inventory.read_text())
    rows = queue_rows(inventory)
    reference = json.loads(args.reference_config.read_text())
    value = {'schema': 'r2g_downstream_corpus_queue_v1', 'created_at': now(),
             'inventory_path': str(args.inventory.resolve()), 'inventory_sha256': digest(args.inventory),
             'runtime': str(args.runtime.resolve()),
             'encoding_sha256': digest(reference['encode_map']),
             'orfs': str(args.orfs.resolve()), 'openroad': str(args.openroad.resolve()),
             'openroad_sha256': digest(args.openroad),
             'cpu_set': args.cpu_set, 'timeout_seconds': args.timeout_seconds,
             'minimum_free_gib': args.minimum_free_gib,
             'review_coverage_below': .8,
             'coverage_rule': 'review flag only, not a finalized dataset eligibility cutoff',
             'runtime_hashes': pilot.fingerprint(args.runtime),
             'code_hashes': {str(p): digest(p) for p in [Path(__file__),
                            Path(audit_record.__code__.co_filename), Path(load_graph.__code__.co_filename)]},
             'family_reviewed': False, 'no_training': True, 'no_physical_implementation': True,
             'rows': rows}
    save_json(out/'plan.json', value)
    save_json(out/'cohort.json', {'purpose': 'downstream data export, not model evaluation or train split',
                                'splits': {'development': rows}})
    review = family_review(rows)
    save_json(out/'family_review.json', review)
    lines = ['# Family Review Queue', '',
             'No training split assigned. All groups still require review.', '',
             '| Family | Designs | Repositories |', '| --- | ---: | --- |']
    lines.extend(f"| {g['family_id']} | {len(g['design_ids'])} | {', '.join(g['repos'])} |" for g in review['groups'])
    (out/'family_review.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'planned': len(rows), 'families': len(review['groups']),
                      'size_counts': dict(Counter(r['size_band'] for r in rows)),
                      'first_12': [r['task_id'] for r in rows[:12]]}), flush=True)


def make_record(root, row, encoding):
    base = root/'methods/r2g-frozen-v3'/row['task_id']/'generated'
    validation = base/'four_stage.validation.json'
    record = {'design_id': row['task_id'], 'family_id': row['family_id'],
              'source_sha256': row['normalized_source_sha256'], 'platform': row['platform'],
              'mapped_cells': row['mapped_cells'], 'split': 'unassigned', 'graphs': {}}
    counts = {}
    for stage in ('cts', 'route'):
        path = base/'stages'/stage/'heterograph.pt'
        record['graphs'][stage] = {'path': str(path), 'sha256': digest(path),
                                  'validation_path': str(validation), 'validation_sha256': digest(validation)}
        graph = load_graph(record, stage)
        if graph.encode_map_sha256 != encoding:
            raise ValueError('Encoding differs from planned corpus')
        counts[stage] = {}
        for target in ('wirelength', 'congestion'):
            _, mask = labels(graph, target)
            counts[stage][target] = {'valid': int(mask.sum()), 'total': len(mask),
                                     'fraction': float(mask.sum())/len(mask) if len(mask) else 0.}
    return record, counts


def collect(root, plan_value, active=None, status='running'):
    states, records, audits = [], [], []
    for row in plan_value['rows']:
        checkpoint = root/'checkpoints'/(row['task_id']+'.json')
        if not checkpoint.is_file():
            continue
        state = json.loads(checkpoint.read_text())
        states.append(state)
        if state['status'] == 'PASS':
            records.append(state['record'])
            audits.append(state['audit'])
    summary = {'status': status, 'updated_at': now(), 'planned': len(plan_value['rows']),
               'processed': len(states), 'remaining': len(plan_value['rows'])-len(states),
               'counts': dict(Counter(s['status'] for s in states)),
               'readiness': dict(Counter(s.get('readiness', 'failed') for s in states)),
               'active': active, 'elapsed_completed_seconds': sum(s['elapsed_seconds'] for s in states),
               'valid_gates': sum(a['gate_distribution']['n'] for a in audits),
               'occupied_grids': sum(a['occupied_grid_distribution']['n'] for a in audits),
               'family_reviewed': False, 'test_evaluated': False, 'training_started': False}
    save_json(root/'status.json', summary)
    save_json(root/'available_graphs.json', {'family_reviewed': False, 'split_assigned': False,
              'records': records, 'excluded_or_failed': [s for s in states if s['status'] != 'PASS']})
    return summary


def run(args):
    root = args.output.resolve()
    value = json.loads((root/'plan.json').read_text())
    cpus = {int(v) for v in value['cpu_set'].split(',')}
    if not cpus <= os.sched_getaffinity(0):
        raise ValueError('CPU set outside allowed affinity')
    os.sched_setaffinity(0, cpus)
    os.environ.update(ORFS_ROOT=value['orfs'], OPENROAD_EXE=value['openroad'],
                      OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='2',
                      NUM_CORES='2', CUDA_VISIBLE_DEVICES='')
    import torch
    torch.set_num_threads(2)
    for mapping in (value['runtime_hashes'], value['code_hashes'], {value['openroad']: value['openroad_sha256']}):
        for path, checksum in mapping.items():
            if digest(path) != checksum:
                raise ValueError('Queue code/runtime changed: '+path)
    conversion.run_command = pilot.bounded_command
    runtime = Path(value['runtime'])
    with (root/'export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempts = 0
        for row in value['rows']:
            checkpoint = root/'checkpoints'/(row['task_id']+'.json')
            if checkpoint.exists():
                continue  # failed cases retained for explicit inspection, not silently retried
            if attempts >= args.limit:
                break
            if shutil.disk_usage(root).free < value['minimum_free_gib']*1024**3:
                collect(root, value, status='paused_low_disk')
                return
            attempts += 1
            started = time.monotonic()
            active = {'task_id': row['task_id'], 'stage': 'materialize', 'started_at': now()}
            state = {'task_id': row['task_id'], 'size_band': row['size_band'], 'started_at': active['started_at']}
            collect(root, value, active)
            print(row['task_id']+': materialize', flush=True)
            try:
                if digest(row['baseline_result']) != row['baseline_result_sha256']:
                    raise ValueError('Baseline result changed')
                hashes = {}
                for info in row['artifacts'].values():
                    path = Path(info['path'])
                    if path.stat().st_size != info['bytes']:
                        raise ValueError('Raw artifact size changed: '+str(path))
                    hashes[str(path)] = digest(path)
                save_json(root/'raw_input_hashes'/(row['task_id']+'.json'), hashes)
                materialized = pilot.materialize_dataset(row, root, runtime, value['timeout_seconds'])
                if materialized['status'] != 'ready':
                    raise ValueError('Input materialization failed; see inputs logs')
                active['stage'] = 'convert'
                collect(root, value, active)
                result = conversion.run_one(row, root, runtime, Path(sys.executable),
                                            'r2g-frozen-v3', None, value['timeout_seconds'])
                if result['status'] != 'completed':
                    raise ValueError('Conversion failed; see method logs')
                active['stage'] = 'validate'
                collect(root, value, active)
                score = conversion.evaluate_one(row, root, runtime, Path(sys.executable),
                                               'r2g-frozen-v3', value['timeout_seconds'])
                if not score['strict_pass']:
                    raise ValueError('Structural validation failed')
                record, counts = make_record(root, row, value['encoding_sha256'])
                active['stage'] = 'independent_congestion_audit'
                collect(root, value, active)
                record, audit = audit_record(record)
                review = any(c['fraction'] < value['review_coverage_below']
                             for stage in counts.values() for c in stage.values())
                state.update(status='PASS', record=record, audit=audit, label_counts=counts,
                             readiness='review_label_coverage' if review else 'ready_for_family_review')
            except Exception as exc:
                state.update(status='FAILED', failed_stage=active['stage'],
                             error=type(exc).__name__+': '+str(exc))
            state.update(elapsed_seconds=round(time.monotonic()-started, 3), completed_at=now())
            save_json(checkpoint, state)
            print(row['task_id']+': '+state['status']+' '+state.get('error', ''), flush=True)
            collect(root, value)
        summary = collect(root, value, status='batch_complete')
        if summary['remaining'] == 0:
            summary = collect(root, value, status='queue_complete')
        print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan')
    for name in ('inventory', 'runtime', 'reference-config', 'output', 'orfs', 'openroad'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--cpu-set', default='144,145')
    p.add_argument('--timeout-seconds', type=int, default=900)
    p.add_argument('--minimum-free-gib', type=float, default=10.)
    p = sub.add_parser('run')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=12)
    args = parser.parse_args()
    if args.command == 'plan':
        if args.timeout_seconds <= 0 or args.minimum_free_gib < 0:
            raise ValueError('Invalid resource bounds')
        plan(args)
    else:
        if args.limit <= 0:
            raise ValueError('Positive batch limit required')
        run(args)


if __name__ == '__main__':
    main()
