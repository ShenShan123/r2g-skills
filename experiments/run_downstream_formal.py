"""Fixed-budget four-GPU downstream training, followed by held-out scoring."""
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
import sys
import time

from run_downstream_pilots import save


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jobs(targets=('wirelength', 'congestion'), stages=('cts', 'route'),
         models=('gine', 'mlp')):
    return [dict(name=f'{stage}_{target}_{model}_seed{seed}', stage=stage, target=target,
                 model=model, seed=seed)
            for seed in (42, 43, 44) for stage in stages
            for target in targets for model in models]


def gpu_info(index):
    text = subprocess.check_output(['nvidia-smi', '-i', str(index),
        '--query-gpu=uuid,name,memory.free,utilization.gpu', '--format=csv,noheader,nounits'], text=True)
    uuid, name, free, util = (s.strip() for s in text.strip().split(','))
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
    return dict(uuid=uuid, name=name, free_mib=int(free), utilization=int(util),
                foreign_pids=foreign)


def command(python, runtime, manifest, output, job, cpu, phase):
    cmd = ['taskset', '-c', cpu, str(python), '-u', str(runtime/'stage_train.py'), 'train',
           '--manifest', str(manifest), '--output', str(output), '--stage', job['stage'],
           '--target', job['target'], '--model', job['model'], '--seed', str(job['seed']),
           '--epochs', '30', '--device', 'cuda:0', '--cpu-threads', '2']
    if job['target'] in ('coupling_cap', 'effective_resistance'):
        cmd += ['--batch-size', '1024']
    if (output/'last.pt').exists():
        cmd += ['--resume']
    if phase == 'test':
        if '--resume' not in cmd:
            raise ValueError('Test scoring requires a completed training checkpoint')
        cmd += ['--finalize']
    return cmd


