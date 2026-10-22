#!/usr/bin/env python3
"""Table 2 operator (2026-10-16): explicit runtime delta, independent uncapped ledger, integrity fail-fast.

Commands (once-only; existing roots are never overwritten, nothing is retried):
  delta    write the per-file runtime delta from the DEV-validated base to the current worktree runtime
  prepare  freeze the 56-episode TEST package (fallback-only policy) with that delta and a fresh ledger
  run      retire the paused old formal supervisor, dispatch, pause at a wave boundary on a TECHNICAL
           integrity fault only, then write the complete results (all episodes, all costs)

Repair rate, TEHM cost or absence of benefit never pause anything: they are results.
"""
import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

REPO = Path('/data1/zhangdy/tehm-integration')
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
DEV = EVAL / 't2a-mechanism-dev-r4-20261015/final-seven-arm-c10'
POLICY = EVAL / 't2a-mechanism-dev-r4-20261015/no-priority-policy.json'
POLICY_SHA = '5aa52cdfade458974aecbb32012ef89e61bf6934ebeaf5bc2c22b64b315ff4b7'
INVENTORY = EVAL / 't2a-formal-f2-20261013/inventory.json'
OLD_SUPERVISOR = {'pid': 1195535, 'argv_marker': b't2a-corrective-cycle-r3-20261015/operator/t2a_corrective_cycle.py'}
KNOWN_RESULT_ERRORS = ('Shared repair restoration is unverified',)   # designed fail-closed result, not a fault
DISCLOSURES = [
    'Validation boundary: real seven-arm DEV on the base runtime (final-seven-arm-c10) plus a test- and '
    'replay-verified delta; the new runtime itself has NOT completed a real seven-arm DEV.',
    'Knob permissions aligned to the published task contract for all seven arms (failure-patterns '
    '"T2a contract/executor knob mismatch"); NB proposals refused before now execute.',
    'TEHM-M0-v2 candidate rules rule_34ed200ab47aeb3e and rule_78d25ae8eff3680f (route slew/cap margins) '
    'are newly executable under the shared knob policy.',
    'Table 2 ledger is independent and has no cumulative request/token/money cap; UNKNOWN usage pauses it.',
    'Policy: fallback-only (cost_priority=false), chosen after FULL failed on DEV (result-dependent, disclosed).',
    'TEST tasks were exposed during F1/F2 correction; this is a corrective replay, not a blind confirmation.']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def _remove_scratch(scratch):
    """The copied sealed store is read-only; make only this scratch copy writable, then remove it."""
    for dirpath, dirnames, filenames in os.walk(scratch):
        os.chmod(dirpath, 0o700)
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                os.chmod(path, 0o600)
    shutil.rmtree(scratch)


def worktree_runtime():
    """The exact runtime map B.prepare copies, read from a scratch TEST preparation (not re-implemented)."""
    import tempfile
    sys.path.insert(0, str(REPO / 'memory/scripts'))
    import t2a_batch as B
    scratch = Path(tempfile.mkdtemp(prefix='t2a-delta-', dir='/data1/zhangdy/.cache/tmp'))
    try:
        B.prepare(scratch / 'pkg', INVENTORY, DEV / 'controls', DEV / 'dev-validation.json', DEV / 'store',
                  split='TEST', mechanism_policy=POLICY)
        return read(scratch / 'pkg/registration.json')['runtime_hashes']
    finally:
        if str(scratch).startswith('/data1/zhangdy/.cache/tmp/t2a-delta-'):
            _remove_scratch(scratch)


