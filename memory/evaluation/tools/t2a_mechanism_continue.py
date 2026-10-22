#!/usr/bin/env python3
"""Explicit C9 continuation of unstarted R4 DEV, never a retry/formal launcher."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path('/data1/zhangdy/tehm-integration')
CASES = ('cts_partial', 'completed_area', 'place_partial', 'task_entry')


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def bank_ready(bank):
    used = bank.get('budget_spent', {})
    if bank.get('open_holds') or bank.get('caps') != {'llm_requests': 5000}:
        raise RuntimeError('Existing bank has open calls or changed caps')
    if bank.get('combined_tokens_cap') != 17000000:
        raise RuntimeError('Existing token cap changed')
    if 5000 - used.get('llm_requests', 5000) < 96:
        raise RuntimeError('Fewer than the predeclared 96 requests remain')
    amounts = [used.get(k) for k in ('tokens_in', 'tokens_out')]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in amounts):
        raise RuntimeError('Unresolved UNKNOWN occupancy')
    if sum(amounts) >= 17000000:
        raise RuntimeError('Existing token bank exhausted')
    if not any(e.get('hold_id') == 'db96c8422b27' and
               e.get('upper_bound') == {'tokens_in': 5362, 'tokens_out': 800} and
               e.get('actual_remains') == 'UNKNOWN' for e in bank.get('unknown_occupancies', [])):
        raise RuntimeError('C9 approved occupancy is not recorded')


def native_receipts(root, runtime):
    grant = read(runtime / 'registration.json')
    receipts = []
    for case in CASES:
        output = root / ('native-core-' + case)
        result = read(output / 'result.json')
        reg = read(output / 'registration.json')
        if (result.get('status') != 'PASS' or result.get('accounting', {}).get('passed') is not True
                or result.get('accounting', {}).get('flows') != 2
                or result.get('token_usage', {}).get('llm_requests') != 0
                or reg.get('case') != case or reg.get('llm_requests') != 0
                or reg.get('runtime_registration_sha256') != digest(runtime / 'registration.json')
                or reg.get('runtime_hashes') != grant['runtime_hashes']):
            raise RuntimeError('Native identity/cost/path gate failed: ' + case)
        receipts.append({'case': case, 'root': str(output),
                         'registration_sha256': digest(output / 'registration.json'),
                         'result_sha256': digest(output / 'result.json')})
    return receipts


def prepare(root):
    cont = root / 'continuation-c9'
    if cont.exists():
        raise RuntimeError('Existing continuation; never overwrite')
    old = read(root / 'registration.json')
    if (root / 'core/batch-start.json').exists():
        raise RuntimeError('CORE already started; cannot retry it')
    if read(root / 'progress.json').get('stage') != 'STOPPED_NO_RETRY':
        raise RuntimeError('Original stopped cycle must remain intact')
    prior = Path(old['prior_dev'])
    response = prior / 'episodes/riscv_core__u19__RECIPE__r1/repair-turn-1.try0.response.json'
    data = read(response)
    if '900-second' not in data.get('error', {}).get('message', '') or 'usage' in data:
        raise RuntimeError('Not the inspected provider-start failure')
    auth = root.parent / 't2a-control-construction-c2-20261011/C9-unknown-occupancy-20261015.json'
    if read(auth).get('user_approved') is not True:
        raise RuntimeError('C9 authorization missing')
    cont.mkdir()
    shutil.copy2(Path(__file__), cont / Path(__file__).name)
    contract = REPO / 'memory/evaluation/r2g_T2a_C9_single_library_learning_20261015.md'
    shutil.copy2(contract, cont / 'contract.md')
    paths = [root / p for p in ('registration.json', 'progress.json', 'unit-validation.json',
                                'combined-regression.xml', 'core/registration.json')]
    paths += [prior / p for p in ('registration.json', 'batch-complete.json', 'dev-validation.json')]
    paths += [response, auth, Path(old['pause_file']), cont / Path(__file__).name, cont / 'contract.md']
    paths += [Path(p) for p in old['files']]
    save(cont / 'registration.json', {
        'schema': 't2a-r4-c9-explicit-continuation-v1', 'registered_at': time.time(),
        'root': str(root), 'files': {str(p): digest(p) for p in paths},
        'runtime_hashes': old['runtime_hashes'], 'phase_binding': old['existing_shared_bank'],
        'prior_dev_passed': False, 'original_cycle_remains_stopped': True,
        'reason': 'Provider error inspected; approved UNKNOWN occupancy only. Fresh final seven-arm/native gates remain mandatory.',
        'max_new_requests': 96, 'planned_episodes': 31, 'no_retry': True,
        'no_budget_reset': True, 'formal_launch_allowed': False,
        'paper_platform': 'sky130hd',
        'historical_sky130hs_dev': 'Previously registered engineering diagnostic only; never single-library paper data',
    })
    print(json.dumps({'status': 'PREPARED_EXPLICIT_C9_CONTINUATION', 'path': str(cont)}))


def run(root):
    cont = root / 'continuation-c9'
    lock = (cont / 'cycle.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (cont / 'start.json').exists():
        raise RuntimeError('Continuation already started; no implicit retry')
    grant = read(cont / 'registration.json')
    if str(Path(__file__).resolve()) not in grant['files']:
        raise RuntimeError('Run the frozen continuation operator only')

    def unchanged():
        for path, sha in grant['files'].items():
            if digest(path) != sha:
                raise RuntimeError('Continuation-bound evidence changed: ' + path)
        for rel, sha in grant['runtime_hashes'].items():
            if digest(REPO / rel) != sha:
                raise RuntimeError('Runtime changed after full regression')
        if (root.parent / 't2a-formal-f3-20261015').exists():
            raise RuntimeError('Formal queue resumed')

    def stage(name, **extra):
        save(cont / 'progress.json', {'stage': name, 'time': time.time(), **extra})

    unchanged()
    save(cont / 'start.json', {'pid': os.getpid(), 'started': time.time(),
                              'registration_sha256': digest(cont / 'registration.json')})
    try:
        stage('WAITING_FOR_NATIVE_COMPLETION')
        while read(root / 'native-validation-progress.json').get('stage') == 'NATIVE_RUNNING':
            unchanged()
            time.sleep(30)
        if read(root / 'native-validation-progress.json').get('stage') != 'NATIVE_COMPLETE_PAID_BLOCKED':
            raise RuntimeError('Independent native checks did not complete successfully')
        core = root / 'core'
        initial_native = native_receipts(root, core)
        save(cont / 'initial-native-validation.json', {'checks': initial_native})
        cycle = module('frozen_r4_cycle', root / 'operator/t2a_mechanism_cycle.py')
        tests = cycle.junit(root / 'combined-regression.xml')
        if read(root / 'unit-validation.json').get('passed') is not True or tests['sha256'] != read(root / 'unit-validation.json')['junit']['sha256']:
            raise RuntimeError('Exact-candidate full regression is not bound')
        sys.path.insert(0, str(core / 'code/memory/scripts'))
        import t2a_batch as B
        import t2a_dev as D
        import t2a_mechanisms as M
        B.preflight(core, 'DEV')
        audit = module('c9_independent_audit', root / 'operator/t2a_dev_completion_audit.py')
        if audit.owned_survivors(Path(read(root / 'registration.json')['prior_dev'])):
            raise RuntimeError('Previous DEV left owned tools')
        binding = grant['phase_binding']
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', CUDA_VISIBLE_DEVICES='',
                   LD_LIBRARY_PATH='/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib')
        bank_env = dict(env, R2G_LEDGER_PATH=binding['ledger_path'], R2G_BUDGET_FILE=binding['budget_file'])
        bank = subprocess.run([sys.executable, str(core / 'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'),
                               'summary', str(root), '--json'], env=bank_env, capture_output=True, text=True, check=True)
        bank = json.loads(bank.stdout)
        bank_ready(bank)
        save(cont / 'bank-before-dispatch.json', bank)

        def launch(command, log):
            unchanged()
            with log.open('x') as out:
                subprocess.run(command, env=env, stdout=out, stderr=subprocess.STDOUT, check=True)

        def batch(path):
            launch([sys.executable, str(path / 'code/memory/scripts/t2a_batch.py'), 'batch',
                    '--root', str(path), '--split', 'DEV'], path / 'worker.log')
            if audit.owned_survivors(path):
                raise RuntimeError('DEV left owned tools')
            result = M.collect_pairs(path)
            if not result['data_audit_passed']:
                raise RuntimeError('DEV identity/accounting/cost audit failed')
            return result

        stage('CORE_DEV_RUNNING')
        reports = {'core': batch(core)}
        samples = M.samples_from_dev(core)
        save(root / 'priority-samples.json', {'source': 'audited CORE DEV only', 'samples': samples})
        for label, priority, fallback in [('full', True, True), ('no-priority', False, True), ('no-fallback', True, False)]:
            policy = root / (label + '-policy.json')
            save(policy, {'schema': M.SCHEMA, 'task_entry_fallback': fallback,
                          'cost_priority': priority, 'priority_samples': samples})
            B.prepare(root / label, core / 'inventory.json', core / 'controls', None, core / 'store',
                      split='DEV', mechanism_policy=policy, dev_arms=('NB', 'TM0'))
            stage(label.upper().replace('-', '_') + '_DEV_RUNNING')
            reports[label] = batch(root / label)
        save(root / 'ablation-results.json', {'schema': 't2a-mechanism-ablations-r4-v1', 'reports': reports,
                                             'paper_data': False, 'formal_launch_allowed': False})
        if not reports['full']['dev_release_gate_passed']:
            stage('COMPLETE_DEV_NEGATIVE_NO_RELEASE')
            return
        final = root / 'final-seven-arm'
        D.prepare(final, parallel=False, mechanism_policy=root / 'full-policy.json')
        stage('EXACT_FINAL_NATIVE_CHECKS')
        receipts = []
        for case in CASES:
            output = root / ('native-final-' + case)
            launch(['taskset', '-c', ','.join(map(str, B.CPU_SLOTS[0])), sys.executable,
                    str(root / 'operator/t2a_native_restoration_check.py'), '--runtime', str(final),
                    '--root', str(output), '--case', case], root / ('native-final-' + case + '.log'))
            result = read(output / 'result.json')
            if result.get('status') != 'PASS' or result.get('token_usage', {}).get('llm_requests') != 0:
                raise RuntimeError('Exact-final native diagnostic failed')
            receipts.append({'case': case, 'root': str(output),
                             'registration_sha256': digest(output / 'registration.json'),
                             'result_sha256': digest(output / 'result.json')})
        stage('FINAL_SEVEN_ARM_DEV_RUNNING')
        launch([sys.executable, str(final / 'code/memory/scripts/t2a_dev.py'), 'batch', '--root', str(final)], final / 'worker.log')
        save(final / 'completion-audit-registration.json', {
            'registration_sha256': digest(final / 'registration.json'),
            'auditor_sha256': digest(root / 'operator/t2a_dev_completion_audit.py'),
            'native_checks': [r for r in receipts if r['case'] != 'task_entry']})
        if not audit.audit(final)['passed']:
            raise RuntimeError('Fresh independent seven-arm DEV/native gate failed')
        if not any((read(p).get('task_entry_fallback') or {}).get('executed') for p in (root / 'full/episodes').glob('*/episode.json')):
            raise RuntimeError('Task-entry fallback not exercised; cannot claim verification')
        save(root / 'release-freeze.json', {
            'status': 'DEV_VALIDATED_FROZEN_NOT_FORMAL', 'root': str(final),
            'registration_sha256': digest(final / 'registration.json'),
            'dev_validation_sha256': digest(final / 'dev-validation.json'),
            'completion_audit_sha256': digest(final / 'completion-audit.json'),
            'performance_report_sha256': digest(root / 'ablation-results.json'), 'formal_launch_allowed': False})
        stage('DEV_VALIDATED_FROZEN_WAITING_FOR_FORMAL_DECISION')
    except Exception as exc:
        stage('STOPPED_NO_RETRY', error=str(exc))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.command == 'prepare' else run)(args.root.resolve())
