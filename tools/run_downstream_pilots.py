"""Run a bounded, serial GNN/MLP software check without scoring the test split."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def gpu_info(index):
    text = subprocess.check_output(['nvidia-smi', '-i', str(index),
        '--query-gpu=uuid,name,memory.used,utilization.gpu', '--format=csv,noheader,nounits'], text=True)
    uuid, name, memory, utilization = [s.strip() for s in text.strip().split(',')]
    return dict(uuid=uuid, name=name, memory_mib=int(memory), utilization=int(utilization))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--gpu', type=int)
    p.add_argument('--cpu', action='store_true')
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new pilot output directory')
    if args.cpu == (args.gpu is not None):
        raise ValueError('Choose exactly one of --cpu or --gpu INDEX')
    gpu = None if args.cpu else gpu_info(args.gpu)
    if gpu and (gpu['memory_mib'] > 64 or gpu['utilization'] > 5):
        raise RuntimeError('Requested GPU is in use; no training started')
    manifest = json.loads(args.manifest.read_text())
    if not manifest['pilot_subset']:
        raise ValueError('Expected bounded pilot manifest')
    args.output.mkdir(parents=True)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpu['uuid'] if gpu else '', OMP_NUM_THREADS='2',
               MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1')
    script = Path(__file__).resolve().parents[1] / 'gnn-node/stage_train.py'
    rows = []
    for stage in ('cts', 'route'):
        for target in ('wirelength', 'congestion'):
            for model in ('gine', 'mlp'):
                name = f'{stage}_{target}_{model}'
                cmd = [sys.executable, '-u', str(script), 'train', '--manifest', str(args.manifest),
                    '--output', str(args.output/name), '--stage', stage, '--target', target,
                    '--model', model, '--device', 'cpu' if args.cpu else 'cuda:0', '--epochs', '2', '--pilot',
                    '--cpu-threads', '2', '--seed', '42']
                save(args.output/'status.json', dict(status='running', active=name, completed=len(rows),
                    planned=8, test_evaluated=False, updated_at=datetime.now(timezone.utc).isoformat()))
                begin = time.monotonic()
                peak = 0
                with (args.output/(name+'.log')).open('w') as log:
                    process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        while process.poll() is None:
                            if gpu:
                                peak = max(peak, gpu_info(args.gpu)['memory_mib'])
                            time.sleep(.5)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait()
                row = dict(name=name, seconds=time.monotonic()-begin, gpu_memory_peak_mib=peak,
                           exit_code=process.returncode, command=cmd)
                rows.append(row)
                save(args.output/'runs.json', rows)
                if process.returncode:
                    save(args.output/'status.json', dict(status='failed', active=name, completed=len(rows)-1,
                                                       log=str(args.output/(name+'.log'))))
                    raise RuntimeError('Pilot failed: '+name)
                result = json.loads((args.output/name/'result.json').read_text())
                if result['test_evaluated'] or result['status'] != 'pilot_complete_not_formal':
                    raise ValueError('Pilot evaluated a test split')
                print(json.dumps(row), flush=True)
    save(args.output/'status.json', dict(status='complete', completed=8, planned=8,
        test_evaluated=False, formal_training=False, gpu=gpu,
        total_seconds=sum(r['seconds'] for r in rows), peak_memory_mib=max(r['gpu_memory_peak_mib'] for r in rows)))


if __name__ == '__main__':
    main()