def cmd_delta(out, junit, replay):
    if Path(out).exists():
        raise RuntimeError('Existing delta; no overwrite')
    reg = read(DEV / 'registration.json')
    dev = read(DEV / 'dev-validation.json')
    base = dev['runtime_hashes']
    if base != reg['runtime_hashes'] or dev.get('real_seven_arm_dev_validated') is not True:
        raise RuntimeError('Base runtime is not the validated seven-arm DEV runtime')
    now = worktree_runtime()
    files = {rel: {'base': base.get(rel), 'new': now.get(rel)}
             for rel in sorted(set(base) | set(now)) if base.get(rel) != now.get(rel)}
    import xml.etree.ElementTree as ET
    suites = ET.parse(junit).getroot().findall('.//testsuite')
    tests = sum(int(s.get('tests', '0')) for s in suites)
    bad = sum(int(s.get('failures', '0')) + int(s.get('errors', '0')) for s in suites)
    skipped = sum(int(s.get('skipped', '0')) for s in suites)
    receipt = {'schema': 't2a-runtime-delta-v1', 'created': time.time(),
               'validation_boundary': 'REAL_SEVEN_ARM_DEV_ON_BASE_PLUS_TEST_AND_REPLAY_VERIFIED_DELTA',
               'base_root': str(DEV), 'base_registration_sha256': digest(DEV / 'registration.json'),
               'base_dev_validation_sha256': digest(DEV / 'dev-validation.json'),
               'base_completion_audit_sha256': digest(DEV / 'completion-audit.json'),
               'base_runtime_hashes': base, 'files': files,
               'evidence': {'junit': str(junit), 'junit_sha256': digest(junit), 'passed': tests - bad - skipped,
                            'failed': bad, 'skipped': skipped, 'replay': replay},
               'disclosures': DISCLOSURES}
    save(Path(out), receipt)
    print(json.dumps({'delta_files': sorted(files), 'passed': tests - bad - skipped, 'failed': bad}))


def cmd_prepare(root, delta):
    if root.exists():
        raise RuntimeError('Existing package; no overwrite')
    if digest(POLICY) != POLICY_SHA:
        raise RuntimeError('Frozen Table 2 policy changed')
    sys.path.insert(0, str(REPO / 'memory/scripts'))
    import t2a_batch as B
    B.prepare(root, INVENTORY, DEV / 'controls', DEV / 'dev-validation.json', DEV / 'store', split='TEST',
              mechanism_policy=POLICY, delta_path=Path(delta))
    reg = B.preflight(root, 'TEST')
    (root / 'phase').mkdir()                       # independent ledger: a fresh, real directory
    operator = root / 'operator'
    operator.mkdir()
    shutil.copy2(__file__, operator / 't2a_table2_operator.py')
    save(root / 'table2-operator-registration.json', {
        'schema': 't2a-table2-operator-v1', 'registered_at': time.time(),
        'registration_sha256': digest(root / 'registration.json'),
        'operator_sha256': digest(operator / 't2a_table2_operator.py'),
        'runtime_delta_sha256': digest(root / 'runtime-delta.json'),
        'phase_budget_sha256': digest(root / 'phase-budget.json'), 'phase_budget': read(root / 'phase-budget.json'),
        'dev_receipt_sha256': digest(DEV / 'dev-validation.json'),
        'dev_audit_sha256': digest(DEV / 'completion-audit.json'),
        'policy_sha256': POLICY_SHA, 'episodes': len(reg['plan']), 'max_repair_generations_per_episode': 3,
        'dev_validation_boundary': reg['dev_validation_boundary'], 'disclosures': DISCLOSURES,
        'old_supervisor': OLD_SUPERVISOR['pid'], 'no_retry': True, 'paper_data': True,
        'fail_fast': 'technical integrity only: accounting, identity/labels, permissions, UNKNOWN usage, '
                     'runtime exceptions; never repair rate or cost'})
    print(json.dumps({'root': str(root), 'status': reg['status'], 'episodes': len(reg['plan']),
                      'boundary': reg['dev_validation_boundary']}))


def check_binding(root):
    grant = read(root / 'table2-operator-registration.json')
    if digest(Path(__file__)) != grant['operator_sha256'] or Path(__file__).resolve() != (root / 'operator/t2a_table2_operator.py').resolve():
        raise RuntimeError('Run the frozen operator copy only')
    for name, key in (('registration.json', 'registration_sha256'), ('runtime-delta.json', 'runtime_delta_sha256'),
                      ('phase-budget.json', 'phase_budget_sha256')):
        if digest(root / name) != grant[key]:
            raise RuntimeError('Frozen package changed: ' + name)
    if digest(DEV / 'dev-validation.json') != grant['dev_receipt_sha256'] or digest(DEV / 'completion-audit.json') != grant['dev_audit_sha256']:
        raise RuntimeError('DEV evidence changed')
    return grant


