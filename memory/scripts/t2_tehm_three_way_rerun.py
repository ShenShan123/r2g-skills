"""Freeze/run eight opt-in TEHM consumer reruns; never rewrite prior results.

Reuses the current agent-selected host, held-out memories and request-only
transport. No constructors, retries or prior UNKNOWN settlements are repeated.
"""
from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / 'memory/scripts'), str(REPO / 'memory')]
import t2_nb_followup as F
import t2_agent_request_resume as R
from t2_agent_memories import TM0_THREE_WAY_POLICY

VERSION = 'tehm-three-way-eight-rerun-v1'
IDS = ('C00876', 'C02786', 'C00237', 'C03579', 'C02966', 'C01295', 'C02881', 'C00568')
DELTA = ('t2_agent_memories.py', 't2_agent_runtime.py', 't2_tehm_execution_policy.py',
         't2_tehm_three_way_rerun.py')
sha, load, save = F.sha, F.load, F.save


def passing_tests(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if len(cases) < 239 or any(c.find(k) is not None for c in cases for k in ('failure', 'error', 'skipped')):
        raise ValueError('Passing three-way consumer/transport regression required')
    return len(cases)


def rebase_template(source, destination):
    import t2a_envelope as E
    shutil.copytree(source, destination)
    path = destination / 'constraints/config.mk'
    path.write_text(path.read_text().replace(str(source) + '/', str(destination) + '/'))
    m = load(destination / 'manifest.json')
    m.update(source_project=str(destination), initial_config=E.effective_config(path.read_text()),
             initial_config_sha256=sha(path))
    save(destination / 'manifest.json', m)
    old = load(source / 'manifest.json')
    norm = lambda cfg, p: {k: v.replace(str(p), '$PROJECT') for k, v in cfg.items()}
    if norm(m['initial_config'], destination) != norm(old['initial_config'], source):
        raise ValueError('Initial parameters changed while copying task')
    if (m['immutable'] != old['immutable'] or m['area'] != old['area']
            or sha(source / 'constraints/constraint.sdc') != sha(destination / 'constraints/constraint.sdc')):
        raise ValueError('Task constraints changed')


def prepare(base, root, tests):
    started, cpu = time.time(), time.process_time()
    R.install_online()
    import t2a_batch as B
    base, root, tests = Path(base).resolve(), Path(root).resolve(), Path(tests).resolve()
    passed = passing_tests(tests)
    if root.exists(): raise FileExistsError('Never overwrite an existing experiment')
    prior_results = load(base / 'table2-results.json')
    for hid in IDS:
        cells = [e for e in prior_results['episodes'] if e['design'] == hid and e['arm'] == 'TM0']
        if len(cells) != 1 or not Path(cells[0]['episode']).is_file():
            raise ValueError('Missing/ambiguous original TEHM cell: ' + hid)
    q = load(base / 'queue-registration.json')
    # Partial old infrastructure cells are retained, not falsely required to
    # become measured successes before a separately authorised labelled rerun.
    if load(base / 'dispatch-state.json')['status'] != 'COMPLETED':
        raise ValueError('Original controller is not complete')
    root.mkdir(parents=True); (root / 'folds').mkdir(); (root / 'logs').mkdir(); (root / 'tmp').mkdir()
    (root / 'online-phase').mkdir(); (root / 'online-phase/resource_ledger.jsonl').touch(exist_ok=False)
    save(root / 'online-budget.json', {'budget_id': root.name, 'caps': {}, 'reserve': {},
                                      'halt_on_unknown_usage': True})
    save(root / 'authorization.json', {
        'user_text': 'ok，那么现在用最新的记忆机制先重新跑一次table2的实验，我看下结果',
        'interpretation': '8 new TEHM-only episodes; reuse R2G and other controls; preserve every old cell',
        'provider': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash',
        'online_requests_per_episode': 3, 'maximum_new_online_requests': 24,
        'cumulative_cost_cap': None, 'implicit_retries': 0, 'UNKNOWN': 'pause',
        'new_memory_construction': False, 'history_writeback': False, 'learning_curve': 'unchanged'})
    shutil.copy2(tests, root / 'offline-tests.xml')
    reference = {str(base / 'table2-results.json'): sha(base / 'table2-results.json'),
                 str(base / 'registration.json'): sha(base / 'registration.json')}
    common = {str(root / n): sha(root / n) for n in ('online-budget.json', 'authorization.json', 'offline-tests.xml')}
    plans, deltas = [], {}
    private_base = base / 'folds/C02786/code'  # all eight use the current JSON-compatible host
    for hid in IDS:
        old = (base / 'folds' / hid).resolve(); prior = load(old / 'registration.json')
        # Validate the superseded package's immutable files without pretending
        # its older transport policy equals the new worker's policy.
        F.verify_files(prior['files'])
        fold = root / 'folds' / hid; fold.mkdir()
        shutil.copytree(private_base, fold / 'code', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        changed = {}
        for name in DELTA:
            dst = fold / 'code/memory/scripts' / name
            before = sha(dst) if dst.exists() else None
            shutil.copy2(REPO / 'memory/scripts' / name, dst)
            changed['memory/scripts/' + name] = {'base': before, 'new': sha(dst)}
        for name in ('shared', 'source', 'ann', 'tehm-import', 'agent-store', 'store', 'controls',
                     'exclusions.json', 'verification.json', 'overrides.json', 'evidence-index.json',
                     'tehm-v2-policy.json', 'online-input-authorization.json', 'episode-budget.json'):
            src = old / name
            (fold / name).symlink_to(src, target_is_directory=src.is_dir())
        template = Path(prior['tasks'][0]['template'])
        (fold / 'tasks').mkdir()
        destination = fold / 'tasks' / template.name
        rebase_template(template, destination)
        task = prior['tasks'][0]['task']
        reg = copy.deepcopy(prior)
        frozen = {**prior['files'], **common, **F.frozen(fold / 'code'), **F.frozen(fold / 'tasks')}
        reg.update(schema=VERSION, status='FROZEN_READY', paper_data=False,
                   arms=['TM0'], plan=[{'task': task, 'arm': 'TM0', 'repeat': 1}],
                   tasks=[{'task': task, 'template': str(destination), 'handoff_id': hid}],
                   files=frozen, online_policy=R.policy(),
                   online_entrypoint=str(fold / 'code/memory/scripts/t2_agent_request_resume.py'),
                   tehm_consumer_policy=TM0_THREE_WAY_POLICY,
                   phase_binding={'budget_file': str(root / 'online-budget.json'),
                       'budget_sha256': sha(root / 'online-budget.json'),
                       'ledger_path': str(root / 'online-phase/resource_ledger.jsonl')},
                   consumer_delta=changed,
                   version_boundary='observed-design TEHM-only labelled rerun; not independent confirmation')
        save(fold / 'registration.json', reg)
        B.preflight(fold, 'TEST')
        from t2_tehm_execution_policy import load_evidence
        units, _, _, refs = load_evidence(fold, {'R2G_TEHM_V2_POLICY': str(fold / 'tehm-v2-policy.json')})
        save(fold / 'three-way-evidence-preflight.json', {'verified_units': len(units), 'binding': refs,
                                                       'model_requests': 0, 'eda_flows': 0})
        reference[str(old / 'registration.json')] = sha(old / 'registration.json')
        reference[str(fold / 'three-way-evidence-preflight.json')] = sha(fold / 'three-way-evidence-preflight.json')
        plans.append({'root': str(fold), 'task': task, 'arm': 'TM0', 'design': hid})
        deltas[hid] = changed
        print(json.dumps({'prepared': hid, 'evidence_units': len(units)}), flush=True)
    first = Path(plans[0]['root'])
    save(root / 'queue-registration.json', {'schema': VERSION, 'base': str(base),
         'plan_size': 8, 'arms': ['TM0'], 'concurrency': len(q['cpu_slots']),
         'tehm_consumer_policy': TM0_THREE_WAY_POLICY})
    files = {**common, **reference, **{str(Path(p['root']) / 'registration.json'):
             sha(Path(p['root']) / 'registration.json') for p in plans}}
    files[str(root / 'queue-registration.json')] = sha(root / 'queue-registration.json')
    save(root / 'registration.json', {'schema': VERSION, 'status': 'FROZEN_READY', 'plan': plans,
        'base': str(base), 'files': files, 'deltas': deltas, 'slots': q['cpu_slots'],
        'tests_passed': passed, 'tehm_consumer_policy': TM0_THREE_WAY_POLICY,
        'request_policy': R.policy(), 'frozen_controller': str(first / 'code/memory/scripts' / Path(__file__).name),
        'construction_reused_not_repeated': True, 'known_old_incomplete_cells_retained': True,
        'preparation_cost': {'wall_s': time.time()-started, 'cpu_s': time.process_time()-cpu,
                             'model_requests': 0, 'tokens': 0, 'eda_flows': 0},
        'created': time.time()})
    save(root / 'dispatch-state.json', {'status': 'FROZEN_READY', 'done': [], 'running': [], 'pending': 8})
    collect(root)


def collect(root):
    root = Path(root); reg = load(root / 'registration.json')
    original = load(Path(reg['base']) / 'table2-results.json')
    previous = [e for e in original['episodes'] if e['arm'] == 'TM0']
    new = []
    for job in reg['plan']:
        p = Path(job['root']) / 'episodes' / (job['task'].replace('/', '__') + '__TM0__r1')
        e = load(p / 'episode.json') if (p / 'episode.json').exists() else {}
        new.append({'design': job['design'], 'task': job['task'], 'arm': 'TM0',
            'version': TM0_THREE_WAY_POLICY, 'status': e.get('status', 'NOT_STARTED'),
            'episode': str(p / 'episode.json'), 'accounting': e.get('accounting'),
            'resources': e.get('resources'), 'token_usage': e.get('token_usage'),
            'wall_s': e.get('total_worker_wall_s'),
            'cost': F.ledger_cost(p / 'reports/resource_ledger.jsonl') if (p / 'episode.json').exists() else None})
    data = {**original, 'updated': time.time(), 'episodes': [e for e in original['episodes'] if e['arm'] != 'TM0'] + new,
            'previous_TEHM_episodes': previous, 'rerun_policy': TM0_THREE_WAY_POLICY,
            'prior_results_unchanged': True, 'historical_control_comparison': True,
            'complete': all(e['status'] in ('ACCEPTED', 'NOT_ACCEPTED') for e in new)
                        and all(e['status'] in ('ACCEPTED', 'NOT_ACCEPTED') for e in original['episodes'] if e['arm'] != 'TM0')}
    # Do not inherit stale previous-arm aggregate numbers after replacing cells.
    data.pop('arms', None)
    save(root / 'table2-results.json', data)
    summary = {'registered': 8, 'ended': sum(e['status'] != 'NOT_STARTED' and e['status'] != 'RUNNING' for e in new),
        'accepted': sum(e['status'] == 'ACCEPTED' for e in new),
        'measured': sum(e['status'] in ('ACCEPTED', 'NOT_ACCEPTED') for e in new),
        'cost_totals_so_far': {k: sum((e['cost'] or {}).get(k, 0) for e in new)
                             for k in ('requests', 'known_tokens', 'unknown_requests', 'flows', 'tool_calls')},
        'TEHM_episodes': new, 'controls_reused': True}
    save(root / 'TEHM-summary.json', summary)
    with (root / 'table2-matrix.csv').open('w') as f:
        w = csv.writer(f); w.writerow(['design', 'R2G', 'TEHM_previous', 'TEHM_three_way', 'tokens', 'requests', 'flows'])
        for e in new:
            nb = next(x for x in original['episodes'] if x['design'] == e['design'] and x['arm'] == 'NB')
            old = next(x for x in previous if x['design'] == e['design'])
            c = e['cost'] or {}; w.writerow([e['design'], nb['status'], old['status'], e['status'],
                                             c.get('known_tokens'), c.get('requests'), c.get('flows')])


def receipt_errors(project):
    ev = F.rows(Path(project) / 'reports/resource_ledger.jsonl')
    errors = []
    for e in (v for v in ev if v.get('type') == 'memory_agent_context'):
        if e.get('consumer_policy') != TM0_THREE_WAY_POLICY: errors.append('wrong TEHM consumer')
    local = [e for e in ev if e.get('type') == 'memory_procedure_consumption']
    for e in local:
        if not e.get('selection') or not e['selection'].get('selection_sha256'):
            errors.append('missing local selection binding')
        if e.get('flow_launched') and not e.get('journal_ref'): errors.append('missing local action journal')
    req = [e for e in ev if e.get('type') == 'llm_request']
    used = [e for e in ev if e.get('type') == 'memory_consumption']
    if len(req) != len(used): errors.append('online consumer receipt count mismatch')
    from t2_tehm_agent_memory import verify_request
    for e in used:
        if verify_request(project, e)['request_files'] != e.get('request_files'):
            errors.append('serialized online consumption mismatch')
    return errors


def run(root):
    root = Path(root).resolve(); reg = load(root / 'registration.json')
    if str(Path(__file__).resolve()) != reg['frozen_controller']: raise ValueError('Use frozen private controller')
    lock = (root / 'supervisor.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (root / 'launch.json').exists(): raise FileExistsError('No implicit restart')
    F.verify_files(reg['files']); R.install_online()
    import t2a_batch as B
    for job in reg['plan']: B.preflight(Path(job['root']), 'TEST')
    cpus = [c for slot in reg['slots'] for c in slot]
    if len(cpus) != len(set(cpus)) or not set(cpus) <= os.sched_getaffinity(0):
        raise ValueError('CPU slots unavailable or overlapping')
    if shutil.disk_usage(root).free < 80 * 1024**3: raise RuntimeError('Disk safety guard: need 80 GiB')
    os.environ.update(TMPDIR=str(root / 'tmp'), PYTHONDONTWRITEBYTECODE='1')
    with (root / 'launch.json').open('x') as f:
        json.dump({'started': time.time(), 'pid': os.getpid(), 'registration_sha256': sha(root / 'registration.json')}, f)
    F.SLOTS = reg['slots']; F.collect = collect
    def popen(cmd, **kwargs):
        cmd = list(cmd)
        worker = Path(cmd[4]); fold = worker.parents[3]
        B.preflight(fold, 'TEST')
        if shutil.disk_usage(root).free < 40 * 1024**3: raise RuntimeError('Disk safety pause')
        cmd[4] = str(worker.with_name('t2_agent_request_resume.py'))
        return subprocess.Popen(cmd, **kwargs)
    # Reuse the tested no-retry, drain-on-technical-fault dispatcher. A failed
    # measured repair is a result, not a reason to stop other designs.
    try:
        result = F.dispatch(root, reg['plan'], popen=popen)
        for job in result['done']:
            p = Path(job['root']) / 'episodes' / (job['task'].replace('/', '__') + '__TM0__r1')
            errors = receipt_errors(p)
            if errors: result.update(status='PAUSED', reason='; '.join(errors))
        save(root / 'dispatch-state.json', result); collect(root)
    except Exception as exc:
        save(root / 'dispatch-state.json', {'status': 'PAUSED_TECHNICAL', 'reason': str(exc), 'updated': time.time()})
        raise


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('command', choices=('prepare', 'run', 'collect'))
    ap.add_argument('--root', type=Path, required=True); ap.add_argument('--base', type=Path); ap.add_argument('--tests', type=Path)
    a = ap.parse_args()
    if a.command == 'prepare': prepare(a.base, a.root, a.tests)
    elif a.command == 'run': run(a.root)
    else: collect(a.root)
