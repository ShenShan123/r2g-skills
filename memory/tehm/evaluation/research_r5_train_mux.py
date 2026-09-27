"""Cold audit of the v8 mux TRAIN component, never Asset/Knowledge authority.

Verify source/action/rollback and reparse raw logs under a frozen scope. A saved
PASS flag alone is not evidence; a source rollback is not Memory removal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v8 import payload_from_source_v8

ROLE = 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV'
CONTEXT = {'WIDTH': 8, 'defined_macros': []}
FAULT_SHA = '764a18706f86f57a40bcede1eb3805e393c48585a48c52fe1f16d23060242829'
SCOPES = ('target', 'preservation', 'native')
ARMS = ('fault', 'candidate', 'rollback')
BIN = '/opt/pdk_klayout_openroad/oss-cad-suite/bin/'


def sha(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or linked evidence: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, reason):
    if not condition:
        raise ValueError('v8 mux TRAIN audit: ' + reason)


def inventory(root):
    require(not any(p.is_symlink() for p in root.rglob('*')), 'symlink in evidence')
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def classify(scope, commands, text, compiled):
    """Raw functional verdict; malformed, missing and contradictory is UNKNOWN."""
    count = 1 if scope == 'native' else 2
    if (scope not in SCOPES or len(commands) != count or not compiled or
            any(type(c.get('returncode')) is not int or c.get('timeout') is not False
                for c in commands) or
            (scope != 'native' and commands[0]['returncode'] != 0)):
        return 'UNKNOWN'
    rc = commands[-1]['returncode']
    if scope == 'native':
        passed = re.findall(r'^PASS: (\d+) checks, (\d+) mismatches$', text, re.M)
        failed = re.findall(r'FAIL: (\d+) mismatches, (\d+) checks', text)
        if (rc == 0 and passed == [('1599', '0')] and not failed and
                not re.search(r'FATAL:|ERROR:|TIMEOUT', text)):
            return 'PASS'
        # The pinned native scoreboard uses $error for functional mismatches.
        # Do not confuse those with an infrastructure/parse error or discard them.
        errors = re.findall(r'^ERROR:.*$', text, re.M)
        payload_error = r'ERROR: tb/axis_skid_tb\.sv:104: t=\d+ m_tdata mismatch: got=[0-9a-fx]+ exp=[0-9a-fx]+'
        if (rc != 0 and not passed and len(failed) == 1 and
                0 < int(failed[0][0]) == len(errors) and int(failed[0][1]) == 1599 and
                all(re.fullmatch(payload_error, line) for line in errors) and
                not re.search(r'TIMEOUT', text)):
            return 'FAIL_NATIVE'
        return 'UNKNOWN'
    marker = {'target': 'R5_DREW_TARGET_PASS accepted=2 received=2',
              'preservation': 'R5_DREW_PRESERVATION_PASS accepted=3 received=3'}[scope]
    failure = 'DELIVERY_PAYLOAD_MISMATCH index=1 got=c3 expected=b2'
    if rc == 0 and text.count(marker) == 1 and not re.search(r'FATAL:|ERROR:|FAIL:|TIMEOUT', text):
        return 'PASS'
    if scope == 'target' and rc != 0 and failure in text and marker not in text and not re.search(r'ERROR:|TIMEOUT', text):
        return 'FAIL_SECOND_DELIVERY'
    return 'UNKNOWN'


def verify(bundle, run):
    bundle, run = Path(bundle), Path(run)
    lock = read(bundle / 'lock.json')
    require(lock['role'] == ROLE and lock['public_context'] == CONTEXT, 'role/context')
    require(inventory(bundle) == {**lock['files'], 'lock.json': sha(bundle / 'lock.json')}, 'bundle inventory')
    require(lock['action_software_commit'] == '70fa5e6' and lock['original_oracles'] == 9
            and lock['recovery_oracles'] == 9 and lock['model_calls'] == 0, 'frozen budget/software')
    for path, digest in lock['tools'].items():
        require(sha(Path(path)) == digest, 'tool identity')
    from tehm.evaluation.research_r5_s2_native import sandbox
    expected_worker = sandbox() + ['--ro-bind', '/bundle/code/memory', '/code',
        '--ro-bind', '/bundle/source.sv', '/source.sv',
        '--ro-bind', '/bundle/worker.py', '/worker.py', '--bind', '/out/worker', '/out',
        '--setenv', 'PYTHONPATH', '/code', '--chdir', '/', '/usr/bin/python3', '/worker.py']
    launch = read(run / 'worker-process/launch.json')
    process = read(run / 'worker-process/process.json')
    require(launch == {'argv': expected_worker, 'timeout_seconds': 60, 'retry_limit': 0}, 'worker visibility/limits')
    require(process['returncode'] == 0 and process['timed_out'] is False, 'worker terminal')
    for channel in ('stdout', 'stderr'):
        require(sha(run / ('worker-process/' + channel + '.log')) == process[channel + '_sha256'], 'worker logs')
    source = (bundle / 'source.sv').read_text()
    require(sha(bundle / 'source.sv') == FAULT_SHA, 'registered fault')
    payload = payload_from_source_v8(source, CONTEXT)
    candidate, edit = apply_rtl_action(source, payload)
    action = read(run / 'worker/action.json')
    require(action == {'role': ROLE, 'payload': payload, 'action': edit}, 'action replay')
    require((run / 'worker/candidate.sv').read_text() == candidate, 'candidate replay')
    require((run / 'backup-fault.sv').read_text() == source, 'rollback backup')
    require((run / 'candidate-before-rollback.sv').read_text() == candidate, 'candidate before restore')
    restoration = read(run / 'rollback.json')
    candidate_sha = hashlib.sha256(candidate.encode()).hexdigest()
    require(restoration == {'before_restore_sha256': candidate_sha,
            'after_restore_sha256': FAULT_SHA, 'backup_sha256': FAULT_SHA,
            'memory_mremove': False}, 'rollback receipt')
    summary = read(run / 'runner-summary.json')
    require(summary['role'] == ROLE and len(summary['rows']) == 9, 'attempt registry')
    require([(row['source'], row['scope']) for row in summary['rows']] ==
            [(a, s) for a in ARMS for s in SCOPES], 'attempt ordering/duplicates')
    matrix = {arm: {} for arm in ARMS}
    for row in summary['rows']:
        arm, scope = row['source'], row['scope']
        stage = run / 'oracles' / arm / scope
        staged = run / 'sources' / arm / 'source.sv'
        expected = candidate_sha if arm == 'candidate' else FAULT_SHA
        require(sha(staged) == expected == row['input_source_sha256'], 'arm source')
        dut = stage / ('rtl/axis_skid.sv' if scope == 'native' else 'dut.sv')
        require(sha(dut) == expected, 'compiled source copy')
        tb = 'tb/axis_skid_tb.sv' if scope == 'native' else 'tb.sv'
        pinned = bundle / ('private-native/' + tb if scope == 'native' else
                          'qualification/drewbabel-axis-skid-task-v1/evaluator-private/tb.sv')
        require(sha(stage / tb) == sha(pinned), 'test identity')
        if scope == 'native':
            require(sha(stage / 'Makefile') == sha(bundle / 'private-native/Makefile'), 'native Makefile')
        commands = row['commands']
        expected_commands = ([['/usr/bin/make', 'MOD=axis_skid', 'RTL=rtl/axis_skid.sv']]
            if scope == 'native' else [[BIN + 'iverilog', '-g2012', '-s', 'tb', '-o', 'sim.vvp'] +
            (['-DPRESERVATION'] if scope == 'preservation' else []) + ['dut.sv', 'tb.sv'],
            [BIN + 'vvp', '-n', 'sim.vvp']])
        require(0 < len(commands) <= len(expected_commands), 'command count')
        for index, command in enumerate(commands):
            require(command['argv'] == expected_commands[index], 'command identity')
            require(command['cwd'] == '/out/oracles/' + arm + '/' + scope, 'command cwd')
            for channel in ('stdout', 'stderr'):
                require(sha(stage / f'command-{index}.{channel}.log') == command[channel + '_sha256'], 'log digest')
        require(inventory(stage) == row['artifacts'], 'raw artifact inventory')
        text = ''.join(p.read_text() for p in sorted(stage.glob('command-*.log')))
        binary = stage / ('build/sim' if scope == 'native' else 'sim.vvp')
        source_name = b'rtl/axis_skid.sv' if scope == 'native' else b'dut.sv'
        compiled = binary.is_file() and source_name in binary.read_bytes()
        if scope == 'native':
            compiled = compiled and 'iverilog -g2012 ' in text and '\nvvp build/sim\n' in text
        matrix[arm][scope] = classify(scope, commands, text, compiled)
        require(matrix[arm][scope] == row['verdict'], 'saved/raw verdict disagreement')
    fault = {'target': 'FAIL_SECOND_DELIVERY', 'preservation': 'PASS', 'native': 'FAIL_NATIVE'}
    eligible = (matrix['fault'] == matrix['rollback'] == fault and
                all(value == 'PASS' for value in matrix['candidate'].values()))
    return {'schema': 'r5-v8-mux-train-raw-audit-v1', 'role': ROLE,
            'raw_chain_verified': True, 'component_training_obligations_pass': eligible,
            'matrix': matrix, 'candidate_sha256': candidate_sha, 'source_rollback_verified': True,
            'oracle_executions': 9, 'source_components': 1, 'model_calls': 0,
            'memory_authority_granted': False, 'memory_mremove': False,
            'heldout_transfer': False, 'new_method_tasks': 0, 'final_tasks': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.run), indent=2, sort_keys=True))