def retire_old_supervisor(root):
    pid = OLD_SUPERVISOR['pid']
    proc = Path('/proc', str(pid))
    if not proc.exists():
        save(root / 'old-supervisor-retired.json', {'time': time.time(), 'pid': pid, 'already_gone': True})
        return
    argv = (proc / 'cmdline').read_bytes()
    state = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0]
    if OLD_SUPERVISOR['argv_marker'] not in argv or b'run' not in argv or state not in ('T', 't'):
        raise RuntimeError('PID %d is not the expected stopped old supervisor; no signal sent' % pid)
    save(root / 'old-supervisor-retirement-pending.json', {'time': time.time(), 'pid': pid, 'state': state})
    os.kill(pid, signal.SIGKILL)                   # never SIGCONT: it would dispatch the old formal version
    for _ in range(100):
        if not proc.exists() or (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0] in ('Z', 'X'):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError('Old supervisor did not terminate; refusing dispatch')
    save(root / 'old-supervisor-retired.json', {'time': time.time(), 'pid': pid, 'signal': 'SIGKILL',
                                                 'scope': 'that one stopped process; no data removed'})


def integrity_problems(root, reg, B, H):
    """Technical faults in ENDED episodes and the Table 2 ledger. Never repair outcome or cost."""
    problems = []
    for item in reg['plan']:
        name = item['task'].replace('/', '__') + '__' + item['arm'] + '__r' + str(item['repeat'])
        project = root / 'episodes' / name
        try:
            data = read(project / 'episode.json')
        except (OSError, ValueError):
            continue
        if 'ended' not in data:
            continue
        ledger = project / 'reports/resource_ledger.jsonl'
        events = [json.loads(l) for l in ledger.read_text().splitlines() if l] if ledger.exists() else []
        accounting = B.T.accounting_check(events, list((project / 'backend').glob('RUN_*')))
        usage = B.T.request_token_totals(events)
        derived = B.T.terminal_status(data.get('final') or {})
        tag = name + ': '
        if data.get('status') == 'ACCOUNTING_FAILURE' or not accounting['passed']:
            problems.append(tag + 'accounting failure')
        if not (data.get('split') == 'TEST' and data.get('paper_data') is True
                and all(data.get(k) == item[k] for k in ('task', 'arm', 'repeat'))):
            problems.append(tag + 'identity/label mismatch')
        if not usage.get('measurement_complete') or usage.get('unknown_usage_requests'):
            problems.append(tag + 'UNKNOWN usage')
        if data.get('status') != derived:
            problems.append(tag + 'terminal status inconsistent (%s vs %s)' % (data.get('status'), derived))
        error = data.get('error') or ''
        if error and error not in KNOWN_RESULT_ERRORS:
            problems.append(tag + 'runtime exception: ' + error[:160])
        man = read(project / 'manifest.json') if (project / 'manifest.json').exists() else {}
        allowed = set(H.executable_knobs(man))
        for e in events:
            reason = str(e.get('reason') or '')
            if e.get('type') == 'repair_candidate' and reason.startswith('non_whitelisted:'):
                if set(reason.split(':', 1)[1].split(',')) & allowed:
                    problems.append(tag + 'published knob refused: ' + reason)
    bank = root / 'phase/resource_ledger.jsonl'
    if bank.exists():
        for line in bank.read_text().splitlines():
            e = json.loads(line)
            if e.get('type') == 'budget_refused':
                problems.append('ledger refusal: ' + str(e.get('reason')))
            if e.get('type') == 'budget_settle' and 'UNKNOWN' in (e.get('actual') or {}).values():
                problems.append('ledger UNKNOWN usage on hold ' + str(e.get('hold_id')))
    return sorted(set(problems))


