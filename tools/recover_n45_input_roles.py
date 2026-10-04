"""Recover a frozen Nangate45 task whose HDL dependency was misclassified.

This is deliberately narrower than the ordinary runner: it changes only source
file roles (.vh/.svh become dependencies), archives the failed preparation, and
runs one baseline attempt with the original physical protocol.
"""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time


DEPENDENCY_SUFFIXES = {'.vh', '.svh', '.mem', '.hex', '.dat'}


def corrected_task(task):
    fixed = copy.deepcopy(task)
    candidate = fixed['candidate']
    compile_files = []
    dependencies = set(candidate.get('header_files', []))
    dependencies.update(candidate.get('readmem_files', []))
    for name in candidate['rtl_files']:
        if Path(name).suffix.lower() in DEPENDENCY_SUFFIXES:
            dependencies.add(name)
        else:
            compile_files.append(name)
    if compile_files == candidate['rtl_files']:
        raise ValueError('task has no misclassified dependency')
    if not compile_files:
        raise ValueError('correction would leave no compilation unit')
    candidate['rtl_files'] = compile_files
    candidate['header_files'] = sorted(dependencies)
    return fixed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--cpu-set', required=True)
    args = parser.parse_args()

    if socket.gethostname() != 'memlab-gpu':
        raise RuntimeError('this recovery is bound to memlab203')
    campaign = args.campaign.resolve()
    sys.path.insert(0, str(campaign))
    import runner

    runner.verify_snapshot()
    cpus = {int(value) for value in args.cpu_set.split(',')}
    if len(cpus) != 4:
        raise ValueError('exactly four CPUs are required')
    os.sched_setaffinity(0, cpus)

    plan = runner.read(campaign / 'plan.json')
    original = next((row for row in plan['tasks'] if row['task_id'] == args.task_id), None)
    if original is None or original.get('eligibility') != 'queued':
        raise ValueError('task is not an eligible frozen baseline input')
    task = corrected_task(original)
    work = campaign / 'jobs' / args.task_id
    review = work / 'needs_review.json'
    if not review.is_file() or runner.read(review).get('reason') != 'input_preparation':
        raise ValueError('task is not an input-preparation recovery')
    if (work / 'complete.json').exists() or (work / 'started.json').exists():
        raise ValueError('task already has physical execution state')

    recovery = campaign / 'input_role_recovery' / args.task_id
    recovery.mkdir(parents=True, exist_ok=False)
    with (work / 'job.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        shutil.copytree(work, recovery / 'original_job', copy_function=shutil.copy2)
        runner.save(recovery / 'role_correction.json', {
            'task_id': args.task_id,
            'started_at': runner.now(),
            'reason': 'dependency_suffix_misclassified_as_compilation_unit',
            'original_rtl_files': original['candidate']['rtl_files'],
            'corrected_rtl_files': task['candidate']['rtl_files'],
            'corrected_header_files': task['candidate']['header_files'],
            'source_files': original['source_files'],
            'source_closure_sha256': original['source_closure_sha256'],
            'physical_protocol_unchanged': True,
        })
        for name in ('project', 'prepare.log', 'needs_review.json'):
            path = work / name
            if path.exists():
                shutil.move(str(path), str(recovery / f'failed_{name}'))
        env = runner.environment()
        project = runner.prepare(task, work, env, (lock.fileno(),))
        started = runner.now()
        runner.save(work / 'started.json', {
            'started_at': started, 'cpu_set': args.cpu_set,
            'worker': 'input_role_recovery',
        })
        command = [sys.executable, str(runner.PROBE), 'execute', '--project', str(project),
                   '--cores', '4', '--cpu-set', args.cpu_set, '--timeout-seconds', '7200']
        begin = time.monotonic()
        rc, timed_out = runner.call(command, work / 'execute.log', env,
                                    plan['trial_timeout_seconds'], (lock.fileno(),))
        result_path = project / 'repair_family_probe_result.json'
        result = runner.read(result_path) if result_path.exists() else {}
        status = runner.classify(result, rc, timed_out)
        record = {
            'task_id': args.task_id,
            'started_at': started,
            'completed_at': runner.now(),
            'elapsed_seconds': round(time.monotonic() - begin, 3),
            'returncode': rc,
            'trial_timeout': timed_out,
            'cpu_set': args.cpu_set,
            'status': status,
            'physical_result': result,
            'input_role_recovery': str(recovery / 'role_correction.json'),
            'note': ('Single baseline run after correcting a frozen file-role metadata error; '
                     'source bytes and physical protocol are unchanged'),
        }
        runner.save(work / 'complete.json', record)
        runner.save(recovery / 'result.json', record)
        print(json.dumps({'task_id': args.task_id, 'status': status,
                          'elapsed_seconds': record['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    main()
