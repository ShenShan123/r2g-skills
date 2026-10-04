"""Time independent full-epoch jobs on idle or explicitly shared GPUs."""
import argparse
import fcntl
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

from run_downstream_pilots import gpu_info, save


def free_memory_mib(index):
    return int(subprocess.check_output(['nvidia-smi', '-i', str(index),
        '--query-gpu=memory.free', '--format=csv,noheader,nounits'], text=True).strip())


def available_gpus(indices, active, allow_shared=False, min_free_mib=2048):
    result = []
    for index in indices:
        if index in active:
            continue
        gpu = gpu_info(index)
        # The frozen CUDA environment supports A100, not the host's Blackwell.
        if 'A100' not in gpu['name']:
            continue
        if allow_shared:
            gpu['free_memory_mib'] = free_memory_mib(index)
            eligible = gpu['free_memory_mib'] >= min_free_mib
        else:
            eligible = gpu['memory_mib'] <= 64 and gpu['utilization'] <= 5
        if eligible:
            result.append((index, gpu))
    return result


def training_command(manifest, output, stage, target, cpu_set):
    script = Path(__file__).resolve().parents[1]/'gnn-node/stage_train.py'
    return ['taskset', '-c', cpu_set, sys.executable, '-u', str(script), 'train',
            '--manifest', str(manifest), '--output', str(output), '--stage', stage,
            '--target', target, '--model', 'gine', '--device', 'cuda:0', '--epochs', '2',
            '--cpu-threads', '2']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--gpu', type=int, nargs='+', required=True)
    p.add_argument('--stages', nargs='+', choices=('cts', 'route'), default=['cts'])
    p.add_argument('--cpu-base', type=int, default=144)
    p.add_argument('--allow-shared-gpu', action='store_true')
    p.add_argument('--min-free-mib', type=int, default=2048)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest.get('pilot_subset') or not manifest.get('formal_training_ready'):
        raise ValueError('Full reviewed cohort required')
    if len(set(args.gpu)) != len(args.gpu) or len(set(args.stages)) != len(args.stages):
        raise ValueError('Duplicate GPU or stage')
    if args.min_free_mib <= 0:
        raise ValueError('Positive free-memory reserve required')
    cpu_sets = {g: f'{args.cpu_base+2*i},{args.cpu_base+2*i+1}' for i, g in enumerate(args.gpu)}
    if not available_gpus(args.gpu, {}, args.allow_shared_gpu, args.min_free_mib):
        raise RuntimeError('Requested GPUs are occupied or unsupported')
    if args.output.exists() and not args.resume:
        raise FileExistsError('Preserve previous measurement')
    if args.resume and not args.output.is_dir():
        raise FileNotFoundError('Measurement to resume does not exist')
    args.output.mkdir(parents=True, exist_ok=args.resume)
    lock = (args.output/'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    pending = [(stage, target) for stage in args.stages for target in ('wirelength', 'congestion')]
    planned = len(pending)
    runs, active = [], {}
    previous_elapsed = 0
    if args.resume:
        prior = json.loads((args.output/'status.json').read_text())
        if prior['phase'] not in ('paused_no_idle_gpu', 'paused_no_available_gpu') or prior['active']:
            raise ValueError('Resume requires an inactive paused queue')
        runs = json.loads((args.output/'runs.json').read_text()) if (args.output/'runs.json').exists() else []
        names = {f'{s}_{t}_gine' for s, t in pending}
        done = {r['name'] for r in runs if r['exit_code'] == 0}
        if len(done) != len(runs) or not done <= names or prior['planned'] != planned:
            raise ValueError('Resume expects successful completed jobs and the same task set')
        if set(prior['pending']) != names - done:
            raise ValueError('Saved pending tasks differ')
        for row in runs:
            command = row['command']
            if Path(command[command.index('--manifest')+1]).resolve() != args.manifest.resolve():
                raise ValueError('Manifest path changed')
            result = json.loads((args.output/row['name']/'result.json').read_text())
            run_status = json.loads((args.output/row['name']/'status.json').read_text())
            if result['test_evaluated'] or run_status['epochs_complete'] != 2:
                raise ValueError('Completed measurement is inconsistent')
        pending = [(s, t) for s, t in pending if f'{s}_{t}_gine' not in done]
        if any((args.output/f'{s}_{t}_gine').exists() for s, t in pending):
            raise ValueError('Pending task has existing artifacts; inspect before retry')
        previous_elapsed = prior.get('elapsed_seconds', 0)
        save(args.output/f'status.before_resume.{time.time_ns()}.json', prior)
    started = time.monotonic()
    idle_since = None

    def status(phase):
        save(args.output/'status.json', dict(phase=phase, planned=planned,
             completed=sum(r['exit_code']==0 for r in runs), failed=sum(r['exit_code']!=0 for r in runs),
             active=[{k: job[k] for k in ('name', 'gpu_index', 'gpu_uuid', 'pid', 'cpu_set')} for job in active.values()],
             pending=[f'{s}_{t}_gine' for s,t in pending], requested_gpus=args.gpu,
             full_training_seeds=True, test_evaluated=False, formal_accuracy_result=False,
             allow_shared_gpu=args.allow_shared_gpu, min_free_mib=args.min_free_mib,
             elapsed_seconds=previous_elapsed+time.monotonic()-started))

    try:
        while pending or active:
            for index, job in list(active.items()):
                code = job['process'].poll()
                if code is None:
                    continue
                job['log'].close()
                row = {k: job[k] for k in ('name', 'gpu_index', 'gpu_uuid', 'cpu_set', 'command')}
                row.update(exit_code=code, wall_seconds=time.monotonic()-job['started'],
                           shared_gpu_allowed=args.allow_shared_gpu, gpu_at_launch=job['gpu_at_launch'])
                if code == 0:
                    output = json.loads((args.output/job['name']/'result.json').read_text())
                    if output['test_evaluated']:
                        raise ValueError('Unexpected test evaluation')
                    history = json.loads((args.output/job['name']/'history.json').read_text())
                    row.update(timing=output['timing'], epochs=[{k: h[k] for k in
                        ('epoch', 'train_seconds', 'epoch_seconds')} for h in history])
                runs.append(row)
                save(args.output/'runs.json', runs)
                del active[index]
                print(json.dumps(row), flush=True)
            for index, gpu in available_gpus(args.gpu, active, args.allow_shared_gpu, args.min_free_mib) if pending else []:
                if not pending:
                    break
                stage, target = pending.pop(0)
                name = f'{stage}_{target}_gine'
                command = training_command(args.manifest, args.output/name, stage, target, cpu_sets[index])
                env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpu['uuid'], OMP_NUM_THREADS='2',
                           MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1')
                log = (args.output/(name+'.log')).open('w')
                try:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True)
                except BaseException:
                    log.close()
                    raise
                active[index] = dict(name=name, gpu_index=index, gpu_uuid=gpu['uuid'], cpu_set=cpu_sets[index],
                                     pid=process.pid, process=process, log=log, command=command,
                                     gpu_at_launch=gpu,
                                     started=time.monotonic())
                print(f'{name}: GPU {index}, CPU {cpu_sets[index]}, full epochs; no test evaluation', flush=True)
            if pending and not active:
                idle_since = idle_since or time.monotonic()
                if time.monotonic()-idle_since > 120:
                    status('paused_no_available_gpu' if args.allow_shared_gpu else 'paused_no_idle_gpu')
                    return
            else:
                idle_since = None
            status('running' if active else 'waiting_for_available_gpu')
            if pending or active:
                time.sleep(10)
        status('failed' if any(r['exit_code'] for r in runs) else 'complete')
    except BaseException:
        status('supervisor_failed')
        raise
    finally:
        for job in active.values():
            if job['process'].poll() is None:
                os.killpg(job['pid'], signal.SIGTERM)
                try:
                    job['process'].wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(job['pid'], signal.SIGKILL)
                    job['process'].wait()
            job['log'].close()
        lock.close()


if __name__ == '__main__':
    main()
