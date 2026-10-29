"""Frozen, separately labelled recovery of interrupted native-protocol SWE cells."""
import argparse
import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT.parent / 't4-swe-agent-adaptation-20261026/envs/runtime/bin/python'


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def episode(cell):
    return ROOT / 'attempts' / cell['task'] / 'hosts/swe-agent' / cell['arm'] / 'episodes' / cell['task']


def verify_parent(plan):
    for name, digest in plan['preserved_parent_files'].items():
        if sha(name) != digest:
            raise ValueError('parent evidence changed: ' + name)


def freeze():
    target = ROOT / 'inputs/registration.json'
    if target.exists():
        raise ValueError('never replace a frozen recovery')
    plan = load(ROOT / 'inputs/plan.json')
    verify_parent(plan)
    if (ROOT / 'inputs/native.yaml').read_bytes() != (ROOT / 'inputs/tehm.yaml').read_bytes():
        raise ValueError('native/TEHM config parity violated')
    if list((ROOT / 'attempts').glob('*/hosts/*/*/episodes/*/started.json')):
        raise ValueError('cannot freeze after launch')
    external = dict(plan['external_files'])
    for q in (ROOT / 'qualification').glob('*/qualification.json'):
        value = load(q)
        if value['status'] != 'PASS':
            raise ValueError('unqualified recovery')
        for label, wanted in [('baseline', 'FAIL'), ('reference', 'PASS')]:
            path = Path(value[label + '_grade'])
            g = load(path)
            output = path.parent / 'test-output.txt'
            if g['status'] != wanted or sha(output) != g['test_output_sha256']:
                raise ValueError('qualification evidence mismatch')
            external[str(path)] = sha(path)
            external[str(output)] = sha(output)
        repo = Path(value['repo'])
        if subprocess.check_output(['git', '-C', str(repo), 'diff', '--binary', 'HEAD']):
            raise ValueError('qualification base has uncommitted edits')
    smoke = ROOT / 'reports/native-real-tools-smoke-r3/receipt.json'
    if load(smoke).get('status') != 'PASS' or load(smoke).get('actual_provider_requests') != 0:
        raise ValueError('real native tools smoke did not pass offline')
    paths = [ROOT / 'prepare.py', ROOT / 'recovery.py', ROOT / 'PROTOCOL.md', ROOT / 'reports/offline-tests.xml', smoke]
    for folder in ['adapters', 'code/memory', 'recovery_tests', 'inputs', 'qualification', 'sources/SWE-bench-v4.1.0']:
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and not any(s in p.parts for s in ['__pycache__', '.git', '.pytest_cache']))
    from xml.etree import ElementTree as ET
    cases = list(ET.parse(ROOT / 'reports/offline-tests.xml').getroot().iter('testcase'))
    if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
        raise ValueError('offline tests not passing')
    plan.update(launch_authorized=True, runtime='swe-native-recovery-20261029',
                files={str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))},
                external_files=external, tests_passed=sum(c.find('skipped') is None for c in cases),
                frozen_at=time.time(), stream_transport='SSE reassembled before native consumption; one HTTP request, zero retries')
    record(target, plan)
    check()
    return {'status': 'FROZEN_READY', 'retry_cells': len(plan['retry_cells']), 'sha256': sha(target)}


