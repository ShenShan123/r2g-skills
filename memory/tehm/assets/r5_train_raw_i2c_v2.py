"""Pinned, read-only I2C NACK v2 TRAIN evidence replay, not promotion authority.

Three researcher-assisted reused-DEV components. The frozen package runner's own
``verify()`` is executed for the original and recovery trees in a network-isolated,
read-only sandbox; its ``prepare``/``inner``/``main`` (which run EDA) are never
entered. Attempt r1 and the failed preparation stay in the package, outside the
sealed receipt scope, and are not reinterpreted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from tehm.evaluation.research_r5_s2_native import sandbox
from tehm.ids import stable_dumps

TRAIN = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/i2c-nack-v2-r1')
RECEIPT_SHA = '72a431056c36e6d88b9d391f454910b48cf56b618fe6e4c7bc480a90d41d30da'
RUNNER_SHA = 'ff634b4f3c58258b51334dc4b0f3144839d6b07201b797a3c2050e7fbf244d46'
OSS = '/opt/pdk_klayout_openroad/oss-cad-suite'
CASE_ORDER = ('alex', 'zip', 'freecores')
ROLES = ('top', 'byte_ctrl', 'bit_ctrl', 'defines')
SOURCES = {
    'alex': {'repository': 'alexforencich/verilog-i2c', 'commit': 'a65be4045e898a52e791c6ee71f8f79a7cd2e129',
             'public_context': {'interface': 'dedicated_missed_ack_output', 'defined_macros': []},
             'fault_sha256': 'sha256:b4951f32e3387cf2a9984999defb66e49c7f2fa3832ea9a39bd8555c25d7accf',
             'candidate_sha256': 'sha256:9c4e05759f356e9ddd91af6981a1fcc10466cad0af6e5afb6553787fe9ea0328',
             'oracle_kind': 'native_myhdl_icarus_5_phase'},
    'zip': {'repository': 'ZipCPU/wbi2c', 'commit': 'afa64c2c151731fd4bcd5b3af9dd3b9a84c857e2',
            'public_context': {'interface': 'wishbone_status_err_bit_30', 'defined_macros': []},
            'fault_sha256': 'sha256:e9add7fdc8d33939b8abc693a096a589be0417f0baab7e8be2f0fa32c379afb2',
            'candidate_sha256': 'sha256:3d8c2f9bc704a4f71bd028e951246c00b6361d68154124261245c53734e12b29',
            'oracle_kind': 'research_augmented_not_native'},
    'freecores': {'repository': 'freecores/i2c', 'commit': '3b067f00ccced753b0502024766a51f58f3e04bc',
                  'public_context': {'interface': 'wishbone_status_err_bit_7', 'parameters': {'ARST_LVL': 0},
                                     'defined_macros': []},
                  'fault_sha256': {
                      'top': 'sha256:29562e912ae644ec61e5af9e1341d1c42b73868f8c0911622ecd0d71f3aa00ff',
                      'byte_ctrl': 'sha256:5e084e3cfcdd30ab7efe9e83dc2202d87a093a2cbe157c2efef0a6de25a45889',
                      'bit_ctrl': 'sha256:09e103a0abaef32a2666145166a51627682fbd050eb3bc64d4b0d8f94ab2fcfe',
                      'defines': 'sha256:a20cc941c1fbc35f0d24a8283d4683046c0d896860c5ece0ab0715f76a0458ba'},
                  'candidate_sha256': {
                      'top': 'sha256:0382530c50abfede70ad5788d68b8a65272b189fe093e61c5635f63ccaa58577',
                      'byte_ctrl': 'sha256:5e084e3cfcdd30ab7efe9e83dc2202d87a093a2cbe157c2efef0a6de25a45889',
                      'bit_ctrl': 'sha256:09e103a0abaef32a2666145166a51627682fbd050eb3bc64d4b0d8f94ab2fcfe',
                      'defines': 'sha256:a20cc941c1fbc35f0d24a8283d4683046c0d896860c5ece0ab0715f76a0458ba'},
                  'oracle_kind': 'native_icarus_log_semantics'},
}
_FAILED = {'target': 'FAIL', 'preservation': 'PASS', 'native': 'FAIL'}
_PASSED = {'target': 'PASS', 'preservation': 'PASS', 'native': 'PASS'}
_ZIP = lambda target, aug: {'target': target, 'preservation': 'PASS',
                            'native': 'NOT_APPLICABLE_AUGMENTED', 'augmented_scope': aug}
EXPECTED_MATRIX = {
    'alex': {'fault': _FAILED, 'candidate': _PASSED, 'rollback': _FAILED},
    'zip': {'fault': _ZIP('FAIL', 'FAIL'), 'candidate': _ZIP('PASS', 'PASS'), 'rollback': _ZIP('FAIL', 'FAIL')},
    'freecores': {'fault': _FAILED, 'candidate': _PASSED, 'rollback': _FAILED},
}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError('I2C v2 TRAIN raw: ' + reason)


def _sha(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), 'missing/linked file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_bytes())


def _equal(left, right) -> bool:
    return stable_dumps(left) == stable_dumps(right)


def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): _sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.is_symlink()}


def _sealed(train: Path) -> dict:
    _require(train.is_dir() and train.absolute() == train.resolve(), 'linked/noncanonical package root')
    _require(_sha(train / 'receipt.json') == RECEIPT_SHA, 'receipt identity')
    receipt = _read(train / 'receipt.json')
    for key, sub in (('bundle_files', 'bundle'), ('original_files', 'original'), ('recovery_files', 'recovery')):
        _require(_tree(train / sub) == receipt[key], 'sealed inventory drift: ' + sub)
    bundle = train / 'bundle'
    lock = _read(bundle / 'lock.json')
    links = {p.relative_to(bundle).as_posix(): os.readlink(p) for p in sorted(bundle.rglob('*')) if p.is_symlink()}
    _require(links == lock['venv_symlinks'], 'bundle symlink drift')
    for tree in ('original', 'recovery'):
        _require(not any(p.is_symlink() for p in (train / tree).rglob('*')), 'linked evidence in ' + tree)
    _require(_sha(bundle / 'run.py') == RUNNER_SHA, 'frozen runner identity')
    return receipt


# Only verify() is called; -I ignores Python env/user site; optimize=0 keeps asserts.
_CHILD = '''
import json, sys
from pathlib import Path
if sys.flags.optimize != 0 or not sys.dont_write_bytecode:
    raise RuntimeError("invalid cold verifier interpreter flags")
root = Path('/evidence')
path = root / 'bundle/run.py'
namespace = {'__name__': 'frozen_i2c_v2_train_audit', '__file__': str(path)}
exec(compile(path.read_bytes(), str(path), 'exec', optimize=0), namespace)
verify = namespace['verify']
original = verify(root / 'bundle', root / 'original')
recovery = verify(root / 'bundle', root / 'recovery')
print(json.dumps({'original': original, 'recovery': recovery}, sort_keys=True))
'''


def _replay(train: Path) -> dict:
    argv = sandbox() + ['--ro-bind', OSS, OSS, '--ro-bind', str(train), '/evidence', '--chdir', '/',
                        '/usr/bin/python3', '-I', '-B', '-c', _CHILD]
    try:
        process = subprocess.run(argv, capture_output=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('I2C v2 TRAIN raw: cold verifier unavailable/timeout') from exc
    _require(process.returncode == 0 and not process.stderr,
             'isolated cold verifier failed: ' + process.stderr.decode(errors='replace')[-1600:])
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('I2C v2 TRAIN raw: malformed cold verifier result') from exc
    _require(isinstance(result, dict) and set(result) == {'original', 'recovery'}, 'replay result shape')
    return result


def _check_audit(audit: dict) -> None:
    _require(audit.get('role') == 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV_V2' and
             audit.get('software_commit') == '317c8a3' and audit.get('raw_chain_verified') is True,
             'audit role/software/raw chain')
    _require(_equal(audit.get('matrix'), EXPECTED_MATRIX), 'raw verdict matrix (UNKNOWN is not PASS)')
    _require(_equal(audit.get('binding_status'), {c: 'BOUND' for c in CASE_ORDER}), 'binding status')
    _require(_equal(audit.get('component_training_obligations_pass'), {c: True for c in CASE_ORDER}),
             'TRAIN obligations')
    _require(_equal(audit.get('candidate_hashes'), {c: SOURCES[c]['candidate_sha256'] for c in CASE_ORDER}),
             'candidate identity')
    for key in ('independent_lineages_established', 'memory_authority_granted', 'memory_mremove'):
        _require(audit.get(key) is False, 'scope flag ' + key)
    _require(audit.get('model_calls') == 0 and audit.get('final_tasks') == 0 and
             audit.get('new_method_tasks') == 0, 'scope counters')


def verify(training_root: Path = TRAIN) -> dict:
    """Cold-replay the exact registered package. Raises on drift, disagreement, or UNKNOWN."""
    train = Path(training_root)
    receipt = _sealed(train)
    replay = _replay(train)
    saved = [replay['original'], replay['recovery'], _read(train / 'original/audit.json'), receipt['audit']]
    for audit in saved:
        _check_audit(audit)
        _require(_equal(audit, saved[0]), 'raw/saved/recovery audit disagreement')
    _require(receipt['actual_original_evaluations'] == 9 and receipt['actual_recovery_evaluations'] == 9,
             'evaluation counts')
    bundle = train / 'bundle'
    contexts = _read(bundle / 'inputs/contexts.json')
    for case in CASE_ORDER:
        _require(_equal(contexts[case], SOURCES[case]['public_context']), 'public context drift')
    _require('sha256:' + _sha(bundle / 'inputs/alex.v') == SOURCES['alex']['fault_sha256'] and
             'sha256:' + _sha(bundle / 'inputs/zip.v') == SOURCES['zip']['fault_sha256'] and
             all('sha256:' + _sha(bundle / 'inputs/freecores' / (r + '.v')) == SOURCES['freecores']['fault_sha256'][r]
                 for r in ROLES), 'fault source drift')
    _sealed(train)  # no successful verdict survives evidence drift during replay
    cases = {case: {**SOURCES[case], 'role': saved[0]['role'], 'matrix': EXPECTED_MATRIX[case],
                    'receipt_sha256': RECEIPT_SHA} for case in CASE_ORDER}
    result = {'schema': 'tehm-r5-i2c-v2-three-component-train-raw-v1', 'case_order': list(CASE_ORDER),
              'cases': cases, 'raw_chain_verified': True, 'source_components': 3,
              'independent_lineages_established': False, 'original_evaluations': 9,
              'recovery_evaluations': 9, 'attempt_r1_preserved_outside_seal': True,
              'cold_replay_new_simulator_executions': 0, 'model_calls': 0,
              'memory_authority_granted': False, 'memory_mremove': False,
              'heldout_transfer': False, 'final_tasks': 0,
              'zipcpu_oracle': 'research_augmented_not_native'}
    result['digest'] = 'sha256:' + hashlib.sha256(stable_dumps(result).encode()).hexdigest()
    return result


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2, sort_keys=True))
