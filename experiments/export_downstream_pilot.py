#!/usr/bin/env python3
"""Serial three-size graph export pilot using the existing Exp4 converter.

Reads completed physical artifacts; never runs synthesis/place/route. Existing
experiments are read-only. Each case has its own checkpoint and conversion log.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import run_experiment4_graph_conversion as conversion
from experiment4_protocol import size_band


def select_cases(inventory):
    existing = {r['task_id'] for r in inventory['graph_records'] if r['status'] == 'recorded_pass'}
    candidates = sorted((r for r in inventory['records'] if r['export_candidate'] and
                         r['task_id'] not in existing), key=lambda r: (r['mapped_cells'], r['task_id']))
    picked, families = [], set()
    for band in ('small_100_499', 'medium_500_1999', 'large_2000_10000'):
        row = next(r for r in candidates if size_band(r['mapped_cells']) == band and
                   r['provisional_family_id'] not in families)
        picked.append(dict(task_id=row['task_id'], mapped_cells=row['mapped_cells'],
            platform='sky130hd', run_dir=row['run_dir'], baseline_result=row['result_path'],
            baseline_result_sha256=row['result_sha256'], source_repo_url=row['repo'],
            source_commit=row['commit'], top_module=row['top_module'],
            normalized_source_sha256=row['normalized_source_sha256'],
            source_closure_sha256=row['source_closure_sha256'],
            family_id=row['provisional_family_id'], size_band=band,
            coverage_state=row['coverage_state'], physical_clean_flag=row['physical_clean_flag'],
            timing_label_review_required=row['timing_label_review_required']))
        families.add(row['provisional_family_id'])
    return picked


def bounded_command(command, log_path, timeout):
    """Timeout the full command group, including children of /usr/bin/time."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resources_path = log_path.with_suffix(log_path.suffix + '.resources')
    started = time.monotonic()
    timed_out = False
    with log_path.open('w') as log:
        log.write(json.dumps(command) + '\n')
        log.flush()
        with subprocess.Popen(['/usr/bin/time', '-f',
            'peak_rss_kib=%M\nuser_seconds=%U\nsystem_seconds=%S', '-o', str(resources_path),
            *command], stdout=log, stderr=subprocess.STDOUT, start_new_session=True) as proc:
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                code = 124
    resources = {}
    if resources_path.is_file():
        for line in resources_path.read_text().splitlines():
            key, _, value = line.partition('=')
            try:
                resources[key] = float(value)
            except ValueError:
                continue
    return dict(command=command, returncode=code, elapsed_seconds=round(time.monotonic()-started, 3),
                error='timeout' if timed_out else '', log=str(log_path), resources=resources)


def fingerprint(runtime):
    files = [Path(__file__), Path(conversion.__file__)]
    scripts = runtime / 'r2g-skills/def-graph/scripts'
    files += sorted(p for p in scripts.rglob('*') if p.is_file() and p.suffix in ('.py', '.sh', '.tcl', '.csv'))
    return {str(p.resolve()): conversion.sha256_file(p) for p in files}


def timing_is_confirmed_empty(state, case):
    commands = state.get('commands', [])
    if len(commands) != 2 or commands[0]['returncode'] != 0 or commands[1]['returncode'] != 1:
        return False
    for name in ('paths_max.rpt', 'paths_min.rpt'):
        path = case / 'time_rpt' / name
        if not path.is_file() or path.read_text().strip() != 'No paths found.':
            return False
    log = case / 'logs/timing.log'
    return log.is_file() and 'OpenSTA produced no paths (max=0, min=0)' in log.read_text()


