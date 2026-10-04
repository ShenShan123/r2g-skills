"""Run the frozen scale and geometry ablations with resource-aware GPU scheduling."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


TARGETS = ('wirelength', 'hold_slack', 'effective_resistance')
SEEDS = (42, 43, 44)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def jobs(full_manifest, manifest25, manifest50):
    conditions = (
        ('scale25', manifest25, 'none'),
        ('scale50', manifest50, 'none'),
        ('no_geometry', full_manifest, 'no_physical_geometry'),
    )
    return [dict(name=f'{condition}_cts_{target}_gine_seed{seed}', condition=condition,
                 manifest=str(Path(manifest).resolve()), feature_ablation=ablation,
                 stage='cts', target=target, model='gine', seed=seed)
            for condition, manifest, ablation in conditions
            for target in TARGETS for seed in SEEDS]


def scale_jobs(scale_manifests):
    return [dict(name=f'scale{percent}_cts_{target}_gine_seed{seed}',
                 condition=f'scale{percent}', manifest=str(Path(manifest).resolve()),
                 feature_ablation='none', stage='cts', target=target, model='gine', seed=seed)
            for percent, manifest in scale_manifests
            for target in TARGETS for seed in SEEDS]


def gpu_info(index):
    text = subprocess.check_output(['nvidia-smi', '-i', str(index),
        '--query-gpu=uuid,name,memory.free,utilization.gpu',
        '--format=csv,noheader,nounits'], text=True)
    uuid, name, free, util = (part.strip() for part in text.strip().split(','))
    pids = subprocess.check_output(['nvidia-smi', '-i', str(index),
        '--query-compute-apps=pid', '--format=csv,noheader,nounits'], text=True)
    foreign = []
    for value in pids.splitlines():
        value = value.strip()
        if not value:
            continue
        pid = int(value)
        try:
            if os.stat(f'/proc/{pid}').st_uid != os.getuid():
                foreign.append(pid)
        except FileNotFoundError:
            pass
    return {'uuid': uuid, 'name': name, 'free_mib': int(free),
            'utilization': int(util), 'foreign_pids': foreign}


def command(python, runtime, output, job, cpu, phase):
    cmd = ['taskset', '-c', cpu, str(python), '-u', str(runtime/'stage_train.py'), 'train',
           '--manifest', job['manifest'], '--output', str(output), '--stage', job['stage'],
           '--target', job['target'], '--model', job['model'], '--seed', str(job['seed']),
           '--feature-ablation', job['feature_ablation'], '--epochs', '30',
           '--device', 'cuda:0', '--cpu-threads', '2']
    if job['target'] == 'effective_resistance':
        cmd += ['--batch-size', '1024']
    if (output/'last.pt').exists():
        cmd += ['--resume']
    if phase == 'test':
        if '--resume' not in cmd:
            raise ValueError('Test scoring requires a completed training checkpoint')
        cmd += ['--finalize']
    return cmd


def completed(output, phase):
    result_path, status_path = output/'result.json', output/'status.json'
    if not result_path.exists() or not status_path.exists():
        return False
    result = json.loads(result_path.read_text())
    status = json.loads(status_path.read_text())
    return (status.get('epochs_complete') == 30 and
            result.get('status') in ('development_complete', 'complete') and
            (phase == 'train' or result.get('test_evaluated') is True))


def validate_inputs(full_manifest, subset_receipt, manifest25, manifest50):
    full = json.loads(full_manifest.read_text())
    receipt = json.loads(subset_receipt.read_text())
    if receipt.get('status') != 'PASS' or receipt['parent_manifest_sha256'] != sha(full_manifest):
        raise ValueError('Subset receipt does not bind the full manifest')
    if not full.get('formal_training_ready') or full.get('pilot_subset'):
        raise ValueError('Expected reviewed formal corpus')
    fixed = {(r['design_id'], r['split']) for r in full['records'] if r['split'] != 'train'}
    families = []
    for key, path in (('25', manifest25), ('50', manifest50)):
        manifest = json.loads(path.read_text())
        if sha(path) != receipt['outputs'][key]['sha256']:
            raise ValueError('Derived manifest changed')
        if {(r['design_id'], r['split']) for r in manifest['records'] if r['split'] != 'train'} != fixed:
            raise ValueError('Validation/test differs from the formal corpus')
        families.append(set(manifest['ablation']['selected_train_families']))
    if not families[0] < families[1]:
        raise ValueError('Scale subsets are not nested')


def validate_scale_inputs(full_manifest, subset_receipt, scale_manifests):
    full = json.loads(full_manifest.read_text())
    receipt = json.loads(subset_receipt.read_text())
    if receipt.get('status') != 'PASS' or receipt['parent_manifest_sha256'] != sha(full_manifest):
        raise ValueError('Subset receipt does not bind the full manifest')
    if not full.get('formal_training_ready') or full.get('pilot_subset'):
        raise ValueError('Expected reviewed formal corpus')
    fixed = {(r['design_id'], r['split']) for r in full['records'] if r['split'] != 'train'}
    family_sets = []
    for percent, path in scale_manifests:
        key = str(percent)
        manifest = json.loads(path.read_text())
        if sha(path) != receipt['outputs'][key]['sha256']:
            raise ValueError(f'Derived manifest changed: {path}')
        if {(r['design_id'], r['split']) for r in manifest['records'] if r['split'] != 'train'} != fixed:
            raise ValueError('Validation/test differs from the formal corpus')
        if manifest['ablation']['fraction'] != percent / 100:
            raise ValueError(f'Unexpected fraction in {path}')
        family_sets.append(set(manifest['ablation']['selected_train_families']))
    for smaller, larger in zip(family_sets, family_sets[1:]):
        if not smaller < larger:
            raise ValueError('Scale subsets are not strictly nested')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest25', type=Path)
    parser.add_argument('--manifest50', type=Path)
    parser.add_argument('--scale', nargs=2, action='append', metavar=('PERCENT', 'MANIFEST'))
    parser.add_argument('--subset-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--gpu', type=int, nargs='+', default=[0, 1, 2, 3])
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if len(set(args.gpu)) != len(args.gpu):
        raise ValueError('Duplicate GPU index')
    if args.scale:
        scale_manifests = sorted((int(percent), Path(path)) for percent, path in args.scale)
        if len({percent for percent, _ in scale_manifests}) != len(scale_manifests):
            raise ValueError('Duplicate scale percentage')
        validate_scale_inputs(args.manifest, args.subset_receipt, scale_manifests)
        planned = scale_jobs(scale_manifests)
        campaign_kind = 'scale_only'
    else:
        if args.manifest25 is None or args.manifest50 is None:
            parser.error('--manifest25 and --manifest50 are required unless --scale is used')
        validate_inputs(args.manifest, args.subset_receipt, args.manifest25, args.manifest50)
        planned = jobs(args.manifest, args.manifest25, args.manifest50)
        campaign_kind = 'scale_and_geometry'
    if args.output.exists() and not args.resume:
        raise FileExistsError('Use --resume to preserve an existing campaign')
    if args.resume and not args.output.exists():
        raise FileNotFoundError(args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output/'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runtime = args.output/'runtime'
    plan_path = args.output/'plan.json'
    if not plan_path.exists():
        runtime.mkdir()
        source = Path(__file__).resolve().parents[1]/'gnn-node'
        for path in source.glob('*.py'):
            shutil.copy2(path, runtime/path.name)
        plan = {
            'schema': 'r2g_downstream_ablation_plan_v2', 'created_at': now(),
            'full_manifest': str(args.manifest.resolve()),
            'full_manifest_sha256': sha(args.manifest), 'python': str(args.python.resolve()),
            'epochs': 30, 'seeds': list(SEEDS), 'jobs': planned,
            'runtime_sha256': {p.name: sha(p) for p in runtime.glob('*.py')},
            'campaign_kind': campaign_kind,
            'test_policy': f'all {len(planned)} development runs finish before any held-out test scoring',
            'scale_policy': 'nested train-family subsets; validation and test unchanged',
            'geometry_policy': 'remove physical coordinates, HPWL geometry, die geometry and their validity flags',
            'min_free_mib': 8192,
        }
        save(plan_path, plan)
    plan = json.loads(plan_path.read_text())
    if plan['jobs'] != planned or plan['full_manifest_sha256'] != sha(args.manifest):
        raise ValueError('Frozen plan/input mismatch')
    if {p.name: sha(p) for p in runtime.glob('*.py')} != plan['runtime_sha256']:
        raise ValueError('Frozen runtime changed')

    events_path = args.output/'runs.json'
    events = json.loads(events_path.read_text()) if events_path.exists() else []
    active, failures = {}, []
    cpu_sets = {gpu: f'{144+2*i},{145+2*i}' for i, gpu in enumerate(args.gpu)}
    started = time.monotonic()

    def write_status(phase, pending):
        save(args.output/'status.json', {
            'phase': phase, 'updated_at': now(), 'total': len(planned),
            'trained': sum(completed(args.output/job['name'], 'train') for job in planned),
            'test_scored': sum(completed(args.output/job['name'], 'test') for job in planned),
            'active': [{key: row[key] for key in ('name', 'gpu', 'pid', 'cpu')}
                       for row in active.values()],
            'pending': [job['name'] for job in pending], 'failures': failures,
            'invocation_seconds': time.monotonic()-started,
        })

    try:
        for phase in ('train', 'test'):
            if phase == 'test' and not all(completed(args.output/job['name'], 'train') for job in planned):
                raise ValueError('Development matrix is incomplete')
            pending = [job for job in planned if not completed(args.output/job['name'], phase)]
            while pending or active:
                for gpu, row in list(active.items()):
                    code = row['process'].poll()
                    if code is None:
                        continue
                    row['log'].close()
                    event = {key: row[key] for key in
                             ('name', 'gpu', 'cpu', 'command', 'gpu_at_launch', 'phase')}
                    event.update(exit_code=code, at=now(), seconds=time.monotonic()-row['started'])
                    events.append(event)
                    save(events_path, events)
                    if code or not completed(args.output/row['name'], phase):
                        failures.append(event)
                    del active[gpu]
                if failures:
                    write_status('failed_requires_review', pending)
                    return
                for gpu in args.gpu:
                    if not pending or gpu in active:
                        continue
                    try:
                        info = gpu_info(gpu)
                    except (subprocess.SubprocessError, ValueError):
                        continue
                    if info['free_mib'] < plan['min_free_mib'] or info['foreign_pids']:
                        continue
                    job = pending.pop(0)
                    output = args.output/job['name']
                    cmd = command(args.python, runtime, output, job, cpu_sets[gpu], phase)
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=info['uuid'], OMP_NUM_THREADS='2',
                               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='2')
                    env.pop('PYTHONHOME', None)
                    env.pop('PYTHONPATH', None)
                    log = (args.output/f'{job["name"]}.{phase}.log').open('a')
                    process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True)
                    active[gpu] = {'name': job['name'], 'gpu': gpu, 'pid': process.pid,
                                   'cpu': cpu_sets[gpu], 'process': process, 'log': log,
                                   'command': cmd, 'gpu_at_launch': info, 'phase': phase,
                                   'started': time.monotonic()}
                    print(f'{phase} {job["name"]}: GPU {gpu}, CPU {cpu_sets[gpu]}', flush=True)
                write_status(phase if active else 'waiting_for_gpu', pending)
                if pending or active:
                    time.sleep(15)
        write_status('complete', [])
    except BaseException:
        write_status('supervisor_failed', [])
        raise
    finally:
        for row in active.values():
            if row['process'].poll() is None:
                os.killpg(row['pid'], signal.SIGTERM)
                try:
                    row['process'].wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(row['pid'], signal.SIGKILL)
                    row['process'].wait()
            row['log'].close()
        lock.close()


if __name__ == '__main__':
    main()