def collect(root, B):
    reg = B.preflight(root, 'TEST')
    families = {r['task']: r['family'] for r in read(root / 'inventory.json')['tasks']}
    rows, events_all, missing = [], [], []
    for item in reg['plan']:
        name = item['task'].replace('/', '__') + '__' + item['arm'] + '__r' + str(item['repeat'])
        project = root / 'episodes' / name
        path = project / 'episode.json'
        if not path.exists():
            missing.append(item)
            continue
        data = read(path)
        ledger = project / 'reports/resource_ledger.jsonl'
        events = [json.loads(l) for l in ledger.read_text().splitlines() if l] if ledger.exists() else []
        events_all.extend(events)
        derived = B.T.terminal_status(data.get('final') or {})
        accounting = B.T.accounting_check(events, list((project / 'backend').glob('RUN_*')))
        labels = (data.get('split') == 'TEST' and data.get('paper_data') is True
                  and all(data.get(k) == item[k] for k in ('task', 'arm', 'repeat')))
        consistent = data['status'] == derived or (data['status'] == 'ACCOUNTING_FAILURE' and not accounting['passed'])
        components = {}
        for e in events:
            if e.get('type') == 'flow_run':
                components[e.get('component', 'unspecified')] = components.get(e.get('component', 'unspecified'), 0) + 1
        final = data.get('final') or {}
        rows.append({**item, 'family': families[item['task']], 'status': data['status'], 'ended': 'ended' in data,
                     'accepted': data.get('accepted'),
                     'strict_success_confirmed': bool(data['status'] == 'ACCEPTED' and derived == 'ACCEPTED'
                                                      and labels and consistent),
                     'episode': str(path), 'episode_sha256': digest(path), 'labels_valid': labels,
                     'terminal_classification_consistent': consistent, 'accounting': accounting,
                     'tokens': B.T.request_token_totals(events), 'wall_s': data.get('wall_s'),
                     'total_worker_wall_s': data.get('total_worker_wall_s'), 'harness_cpu_s': data.get('harness_cpu_s'),
                     'tool_calls': sum(e.get('type') == 'tool_call' for e in events),
                     'checker_runs': sum(e.get('type') == 'checker_run' for e in events),
                     'repair_candidates': sum(e.get('type') == 'repair_candidate' for e in events),
                     'flow_components': components, 'task_entry_fallback': data.get('task_entry_fallback'),
                     'final_failed': (final.get('acceptance') or {}).get('failed'),
                     'final_unknown': (final.get('acceptance') or {}).get('unknown'),
                     'terminal_reason': data.get('terminal_reason'), 'error': data.get('error')})
    index = {(r['task'], r['repeat'], r['arm']): r for r in rows}
    by_arm = {}
    for arm in reg['arms']:
        part = [r for r in rows if r['arm'] == arm]
        denom = sum(r['arm'] == arm for r in reg['plan'])
        complete = len(part) == denom and all(r['ended'] for r in part)
        usage = complete and all(r['tokens']['measurement_complete'] for r in part)
        walls = [r['total_worker_wall_s'] for r in part]
        paired = {'G': 0, 'H': 0, 'eligible_pairs': 0, 'unverified_or_missing_pairs': 0, 'by_source': {}}
        for task in reg['tasks']:
            for rep in range(1, reg['repeats'] + 1):
                x, nb = index.get((task['task'], rep, arm)), index.get((task['task'], rep, 'NB'))
                group = paired['by_source'].setdefault(families[task['task']],
                                                       {'G': 0, 'H': 0, 'eligible_pairs': 0, 'unverified_or_missing_pairs': 0})
                if not x or not nb or any(r['status'] not in ('ACCEPTED', 'NOT_ACCEPTED')
                                          or not r['terminal_classification_consistent'] for r in (x, nb)):
                    paired['unverified_or_missing_pairs'] += 1
                    group['unverified_or_missing_pairs'] += 1
                    continue
                paired['eligible_pairs'] += 1
                group['eligible_pairs'] += 1
                for label, cond in (('G', x['strict_success_confirmed'] and not nb['strict_success_confirmed']),
                                    ('H', nb['strict_success_confirmed'] and not x['strict_success_confirmed'])):
                    paired[label] += int(cond)
                    group[label] += int(cond)
        by_arm[arm] = {
            'registered': denom, 'records': len(part),
            'strict_successes': sum(r['strict_success_confirmed'] for r in part),
            'status_counts': {s: sum(r['status'] == s for r in part) for s in sorted({r['status'] for r in part})},
            'mean_total_tokens_all_registered': (sum(r['tokens']['known_input_tokens'] + r['tokens']['known_output_tokens']
                                                     for r in part) / denom) if usage else 'UNKNOWN',
            'mean_requests_all_registered': sum(r['tokens']['llm_requests'] for r in part) / denom if complete else 'UNKNOWN',
            'mean_repair_requests_all_registered': sum(r['tokens']['repair_requests'] for r in part) / denom if complete else 'UNKNOWN',
            'mean_flows_all_registered': sum(r['accounting']['flows'] for r in part) / denom if complete else 'UNKNOWN',
            'mean_tool_calls_all_registered': sum(r['tool_calls'] for r in part) / denom if complete else 'UNKNOWN',
            'mean_worker_wall_s_all_registered': (sum(walls) / denom) if complete and all(
                isinstance(w, (int, float)) for w in walls) else 'UNKNOWN',
            'paired_vs_NB': paired}
    result = {'schema': 't2a-table2-results-v1', 'completed_at': time.time(),
              'execution_complete': not missing and len(rows) == len(reg['plan']) and all(r['ended'] for r in rows),
              'paused': (root / 'batch-paused.json').exists(),
              'records': rows, 'missing': missing, 'registered': len(reg['plan']), 'tasks': len(reg['tasks']),
              'source_families': len(set(families.values())), 'tokens': B.T.request_token_totals(events_all),
              'flows': sum(r['accounting']['flows'] for r in rows), 'tool_calls': sum(r['tool_calls'] for r in rows),
              'by_arm': by_arm, 'registration_sha256': digest(root / 'registration.json'),
              'dev_validation_boundary': reg.get('dev_validation_boundary'), 'disclosures': DISCLOSURES,
              'cost_notes': 'All-episode means include failures and stops. Tool and native-stage CPU records are nested, not additive.',
              'data_audit_passed': not missing and all(r['labels_valid'] and r['terminal_classification_consistent']
                                                       and r['accounting']['passed'] and r['tokens']['measurement_complete']
                                                       for r in rows)}
    save(root / 'table2-results.json', result)
    fields = ('task', 'family', 'repeat', 'arm', 'status', 'strict_success_confirmed', 'requests', 'repair_requests',
              'auxiliary_requests', 'input_tokens', 'output_tokens', 'flows', 'tool_calls', 'checker_runs',
              'total_worker_wall_s')
    with (root / 'table2-matrix.csv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: v for k, v in {**r, 'requests': r['tokens']['llm_requests'],
                             'repair_requests': r['tokens']['repair_requests'],
                             'auxiliary_requests': r['tokens']['auxiliary_requests'],
                             'input_tokens': r['tokens']['input_tokens'], 'output_tokens': r['tokens']['output_tokens'],
                             'flows': r['accounting']['flows']}.items() if k in fields})
    return result