def materialize_dataset(row, root, runtime, timeout):
    state = conversion.materialize_one(row, root, runtime, Path(sys.executable), timeout)
    case = root / 'inputs' / row['task_id']
    if state['status'] == 'ready' or not timing_is_confirmed_empty(state, case):
        return state
    # Empty constrained-path reports are missing supervision, not fake zero slack.
    # Preserve the original strict materializer failure and only enable RC tasks.
    conversion.write_json(case / 'timing_unavailable_evidence.json', state)
    path = conversion.find_sample_config(case)
    config = conversion.read_json(path)
    config.update(timing_enabled=False, timing_require_manifest=False,
                  timing_use_report_path_edges=False)
    for key in ('timing_max_rpt', 'timing_min_rpt', 'timing_manifest'):
        config.pop(key, None)
    conversion.write_json(path, config)
    artifacts = {}
    for field in ('yosys_v', 'floorplan_def', 'place_def', 'cts_def', 'route_def',
                  'spef', 'sdc', 'encode_map', 'raw_manifest'):
        artifact = Path(config[field])
        if not artifact.is_file():
            raise ValueError('Missing RC materialization input: ' + field)
        artifacts[field] = dict(path=str(artifact), bytes=artifact.stat().st_size,
                                sha256=conversion.sha256_file(artifact))
    state.update(status='ready', task_id=row['task_id'],
        materializer_version=conversion.MATERIALIZER_VERSION,
        dataset_timing_status='unavailable_no_constrained_paths',
        timing_targets_approved=False, config=str(path), config_sha256=conversion.sha256_file(path),
        baseline_result_sha256=row['baseline_result_sha256'], artifacts=artifacts,
        toolchain=dict(orfs_root=os.environ['ORFS_ROOT'], openroad_exe=os.environ['OPENROAD_EXE'],
                       openroad_sha256=conversion.sha256_file(Path(os.environ['OPENROAD_EXE']))))
    conversion.write_json(case / 'input_attestation.json', state)
    return state


def check_adapter(root, row, expected_encoding):
    import torch
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gnn-node'))
    from stage_data import load_graph, labels
    torch.set_num_threads(2)
    method = root / 'methods/r2g-frozen-v3' / row['task_id']
    base = method / 'generated'
    validation = base / 'four_stage.validation.json'
    record = dict(design_id=row['task_id'], family_id=row['family_id'],
                  source_sha256=row['normalized_source_sha256'], mapped_cells=row['mapped_cells'],
                  platform=row['platform'], graphs={})
    counts, schemas = {}, {}
    for stage in ('cts', 'route'):
        graph = base / 'stages' / stage / 'heterograph.pt'
        record['graphs'][stage] = dict(path=str(graph), sha256=conversion.sha256_file(graph),
            validation_path=str(validation), validation_sha256=conversion.sha256_file(validation))
        data = load_graph(record, stage)
        if data.encode_map_sha256 != expected_encoding:
            raise ValueError('Encoding map differs from the existing 24 graphs')
        counts[stage] = {}
        for target in ('wirelength', 'ground_cap'):
            _, valid = labels(data, target)
            counts[stage][target] = int(valid.sum())
            if not valid.any():
                raise ValueError(f'No valid {target} labels')
        schemas[stage] = {kind: list(data[kind].x_schema) for kind in data.node_types}
    return dict(record=record, valid_target_counts=counts, feature_schemas=schemas,
                encode_map_sha256=expected_encoding, status='PASS',
                timing_targets_approved=False, trained=False)


