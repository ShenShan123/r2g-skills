"""Pinned, read-only v8 TRAIN evidence replay, not promotion authority.

Three reused-DEV components; original and recovery executions are not extra
tasks or lineages. Frozen auditors run with their own frozen imports, without
network, writable evidence, upstream checkouts or Memory databases. No EDA or
provider is launched. The mux r1 audit failure remains in the sealed package.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from tehm.evaluation.research_r5_s2_native import sandbox
from tehm.ids import stable_dumps

TRAIN = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training')
CASE_ORDER = ('axis_register', 'zipcpu_skidbuffer', 'mux_skid')
PACKAGES = {
    'axis_zip': {
        'directory': 'skid-v8-axis-zip-r1',
        'seal': '28acad31bb8b20d8e1da464e284fe027427deee3bb5fe2d7dafd17fe22b3b3aa',
        'receipt': 'bfc86a13f90a301e81cf15994761b5349ab9623636bb0d65e8d83bf45033a90d',
        'auditor': 'bundle/run.py',
        'auditor_sha': '7fb1ed0f7940076beb2843d4b5088f53a0e44cff84530749a054f401f3788ba7',
    },
    'mux': {
        'directory': 'skid-v8-mux-r1',
        'seal': '3094172a44d7e6f884b32ab9ed334438bfd8676f3bbbcf657aa2cd7d195b4cce',
        'receipt': '34129f5f8512bf2c916acea880762b7e0a0162a33942df4f6a33944668128a6d',
        'auditor': 'recovery-bundle/audit.py',
        'auditor_sha': '53ce91f3bd20313b8f49d4589c2b83bac2417cd69572b6d6249b3ee0585048e9',
    },
}
SOURCES = {
    'axis_register': {
        'repository': 'alexforencich/verilog-axis',
        'commit': '48ff7a7e2ef782cf778d47910cf85835c64b1bce',
        'source_file': 'rtl/axis_register.v',
        'fault_sha256': '000b18ba283dd3ad7ff6d9b5a2165df152441b662a43b4a34fd1ef8f88264e25',
        'candidate_sha256': '599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39',
        'public_context': {'DATA_WIDTH': 8, 'REG_TYPE': 2},
        'oracle_kind': 'native_cocotb_9_tests',
    },
    'zipcpu_skidbuffer': {
        'repository': 'ZipCPU/wb2axip',
        'commit': '2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b',
        'source_file': 'rtl/skidbuffer.v',
        'fault_sha256': 'ca72b46e9dd7744f90d302811603140fc688418dbdde5d811dee5c502ea33fa3',
        'candidate_sha256': 'ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389',
        'public_context': {'DW': 8, 'OPT_INITIAL': 1, 'OPT_LOWPOWER': 0,
                           'OPT_OUTREG': 1, 'OPT_PASSTHROUGH': 0},
        'oracle_kind': 'research_augmented_not_native',
    },
    'mux_skid': {
        'repository': 'drewbabel/eth-datapath',
        'commit': '5b8e276fe4cfd3387084709d40befbb9ddc30cfa',
        'source_file': 'rtl/axis_skid.sv',
        'fault_sha256': '764a18706f86f57a40bcede1eb3805e393c48585a48c52fe1f16d23060242829',
        'candidate_sha256': 'ebe739d7287bf59a133fe113fbb4b454447212c2c4df1f3629cb7bfd115c190a',
        'public_context': {'WIDTH': 8, 'defined_macros': []},
        'oracle_kind': 'research_augmented_plus_native_1599_checks',
    },
}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError('v8 TRAIN raw: ' + reason)


def _sha(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), 'missing/linked file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _equal(left: object, right: object) -> bool:
    # JSON booleans must not compare equal to integer counters/parameters.
    return stable_dumps(left) == stable_dumps(right)


def _sealed(root: Path, pin: dict) -> dict:
    _require(root.is_dir() and root.absolute() == root.resolve(), 'linked/noncanonical package root')
    _require(_sha(root / 'seal.json') == pin['seal'], 'seal identity')
    seal = _read(root / 'seal.json')
    files = seal['files']
    _require(isinstance(files, dict) and bool(files), 'empty file inventory')
    for name in files:
        relative = Path(name)
        _require(not relative.is_absolute() and '..' not in relative.parts
                 and relative.as_posix() == name and name != 'seal.json', 'unsafe inventory path')
    observed = {}
    for path in sorted(root.rglob('*')):
        _require(not path.is_symlink(), 'linked evidence')
        if path.is_dir():
            continue
        _require(path.is_file(), 'nonregular evidence')
        name = path.relative_to(root).as_posix()
        if name != 'seal.json':
            observed[name] = _sha(path)
    _require(observed == files, 'sealed file inventory drift')
    _require(_sha(root / 'receipt.json') == pin['receipt'] ==
             seal['recovery_receipt_sha256'], 'recovery receipt identity')
    _require(_sha(root / pin['auditor']) == pin['auditor_sha'], 'auditor identity')
    return seal


# Only verify() is called. In particular the frozen runner's prepare/inner/main
# methods (which can execute EDA) are never entered. -I ignores Python env and
# user site packages; optimize=0 keeps the reviewed frozen assert checks active.
_CHILD = '''
import json, sys
from pathlib import Path
if sys.flags.optimize != 0 or not sys.dont_write_bytecode:
    raise RuntimeError("invalid cold verifier interpreter flags")
kind, auditor = sys.argv[1:]
root = Path('/evidence')
sys.path.insert(0, str(root / 'bundle/code/memory'))
path = root / auditor
namespace = {'__name__': 'frozen_train_audit', '__file__': str(path)}
exec(compile(path.read_bytes(), str(path), 'exec', optimize=0), namespace)
verify = namespace['verify']
original = verify(root / 'bundle', root / 'original')
recovery_bundle = root / ('recovery-bundle' if kind == 'mux' else 'bundle')
recovery = verify(recovery_bundle, root / 'recovery')
print(json.dumps({'original': original, 'recovery': recovery}, sort_keys=True))
'''


def _replay(root: Path, kind: str, pin: dict) -> dict:
    toolchain = '/opt/pdk_klayout_openroad/oss-cad-suite'
    argv = sandbox() + ['--ro-bind', toolchain, toolchain,
        '--ro-bind', str(root), '/evidence', '--chdir', '/',
        '/usr/bin/python3', '-I', '-B', '-c', _CHILD, kind, pin['auditor']]
    try:
        process = subprocess.run(argv, capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('v8 TRAIN raw: cold verifier unavailable/timeout') from exc
    _require(process.returncode == 0 and not process.stderr,
             'isolated cold verifier failed: ' + process.stderr.decode(errors='replace')[-1600:])
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('v8 TRAIN raw: malformed cold verifier result') from exc
    _require(isinstance(result, dict) and set(result) == {'original', 'recovery'}, 'replay result shape')
    return result


def _expected(kind: str) -> dict:
    common = {'raw_chain_verified': True, 'component_training_obligations_pass': True,
        'oracle_executions': 9, 'model_calls': 0, 'memory_authority_granted': False,
        'memory_mremove': False, 'new_method_tasks': 0, 'final_tasks': 0}
    failed = {'target': 'FAIL', 'preservation': 'PASS', 'native': 'FAIL'}
    passed = {key: 'PASS' for key in failed}
    if kind == 'axis_zip':
        return {**common, 'schema': 'r5-v8-axis-zip-raw-train-v1',
            'role': 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV_V8', 'source_components': 2,
            'candidate_sha256': {c: SOURCES[c]['candidate_sha256'] for c in CASE_ORDER[:2]},
            'oracle_kinds': {c: SOURCES[c]['oracle_kind'] for c in CASE_ORDER[:2]},
            'zipcpu_native_payload_oracle': 'MISSED',
            'matrix': {
                'axis_register': {'fault': failed, 'candidate': passed, 'rollback': failed},
                'zipcpu_skidbuffer': {
                    arm: {key: value for key, value in verdict.items() if key != 'native'}
                    for arm, verdict in [('fault', failed), ('candidate', passed), ('rollback', failed)]}}}
    _require(kind == 'mux', 'unregistered package kind')
    failed = {'target': 'FAIL_SECOND_DELIVERY', 'preservation': 'PASS', 'native': 'FAIL_NATIVE'}
    return {**common, 'schema': 'r5-v8-mux-train-raw-audit-v1',
        'role': 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV', 'source_components': 1,
        'candidate_sha256': SOURCES['mux_skid']['candidate_sha256'],
        'source_rollback_verified': True, 'heldout_transfer': False,
        'matrix': {'fault': failed, 'candidate': passed, 'rollback': failed}}


def _verify_package(root: Path, kind: str, pin: dict) -> dict:
    _sealed(root, pin)
    replay = _replay(root, kind, pin)
    expected = _expected(kind)
    audit_file = 'audit-r2.json' if kind == 'mux' else 'audit.json'
    for value in [replay['original'], replay['recovery'],
                  _read(root / ('original/' + audit_file)),
                  _read(root / 'recovery/audit.json'), _read(root / 'receipt.json')['audit']]:
        _require(_equal(value, expected), 'raw/saved/recovery verdict or scope disagreement')
    _sealed(root, pin)  # No successful verdict survives evidence drift during replay.
    return expected


def verify(training_root: Path = TRAIN) -> dict:
    """Cold-replay exact registered packages; relocated sealed copies are allowed.

    Raises on missing/drifted evidence or UNKNOWN. No cache and no caller-supplied
    PASS flags. The result has no authority to promote an Asset or Knowledge.
    """
    training_root = Path(training_root)
    audits, cases = {}, {}
    for kind, pin in PACKAGES.items():
        root = training_root / pin['directory']
        audits[kind] = _verify_package(root, kind, pin)
        selected = CASE_ORDER[:2] if kind == 'axis_zip' else CASE_ORDER[2:]
        locks = _read(root / ('bundle/source-locks.json' if kind == 'axis_zip' else 'bundle/source-lock.json'))
        contexts = (_read(root / 'bundle/inputs/contexts.json') if kind == 'axis_zip'
                    else {'mux_skid': _read(root / 'bundle/lock.json')['public_context']})
        for case in selected:
            expected = SOURCES[case]
            lock = locks[case] if kind == 'axis_zip' else locks
            _require(lock['repository'] == expected['repository'] and
                     lock['source_git_sha' if kind == 'axis_zip' else 'commit'] == expected['commit'],
                     'source repository/commit drift')
            source = root / ('bundle/inputs/' + case + '.v' if kind == 'axis_zip' else 'bundle/source.sv')
            _require(_sha(source) == expected['fault_sha256'] and
                     _equal(contexts[case], expected['public_context']), 'source/context drift')
            cases[case] = {**expected, 'role': audits[kind]['role'],
                'package_seal_sha256': pin['seal'], 'recovery_receipt_sha256': pin['receipt'],
                'matrix': audits[kind]['matrix'][case] if kind == 'axis_zip' else audits[kind]['matrix']}
    result = {'schema': 'tehm-r5-v8-three-component-train-raw-v1',
        'case_order': list(CASE_ORDER), 'cases': cases, 'raw_chain_verified': True,
        'source_components': 3, 'independent_lineages_established': False,
        'original_evaluations': 18, 'recovery_evaluations': 18,
        'cold_replay_new_simulator_executions': 0, 'model_calls': 0,
        'memory_authority_granted': False, 'memory_mremove': False,
        'heldout_transfer': False, 'final_tasks': 0,
        'zipcpu_native_payload_oracle': 'MISSED', 'mux_original_audit_r1_failed': True}
    result['digest'] = 'sha256:' + hashlib.sha256(stable_dumps(result).encode()).hexdigest()
    return result