def completed(output, phase):
    p = output/'result.json'
    s = output/'status.json'
    if not p.exists() or not s.exists():
        return False
    result, status = json.loads(p.read_text()), json.loads(s.read_text())
    return (status.get('epochs_complete') == 30 and
            result.get('status') in ('development_complete', 'complete') and
            (phase == 'train' or result.get('test_evaluated') is True))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--python', type=Path, required=True)
    p.add_argument('--gpu', type=int, nargs='+', default=[0, 1, 2, 3])
    p.add_argument('--target', dest='targets', action='append',
                   choices=('wirelength', 'ground_cap', 'congestion', 'setup_slack',
                            'hold_slack', 'coupling_cap', 'effective_resistance'))
    p.add_argument('--stage', dest='stages', action='append',
                   choices=('floorplan', 'placement', 'cts', 'route'))
    p.add_argument('--model', dest='models', action='append', choices=('gine', 'mlp'))
    p.add_argument('--parent-manifest', type=Path)
    p.add_argument('--derivation-receipt', type=Path)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    assert len(set(args.gpu)) == len(args.gpu)
    selected_targets = tuple(args.targets or ('wirelength', 'congestion'))
    selected_stages = tuple(args.stages or ('cts', 'route'))
    selected_models = tuple(args.models or ('gine', 'mlp'))
    planned_jobs = jobs(selected_targets, selected_stages, selected_models)
    manifest = json.loads(args.manifest.read_text())
    assert manifest.get('formal_training_ready') and not manifest.get('pilot_subset')
    receipt_manifest = args.parent_manifest or args.manifest
    receipts = [json.loads((receipt_manifest.parent/n).read_text()) for n in
                ('gpu0_blackwell_validation.json', 'a100_new_environment_validation.json')]
    assert all(r['status']=='PASS' and r['manifest_sha256']==sha(receipt_manifest) for r in receipts)
    assert receipts[0]['versions'] == receipts[1]['versions'], 'Use one validated software stack'
    if args.parent_manifest:
        assert args.derivation_receipt, 'Derived manifests require a derivation receipt'
        derivation = json.loads(args.derivation_receipt.read_text())
        assert derivation['status'] == 'PASS'
        assert derivation['parent_manifest_sha256'] == sha(args.parent_manifest)
        assert derivation['derived_manifest_sha256'] == sha(args.manifest)
    if args.output.exists() and not args.resume:
        raise FileExistsError('Use --resume to preserve previous training')
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
        lineage = None
        if args.parent_manifest:
            lineage = dict(parent_manifest=str(args.parent_manifest.resolve()),
                           parent_manifest_sha256=sha(args.parent_manifest),
                           derivation_receipt=str(args.derivation_receipt.resolve()),
                           derivation_receipt_sha256=sha(args.derivation_receipt))
        save(plan_path, dict(manifest=str(args.manifest.resolve()), manifest_sha256=sha(args.manifest),
             python=str(args.python.resolve()), epochs=30, seeds=[42, 43, 44],
             targets=list(selected_targets), stages=list(selected_stages),
             models=list(selected_models), jobs=planned_jobs,
             lineage=lineage,
             versions=receipts[0]['versions'], created_at=now(),
             runtime_sha256={f.name: sha(f) for f in runtime.glob('*.py')},
             rule='Validation selects checkpoint; test scored only after all planned training jobs finish',
             shared_gpu_allowed=True, min_free_mib=2048))
    plan = json.loads(plan_path.read_text())
    assert sha(args.manifest) == plan['manifest_sha256']
    assert str(args.python.resolve()) == plan['python']
    if plan.get('lineage'):
        assert args.parent_manifest and args.derivation_receipt
        assert sha(args.parent_manifest) == plan['lineage']['parent_manifest_sha256']
        assert sha(args.derivation_receipt) == plan['lineage']['derivation_receipt_sha256']
    assert {f.name: sha(f) for f in runtime.glob('*.py')} == plan['runtime_sha256']
    assert plan['jobs'] == planned_jobs
    active, events, failures = {}, [], []
    events_path = args.output/'runs.json'
    if events_path.exists():
        events = json.loads(events_path.read_text())
    cpu_sets = {g: f'{144+2*i},{145+2*i}' for i, g in enumerate(args.gpu)}
    started = time.monotonic()

    def status(phase, pending):
        save(args.output/'status.json', dict(phase=phase, updated_at=now(), total=len(planned_jobs),
             trained=sum(completed(args.output/j['name'], 'train') for j in planned_jobs),
             test_scored=sum(completed(args.output/j['name'], 'test') for j in planned_jobs),
             active=[{k: a[k] for k in ('name', 'gpu', 'pid', 'cpu')} for a in active.values()],
             pending=[j['name'] for j in pending], failures=failures,
             invocation_seconds=time.monotonic()-started))

    try:
        for phase in ('train', 'test'):
            if phase == 'test':
                assert all(completed(args.output/j['name'], 'train') for j in planned_jobs)
            pending = [j for j in planned_jobs if not completed(args.output/j['name'], phase)]
            while pending or active:
                for index, a in list(active.items()):
                    code = a['process'].poll()
                    if code is None:
                        continue
                    a['log'].close()
                    row = {k: a[k] for k in ('name', 'gpu', 'cpu', 'command', 'gpu_at_launch', 'phase')}
                    row.update(exit_code=code, at=now(), seconds=time.monotonic()-a['started'])
                    events.append(row)
                    save(events_path, events)
                    if code or not completed(args.output/a['name'], phase):
                        failures.append(row)
                    del active[index]
                for index in args.gpu:
                    if not pending or index in active:
                        continue
                    gpu = gpu_info(index)
                    if gpu['free_mib'] < 2048 or gpu['foreign_pids']:
                        continue
                    j = pending.pop(0)
                    output = args.output/j['name']
                    cmd = command(args.python, runtime, args.manifest, output, j, cpu_sets[index], phase)
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpu['uuid'], OMP_NUM_THREADS='2',
                               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='2')
                    env.pop('PYTHONHOME', None)
                    env.pop('PYTHONPATH', None)
                    log = (args.output/(j['name']+'.'+phase+'.log')).open('a')
                    try:
                        process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                   start_new_session=True)
                    except BaseException:
                        log.close()
                        raise
                    active[index] = dict(name=j['name'], gpu=index, pid=process.pid,
                         cpu=cpu_sets[index], process=process, log=log, command=cmd,
                         gpu_at_launch=gpu, phase=phase, started=time.monotonic())
                    print(f'{phase} {j["name"]}: GPU {index}, CPU {cpu_sets[index]}', flush=True)
                status(phase if active else 'waiting_for_memory', pending)
                if pending or active:
                    time.sleep(15)
            if failures:
                status('failed_requires_review', [])
                return
        status('complete', [])
    except BaseException:
        status('supervisor_failed', [])
        raise
    finally:
        for a in active.values():
            if a['process'].poll() is None:
                os.killpg(a['pid'], signal.SIGTERM)
                try:
                    a['process'].wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(a['pid'], signal.SIGKILL)
                    a['process'].wait()
            a['log'].close()
        lock.close()


if __name__ == '__main__':
    main()