def check():
    reg = load(ROOT / 'inputs/registration.json')
    if not reg['launch_authorized']:
        raise ValueError('not authorized')
    verify_parent(reg)
    for name, digest in reg['files'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('frozen file changed: ' + name)
    for name, digest in reg['external_files'].items():
        if sha(name) != digest:
            raise ValueError('external frozen file changed: ' + name)
    if (ROOT / 'reports/TECHNICAL-PAUSE.json').exists():
        raise ValueError('global technical/payment/identity pause')
    return reg


def audit(cell):
    base = episode(cell)
    result = load(base / 'result.json')
    p = base / 'provider/requests.jsonl'
    events = [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
    starts = [e for e in events if e['event'] == 'request_started']
    ends = [e for e in events if e['event'] == 'request_ended']
    ids = [e['request_local_id'] for e in starts]
    if len(ids) != len(set(ids)) or set(ids) != {e['request_local_id'] for e in ends}:
        raise ValueError('unsettled or duplicate request accounting')
    u = result['usage']
    known = sum(e['usage']['total_tokens'] for e in ends if isinstance(e.get('usage'), dict))
    unknown = sum(e.get('usage') == 'UNKNOWN' for e in ends)
    if (u['requests'], u['known_tokens'], u['unknown_requests']) != (len(starts), known, unknown):
        raise ValueError('terminal accounting mismatch')
    if unknown and (u['actual_total_tokens'] is not None or result['status'] in {'ACCEPTED', 'NOT_ACCEPTED'}):
        raise ValueError('UNKNOWN converted to measured outcome')
    if result['status'] in {'ACCEPTED', 'NOT_ACCEPTED'}:
        grade = load(base / 'evaluation/grade.json')
        if grade.get('status') != ('PASS' if result['status'] == 'ACCEPTED' else 'FAIL'):
            raise ValueError('terminal grade mismatch')
        if sha(base / 'submitted.patch') != grade['patch_sha256']:
            raise ValueError('submitted patch mismatch')
    return {'task': cell['task'], 'arm': cell['arm'], 'status': 'PASS',
            'requests': len(starts), 'known_tokens': known, 'unknown_requests': unknown}


def summary():
    plan = load(ROOT / 'inputs/plan.json')
    rows = []
    for cell in plan['retry_cells']:
        path = episode(cell) / 'result.json'
        result = load(path) if path.exists() else {'status': 'RUNNING' if episode(cell).exists() else 'NOT_STARTED'}
        rows.append({**cell, **result})
    completed = [r for r in rows if r['status'] not in {'RUNNING', 'NOT_STARTED'}]
    record(ROOT / 'reports/results.json', {
        'runtime': 'swe-native-recovery-20261029', 'registered_recovery_cells': len(rows),
        'ended': len(completed), 'episodes': rows,
        'status_counts': dict(collections.Counter(r['status'] for r in rows)),
        'known_tokens': sum(r.get('usage', {}).get('known_tokens', 0) for r in completed),
        'requests': sum(r.get('usage', {}).get('requests', 0) for r in completed),
        'unknown_requests': sum(r.get('usage', {}).get('unknown_requests', 0) for r in completed),
        'parent_costs_not_overwritten': True,
        'collector_passes_not_rerun': plan['collector_passes_not_rerun']})
    parent = load(Path(plan['parent']) / 'reports/results.json')['episodes']
    retries = {(r['task'], r['arm']): r for r in completed}
    collectors = {(r['task'], r['arm']): r for r in plan['collector_passes_not_rerun']}
    combined = []
    for old in parent:
        key = (old['task'], old['arm'])
        if key in retries:
            row = {**retries[key], 'source': 'explicit recovery attempt; parent costs retained'}
        elif key in collectors:
            row = {**old, 'status': 'ACCEPTED', 'strict_accepted': True,
                   'source': 'verified offline collector correction; raw old result unchanged',
                   'collector_receipt': collectors[key]['receipt']}
        else:
            row = {**old, 'source': 'original episode'}
        row['parent_status'] = old['status']
        row['original_usage'] = old.get('usage')
        combined.append(row)
    record(ROOT / 'reports/combined-view.json', {'episodes': combined,
        'scope': 'labelled recovery view, not a clean first-attempt score; never best-of',
        'parent': plan['parent'], 'original_and_recovery_costs_must_both_be_reported': True})
    return {'registered': len(rows), 'ended': len(completed), 'statuses': dict(collections.Counter(r['status'] for r in rows))}


def lane(arm):
    with (ROOT / 'reports' / (arm + '.lock')).open('a') as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reg = check()
        for cell in [c for c in reg['retry_cells'] if c['arm'] == arm]:
            if (episode(cell) / 'result.json').exists():
                audit(cell)
                continue
            check()
            if episode(cell).parent.parent.exists():
                raise ValueError('partially started recovery: do not auto-retry')
            if shutil.disk_usage(ROOT).free < 50 * 1024 ** 3:
                raise ValueError('50 GiB disk floor; no new episode admitted')
            record(ROOT / 'reports' / (arm + '-state.json'), {'status': 'RUNNING', **cell, 'pid': os.getpid(), 'updated': time.time()})
            log = ROOT / 'reports/worker-logs' / (arm + '--' + cell['task'] + '.log')
            log.parent.mkdir(exist_ok=True)
            with log.open('x') as out:
                rc = subprocess.run([str(PYTHON), str(ROOT / 'adapters/run_swe.py'),
                                     '--task', cell['task'], '--arm', arm], stdout=out, stderr=subprocess.STDOUT).returncode
            if rc != 0 or not (episode(cell) / 'result.json').exists():
                raise ValueError('worker exited without auditable terminal: ' + str(rc))
            record(ROOT / 'reports/audits' / (arm + '--' + cell['task'] + '.json'), audit(cell))
            verify_parent(reg)
            summary()
        record(ROOT / 'reports' / (arm + '-state.json'), {'status': 'COMPLETE', 'updated': time.time(), 'pid': os.getpid()})


def run():
    with (ROOT / 'reports/dispatcher.lock').open('a') as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check()
        if (ROOT / 'reports/launch.json').exists():
            raise ValueError('already launched; no implicit batch restart')
        record(ROOT / 'reports/launch.json', {'pid': os.getpid(), 'registration_sha256': sha(ROOT / 'inputs/registration.json'), 'started': time.time()})
        children = {}
        for arm in ['native', 'tehm']:
            log = (ROOT / 'reports' / (arm + '-lane.log')).open('x')
            children[arm] = subprocess.Popen([str(PYTHON), str(ROOT / 'recovery.py'), 'lane', '--arm', arm], stdout=log, stderr=subprocess.STDOUT)
            log.close()
        record(ROOT / 'reports/dispatch-state.json', {'status': 'RUNNING', 'pid': os.getpid(), 'lanes': {a: p.pid for a, p in children.items()}})
        rc = {a: p.wait() for a, p in children.items()}
        record(ROOT / 'reports/dispatch-state.json', {'status': 'COMPLETE' if not any(rc.values()) else 'BLOCKED', 'lane_returncodes': rc, 'summary': summary(), 'updated': time.time()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'check', 'run', 'lane', 'summary'])
    parser.add_argument('--arm', choices=['native', 'tehm'])
    args = parser.parse_args()
    if args.command == 'lane':
        try:
            lane(args.arm)
        except BaseException as exc:
            record(ROOT / 'reports' / (args.arm + '-state.json'), {'status': 'BLOCKED', 'reason': str(exc), 'updated': time.time()})
            raise
    else:
        result = globals()[args.command]()
        if args.command == 'check':
            result = {'status': 'PASS', 'retry_cells': len(result['retry_cells'])}
        print(json.dumps(result, ensure_ascii=False))