def cmd_run(root):
    lock = (root / 'table2-control.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (root / 'table2-control-start.json').exists() or (root / 'batch-start.json').exists():
        raise RuntimeError('No implicit control or batch restart')
    check_binding(root)
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    import t2a_batch as B
    import r2g_memory_eval as H
    reg = B.preflight(root, 'TEST')
    retire_old_supervisor(root)

    def stage(name, **extra):
        save(root / 'table2-progress.json', {'stage': name, 'time': time.time(), **extra})
    save(root / 'table2-control-start.json', {'started': time.time(), 'pid': os.getpid(),
                                              'registration_sha256': digest(root / 'registration.json')})
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'CUDA_VISIBLE_DEVICES': '',
           'LD_LIBRARY_PATH': '/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib'}
    stage('TABLE2_RUNNING')
    with (root / 'worker.log').open('x') as log:
        batch = subprocess.Popen([sys.executable, '-u', str(root / 'code/memory/scripts/t2a_batch.py'), 'batch',
                                  '--root', str(root), '--split', 'TEST'], env=env, stdout=log, stderr=subprocess.STDOUT)
        while batch.poll() is None:
            problems = integrity_problems(root, reg, B, H)
            if problems and not (root / 'dispatch-pause.json').exists():
                save(root / 'dispatch-pause.json', {'time': time.time(), 'reason': 'technical integrity',
                                                    'problems': problems})
                stage('PAUSE_REQUESTED_AT_NEXT_WAVE', problems=problems)
            time.sleep(60)
    result = collect(root, B)
    if batch.returncode:
        stage('STOPPED_NO_RETRY', error='batch exited %d' % batch.returncode, results=str(root / 'table2-results.json'))
        raise SystemExit(batch.returncode)
    stage('PAUSED_FOR_INTEGRITY_REVIEW' if result['paused'] else 'COMPLETE',
          execution_complete=result['execution_complete'], data_audit_passed=result['data_audit_passed'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('delta', 'prepare', 'run'))
    p.add_argument('--root', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--delta', type=Path)
    p.add_argument('--junit', type=Path)
    p.add_argument('--replay')
    a = p.parse_args()
    if a.command == 'delta':
        cmd_delta(a.out, a.junit, a.replay)
    elif a.command == 'prepare':
        cmd_prepare(a.root.resolve(), a.delta.resolve())
    else:
        cmd_run(a.root.resolve())
