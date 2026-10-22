#!/usr/bin/env python3
"""Table 2 T1 resume controller (2026-10-16): continue a paused batch, never restart or re-run.

Operator-side only. The frozen package (code, registration, delta, ledger budget) is not modified; each
remaining episode is executed by the unchanged frozen worker (`t2a_batch.py episode`). Only plan rows
that have NO episode directory are dispatched, in the original plan order and pair waves. Ended episodes,
the pause record, partial results and ledger history are preserved. Problems from the reviewed incident
(the UNKNOWN hold and its episode) are acknowledged by name; any other technical fault pauses again.

  register --root R --authorization A   bind the resume to the paused package and the user authorization
  run      --root R                     dispatch the remaining episodes, then write complete results
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def episode_name(row):
    return row['task'].replace('/', '__') + '__' + row['arm'] + '__r' + str(row['repeat'])


def remaining_rows(root, plan):
    """Plan rows never started (no episode directory), original order. Started rows are never touched."""
    return [row for row in plan if not (Path(root) / 'episodes' / episode_name(row)).exists()]


def incident_only(problems, hold_id, episode):
    """Every acknowledged problem must belong to the authorized incident(s), nothing else.
    ``hold_id`` / ``episode`` may each be one value or a list (incident hold(s), episode plus collateral)."""
    holds = [hold_id] if isinstance(hold_id, str) else list(hold_id)
    episodes = [episode] if isinstance(episode, str) else list(episode)
    return [p for p in problems if not any(h in p for h in holds)
            and not any(p.startswith(e + ':') for e in episodes)]


def ledger_refusal_problems(root, plan):
    """Ended episodes stopped by a ledger refusal (no model turn granted) are infrastructure-blocked even when
    their status reads NOT_ACCEPTED. The frozen T1 monitor did not check this (incident 2, RET r2)."""
    out = []
    for row in plan:
        project = Path(root) / 'episodes' / episode_name(row)
        try:
            data = read(project / 'episode.json')
        except (OSError, ValueError):
            continue
        if 'ended' not in data:
            continue
        stopped = any(isinstance(t, dict) and t.get('status') == 'REQUEST_STOP'
                      and (t.get('usage') or t.get('reason') or {}).get('budget_exhausted')
                      for t in data.get('repair_turns') or [])
        ledger = project / 'reports/resource_ledger.jsonl'
        refused = ledger.exists() and any('"budget_refused"' in line for line in ledger.read_text().splitlines())
        if stopped or refused:
            out.append(episode_name(row) + ': ledger-refusal stop, infrastructure-blocked (status %s)'
                       % data.get('status'))
    return out


def refusals_within_incident_windows(ledger_lines, holds):
    """True when EVERY ledger refusal lies between an incident hold's UNKNOWN settlement and that hold's
    authorized occupancy record, i.e. it was caused by the reviewed UNKNOWN pause and nothing else."""
    events = [json.loads(line) for line in ledger_lines if line.strip()]
    windows = []
    for hold in holds:
        settle = next((e for e in events if e.get('type') == 'budget_settle' and e.get('hold_id') == hold
                       and 'UNKNOWN' in (e.get('actual') or {}).values()), None)
        occupy = next((e for e in events if e.get('type') == 'budget_unknown_occupancy' and e.get('hold_id') == hold), None)
        if settle is None or occupy is None:
            return False
        windows.append((settle['event_seq'], occupy['event_seq']))
    refusals = [e for e in events if e.get('type') == 'budget_refused']
    return all('UNKNOWN' in str(e.get('reason')) and any(lo < e['event_seq'] < hi for lo, hi in windows)
               for e in refusals)


def names(number):
    """Per-resume file names; resume 1 keeps its original names."""
    s = '' if number == 1 else '-%d' % number
    return {'registration': 'table2-resume-registration%s.json' % s, 'start': 'table2-resume-start%s.json' % s,
            'operator': 'operator/t2a_table2_resume%s.py' % ('' if number == 1 else '_%d' % number),
            'paused_before': 'batch-paused.json' if number == 1 else 'batch-paused-%d.json' % number,
            'paused_after': 'batch-paused-%d.json' % (number + 1), 'pause_archive': 'dispatch-pause-%d.json' % number,
            'results_archive': 'table2-results-paused-%d.json' % number,
            'matrix_archive': 'table2-matrix-paused-%d.csv' % number,
            'progress_archive': 'table2-resume-progress-before-%d.json' % number}


def ended_episodes(root):
    out = {}
    for d in sorted((Path(root) / 'episodes').glob('*')):
        data = read(d / 'episode.json')
        if 'ended' not in data:
            raise RuntimeError('Episode not ended; refusing to resume: ' + d.name)
        out[d.name] = digest(d / 'episode.json')
    return out


def dispatch(root, rows, slots, spawn, pause_requested, wave_planner, on_wave=None):
    """Pair waves of the remaining rows; stop at a wave boundary on a pause request. Returns waves run."""
    waves = wave_planner(rows, len(slots))
    for index, wave in enumerate(waves):
        if pause_requested():
            return index, len(waves), True
        for row in wave:
            if (Path(root) / 'episodes' / episode_name(row)).exists():
                raise RuntimeError('Refusing to re-run an existing episode: ' + episode_name(row))
        children = [spawn(slot, row) for slot, row in zip(slots, wave)]
        while any(c.poll() is None for c in children):
            if on_wave:
                on_wave()
            time.sleep(30)
        if any(c.returncode for c in children):
            raise RuntimeError('Worker aborted; no implicit retry')
    return len(waves), len(waves), False


def _operator(root):
    spec = importlib.util.spec_from_file_location('t2a_table2_operator_frozen', root / 'operator/t2a_table2_operator.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _package_bindings(root):
    grant = read(root / 'table2-operator-registration.json')
    if digest(root / 'operator/t2a_table2_operator.py') != grant['operator_sha256']:
        raise RuntimeError('Frozen operator changed')
    for name, key in (('registration.json', 'registration_sha256'), ('runtime-delta.json', 'runtime_delta_sha256'),
                      ('phase-budget.json', 'phase_budget_sha256')):
        if digest(root / name) != grant[key]:
            raise RuntimeError('Frozen package changed: ' + name)
    return grant


def _occupancy(root, hold_id, authorization):
    for line in (root / 'phase/resource_ledger.jsonl').read_text().splitlines():
        e = json.loads(line)
        if e.get('type') == 'budget_unknown_occupancy' and e.get('hold_id') == hold_id:
            if e.get('authorization_sha256') != digest(authorization) or e.get('actual_remains') != 'UNKNOWN':
                raise RuntimeError('Occupancy record does not match the authorization')
            return e
    raise RuntimeError('Authorized UNKNOWN occupancy is not recorded in the Table 2 ledger')


def _prior_acknowledged(root, number):
    acknowledged, incidents = [], []
    for k in range(1, number):
        grant = read(root / names(k)['registration'])
        if not (root / names(k)['start']).exists():
            raise RuntimeError('Resume %d was registered but never started' % k)
        acknowledged += grant['acknowledged_problems']
        incidents.append(grant['incident'])
    return acknowledged, incidents


def cmd_register(root, authorization, number=1):
    n = names(number)
    if (root / n['registration']).exists():
        raise RuntimeError('Existing resume registration; no overwrite')
    if not (root / n['paused_before']).exists() or (root / 'batch-complete.json').exists():
        raise RuntimeError('Package is not in a paused, incomplete state for resume %d' % number)
    if number > 1 and (root / names(number + 1)['registration']).exists():
        raise RuntimeError('A later resume already exists')
    _package_bindings(root)
    prior, incidents = _prior_acknowledged(root, number)
    auth = read(authorization)
    target = auth['targets'][0]
    if (auth.get('schema') != 'r2g-unknown-occupancy-authorization-v1' or auth.get('user_approved') is not True
            or len(auth['targets']) != 1 or Path(target['ledger_path']) != (root / 'phase/resource_ledger.jsonl')):
        raise RuntimeError('Authorization is not the Table 2 occupancy authorization')
    occupancy = _occupancy(root, target['hold_id'], authorization)
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    import t2a_batch as B
    import r2g_memory_eval as H
    reg = B.preflight(root, 'TEST')
    ended = ended_episodes(root)
    op = _operator(root)
    problems = sorted(set(op.integrity_problems(root, reg, B, H) + ledger_refusal_problems(root, reg['plan'])))
    incident_episodes = [Path(target['episode']).name] + [Path(e).name for e in target.get('collateral_episodes', [])]
    holds = [i['hold_id'] for i in incidents] + [target['hold_id']]
    episodes = [e for i in incidents for e in i.get('episodes', [i['episode']])] + incident_episodes
    stray = incident_only([p for p in problems if p not in prior], holds, episodes)
    # Ledger-refusal labels for episodes of EARLIER incidents are known infrastructure blocks, not new faults.
    stray = [p for p in stray if not any(p.startswith(e + ':') for e in episodes)]
    if any(p.startswith('ledger refusal:') for p in stray):
        if not refusals_within_incident_windows((root / 'phase/resource_ledger.jsonl').read_text().splitlines(), holds):
            raise RuntimeError('A ledger refusal lies outside every reviewed UNKNOWN window')
        stray = [p for p in stray if not p.startswith('ledger refusal:')]
    if stray:
        raise RuntimeError('Unreviewed technical problems remain: %s' % stray)
    rows = remaining_rows(root, reg['plan'])
    if len(rows) + len(ended) != len(reg['plan']):
        raise RuntimeError('Plan/episode accounting mismatch')
    operator = root / n['operator']
    if operator.exists():
        raise RuntimeError('Resume operator copy already exists')
    shutil.copy2(__file__, operator)
    summary_source = Path(__file__).with_name('t2a_table2_summary.py')
    summary_copy = root / 'operator/t2a_table2_summary.py'
    if summary_copy.exists() and digest(summary_copy) != digest(summary_source):
        raise RuntimeError('A different summary script is already bound')
    if not summary_copy.exists():
        shutil.copy2(summary_source, summary_copy)
    for source, target_name in (('dispatch-pause.json', n['pause_archive']), ('table2-results.json', n['results_archive']),
                                ('table2-matrix.csv', n['matrix_archive']),
                                ('table2-resume-progress.json', n['progress_archive'])):
        if (root / source).exists():
            if (root / target_name).exists():
                raise RuntimeError('Archive exists; no overwrite: ' + target_name)
            shutil.copy2(root / source, root / target_name)   # preserved verbatim
    save(root / n['registration'], {
        'schema': 't2a-table2-resume-v2', 'resume_number': number, 'registered_at': time.time(),
        'resume_operator': n['operator'], 'resume_operator_sha256': digest(operator),
        'summary_script_sha256': digest(summary_copy),
        'package_registration_sha256': digest(root / 'registration.json'),
        'authorization': str(Path(authorization).resolve()), 'authorization_sha256': digest(authorization),
        'occupancy_event': occupancy, 'acknowledged_problems': sorted(set(prior + problems)),
        'incident': {'hold_id': target['hold_id'], 'episode': incident_episodes[0], 'episodes': incident_episodes,
                     'rerun': False},
        'earlier_incidents': incidents, 'ended_episodes': ended, 'remaining': rows,
        'pause_record': n['pause_archive'], 'partial_results': n['results_archive'],
        'no_restart': True, 'no_rerun': True})
    if (root / 'dispatch-pause.json').exists():
        (root / 'dispatch-pause.json').unlink()       # the archived copy above keeps the original bytes
    print(json.dumps({'resume': number, 'remaining': len(rows), 'ended': len(ended),
                      'acknowledged_new': [p for p in problems if p not in prior]}))


def cmd_run(root, number=1):
    n = names(number)
    lock = (root / ('table2-resume%s.lock' % ('' if number == 1 else '-%d' % number))).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    grant = read(root / n['registration'])
    if Path(__file__).resolve() != (root / n['operator']).resolve() or digest(Path(__file__)) != grant['resume_operator_sha256']:
        raise RuntimeError('Run the registered resume operator copy only')
    if (root / n['start']).exists() or (root / 'batch-complete.json').exists():
        raise RuntimeError('No implicit resume restart')
    _package_bindings(root)
    if digest(root / 'registration.json') != grant['package_registration_sha256']:
        raise RuntimeError('Package registration changed')
    for name, sha in grant['ended_episodes'].items():
        if digest(root / 'episodes' / name / 'episode.json') != sha:
            raise RuntimeError('Ended episode record changed: ' + name)
    _occupancy(root, grant['incident']['hold_id'], grant['authorization'])
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    import t2a_batch as B
    import r2g_memory_eval as H
    reg = B.preflight(root, 'TEST')
    rows = remaining_rows(root, reg['plan'])
    if rows != grant['remaining']:
        raise RuntimeError('Remaining plan differs from registration')
    slots = reg['scheduler']['cpu_slots']
    B.validate_cpu_slots(slots, os.sched_getaffinity(0), 'TEST')
    worker = root / 'code/memory/scripts/t2a_batch.py'
    if digest(worker) != reg['files'][str(worker)]:
        raise RuntimeError('Worker code changed')
    op = _operator(root)
    acknowledged = set(grant['acknowledged_problems'])
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'CUDA_VISIBLE_DEVICES': ''}

    def stage(name, **extra):
        save(root / 'table2-resume-progress.json', {'stage': name, 'resume': number, 'time': time.time(), **extra})

    def spawn(slot, row):
        handle = (root / (episode_name(row) + '.worker.log')).open('a')
        return subprocess.Popen(['taskset', '-c', ','.join(map(str, slot)), sys.executable, str(worker), 'episode',
                                 '--root', str(root), '--task', row['task'], '--arm', row['arm'],
                                 '--repeat', str(row['repeat']), '--split', 'TEST'],
                                env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)

    def watch():
        found = op.integrity_problems(root, reg, B, H) + ledger_refusal_problems(root, reg['plan'])
        new = sorted({p for p in found if p not in acknowledged})
        if new and not (root / 'dispatch-pause.json').exists():
            save(root / 'dispatch-pause.json', {'time': time.time(), 'reason': 'technical integrity', 'problems': new})
            stage('PAUSE_REQUESTED_AT_NEXT_WAVE', problems=new)
        with (root / 'machine-load.jsonl').open('a') as load:
            load.write(json.dumps({'ts': time.time(), 'loadavg': os.getloadavg(), 'slots': slots,
                                   'resume': number}) + '\n')

    save(root / n['start'], {'started': time.time(), 'pid': os.getpid(), 'remaining': len(rows)})
    stage('TABLE2_RESUMED', remaining=len(rows))
    done, planned, paused = dispatch(root, rows, slots, spawn, lambda: (root / 'dispatch-pause.json').exists(),
                                     B.plan_waves, on_wave=watch)
    watch()
    if paused:
        save(root / n['paused_after'], {'paused_at': time.time(), 'resume': number, 'resume_waves_completed': done,
                                         'resume_waves_planned': planned,
                                         'request': read(root / 'dispatch-pause.json')})
    else:
        save(root / 'batch-complete.json', {'ended': time.time(), 'episodes': len(reg['plan']),
                                             'resumed_after_pause': True, 'resumes': number,
                                             'unreviewed_problems_at_end': read(root / 'dispatch-pause.json')
                                             if (root / 'dispatch-pause.json').exists() else None})
    result = op.collect(root, B)
    incidents = grant.get('earlier_incidents', []) + [grant['incident']]
    blocked = sorted({e for i in incidents for e in i.get('episodes', [i['episode']])})
    others = [r for r in result['records'] if episode_name(r) not in blocked]
    result.update(paused=paused, resumed_after_pause=True, resumes=number,
                  acknowledged_incidents=incidents, acknowledged_problems=grant['acknowledged_problems'],
                  data_audit_passed_excluding_acknowledged_incidents=(
                      not result['missing'] and all(r['labels_valid'] and r['terminal_classification_consistent']
                                                    and r['accounting']['passed'] and r['tokens']['measurement_complete']
                                                    for r in others)))
    save(root / 'table2-results.json', result)
    import importlib.util as iu
    summary_path = root / 'operator/t2a_table2_summary.py'
    if digest(summary_path) != grant['summary_script_sha256']:
        raise RuntimeError('Summary script changed')
    spec = iu.spec_from_file_location('t2a_table2_summary', summary_path)
    summary = iu.module_from_spec(spec)
    spec.loader.exec_module(summary)
    summary.write(root)
    stage('PAUSED_FOR_INTEGRITY_REVIEW' if paused else 'COMPLETE',
          execution_complete=result['execution_complete'], data_audit_passed=result['data_audit_passed'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('register', 'run'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--authorization', type=Path)
    p.add_argument('--number', type=int, default=1)
    a = p.parse_args()
    if a.command == 'register':
        cmd_register(a.root.resolve(), a.authorization.resolve(), a.number)
    else:
        cmd_run(a.root.resolve(), a.number)