def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inventory = conversion.read_json(args.inventory)
        rows = select_cases(inventory)
        reference = conversion.read_json(args.reference_config)
        expected_encoding = conversion.sha256_file(Path(reference['encode_map']))
        identity = dict(inventory_sha256=conversion.sha256_file(args.inventory),
            runtime_sha256=fingerprint(args.runtime), reference_encoding_sha256=expected_encoding,
            openroad_sha256=conversion.sha256_file(args.openroad),
            python=str(Path(sys.executable).resolve()), timeout=args.timeout,
            cpu_set=args.cpu_set, cases=rows,
            quarantine_evidence={str(p.resolve()): conversion.sha256_file(p) for p in args.quarantine_evidence})
        quarantined = {}
        for path in args.quarantine_evidence:
            evidence = conversion.read_json(path)
            previous = conversion.read_json(path.parent.parent / 'identity.json')
            matched = next((r for r in rows if r['task_id'] == evidence['task_id']), None)
            if evidence.get('status') != 'FAILED' or matched not in previous['cases']:
                raise ValueError('Quarantine evidence must be a failed attempt of the identical case')
            for filename, checksum in identity['runtime_sha256'].items():
                if args.runtime.resolve() in Path(filename).parents:
                    if previous['runtime_sha256'].get(filename) != checksum:
                        raise ValueError('Quarantine converter version differs')
            quarantined[evidence['task_id']] = dict(evidence, quarantined_from=str(path.resolve()))
        identity_path = root / 'identity.json'
        if identity_path.exists():
            if conversion.read_json(identity_path) != identity:
                raise ValueError('Source/code/config differs: do not reuse this export checkpoint')
        else:
            conversion.write_json(identity_path, identity)
            conversion.write_json(root / 'cohort.json', dict(
                purpose='dataset export software pilot; not an experiment4 test or a GNN split',
                family_reviewed=False, splits={'development': rows}))
        cpus = {int(s) for s in args.cpu_set.split(',')}
        if not cpus <= os.sched_getaffinity(0):
            raise ValueError('Requested CPUs outside allowed affinity')
        os.sched_setaffinity(0, cpus)
        os.environ.update(ORFS_ROOT=str(args.orfs), OPENROAD_EXE=str(args.openroad),
                          OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='2',
                          NUM_CORES='2', CUDA_VISIBLE_DEVICES='')
        # Reuse the established materialization/export contract with group cleanup.
        conversion.run_command = bounded_command
        states = []
        for row in rows:
            case_state = root / 'checkpoints' / (row['task_id'] + '.json')
            if row['task_id'] in quarantined:
                state = quarantined[row['task_id']]
                conversion.write_json(case_state, state)
                states.append(state)
                print(row['task_id'] + ': quarantined prior failure; not rerun', flush=True)
                continue
            if case_state.exists():
                saved = conversion.read_json(case_state)
                if saved.get('status') == 'PASS':
                    # Resume still checks tensors/validation through the adapter.
                    check_adapter(root, row, expected_encoding)
                    states.append(saved)
                    continue
                raise ValueError('Failed case requires inspection; artifacts are retained: ' + str(case_state))
            state = dict(task_id=row['task_id'], status='running', stage='materialize',
                         started_at=datetime.now(timezone.utc).isoformat())
            conversion.write_json(root / 'status.json', dict(completed=states, active=state))
            start = time.monotonic()
            try:
                print(row['task_id'] + ': materialize', flush=True)
                materialized = materialize_dataset(row, root, args.runtime, args.timeout)
                if materialized['status'] != 'ready':
                    raise ValueError('Input materialization failed; see inputs logs')
                config = conversion.read_json(Path(materialized['config']))
                if conversion.sha256_file(Path(config['encode_map'])) != expected_encoding:
                    raise ValueError('Materialized encoding differs; do not merge graphs')
                state['stage'] = 'convert'
                conversion.write_json(root / 'status.json', dict(completed=states, active=state))
                print(row['task_id'] + ': convert', flush=True)
                result = conversion.run_one(row, root, args.runtime, Path(sys.executable),
                                            'r2g-frozen-v3', None, args.timeout)
                if result['status'] != 'completed':
                    raise ValueError('Conversion failed; see method logs')
                state['stage'] = 'validate'
                conversion.write_json(root / 'status.json', dict(completed=states, active=state))
                score = conversion.evaluate_one(row, root, args.runtime, Path(sys.executable),
                                                'r2g-frozen-v3', args.timeout)
                if not score['strict_pass']:
                    raise ValueError('Graph validation failed; not a physical signoff failure')
                state['adapter'] = check_adapter(root, row, expected_encoding)
                state['status'] = 'PASS'
            except Exception as exc:
                state.update(status='FAILED', error=str(exc))
            state.update(elapsed_seconds=round(time.monotonic()-start, 3),
                         completed_at=datetime.now(timezone.utc).isoformat())
            conversion.write_json(case_state, state)
            states.append(state)
            print(row['task_id'] + ': ' + state['status'], flush=True)
        final = dict(status='pilot_complete' if len(states) == len(rows) and
                     all(s['status'] == 'PASS' for s in states) else 'needs_inspection',
                     completed=states, active=None, no_orfs_rerun=True, no_training=True)
        conversion.write_json(root / 'status.json', final)
        print(json.dumps({k: v for k, v in final.items() if k != 'completed'}))
        return 0 if final['status'] == 'pilot_complete' else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('inventory', 'runtime', 'reference-config', 'output', 'orfs', 'openroad'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--timeout', type=int, default=900)
    p.add_argument('--cpu-set', default='144,145')
    p.add_argument('--quarantine-evidence', type=Path, action='append', default=[])
    raise SystemExit(run(p.parse_args()))
