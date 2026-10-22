"""T2a 2026-10-16: explicit runtime-delta acceptance and the independent, uncapped Table 2 ledger.

The TEST package may differ from the DEV-validated runtime only by a registered, per-file delta that names
its base, each file's base and new hash, and its test/replay evidence. Any unregistered difference is still
refused, and the registration states the true validation boundary (a real seven-arm DEV on the BASE plus a
test/replay-verified delta), never "the new runtime passed a real seven-arm DEV".
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
import t2a_batch as B  # noqa: E402

BASE = {'a.py': 'a0', 'b.py': 'b0', 'c.py': 'c0'}


def _delta(files, **over):
    d = {'schema': 't2a-runtime-delta-v1', 'validation_boundary': B.DELTA_BOUNDARY,
         'base_registration_sha256': 'r' * 64, 'base_runtime_hashes': dict(BASE), 'files': files,
         'evidence': {'junit_sha256': 'j' * 64, 'passed': 10, 'failed': 0, 'replay': 'x'},
         'disclosures': ['two TM0 rules newly executable']}
    d.update(over)
    return d


def test_an_exact_registered_delta_is_accepted():
    now = {**BASE, 'b.py': 'b1', 'd.py': 'd1'}
    files = {'b.py': {'base': 'b0', 'new': 'b1'}, 'd.py': {'base': None, 'new': 'd1'}}
    assert B.runtime_delta_errors(BASE, now, _delta(files)) == []


@pytest.mark.parametrize('now,files,needle', [
    ({**BASE, 'b.py': 'b1', 'c.py': 'c9'}, {'b.py': {'base': 'b0', 'new': 'b1'}}, 'unregistered'),
    ({**BASE, 'b.py': 'b2'}, {'b.py': {'base': 'b0', 'new': 'b1'}}, 'registered new hash'),
    ({**BASE, 'b.py': 'b1'}, {'b.py': {'base': 'bX', 'new': 'b1'}}, 'registered base hash'),
    ({**BASE}, {'b.py': {'base': 'b0', 'new': 'b1'}}, 'registered new hash'),
    ({k: v for k, v in BASE.items() if k != 'c.py'}, {}, 'unregistered'),
])
def test_any_unregistered_or_mismatched_difference_is_refused(now, files, needle):
    errors = B.runtime_delta_errors(BASE, now, _delta(files))
    assert errors and any(needle in e for e in errors)


@pytest.mark.parametrize('over,needle', [
    ({'base_runtime_hashes': {**BASE, 'a.py': 'zz'}}, 'base'),
    ({'validation_boundary': 'REAL_SEVEN_ARM_DEV_VALIDATED'}, 'boundary'),
    ({'evidence': {'junit_sha256': 'j' * 64, 'passed': 10, 'failed': 1, 'replay': 'x'}}, 'evidence'),
    ({'evidence': {}}, 'evidence'),
    ({'disclosures': None}, 'disclosures'),
])
def test_the_delta_needs_its_base_boundary_evidence_and_disclosures(over, needle):
    now = {**BASE, 'b.py': 'b1'}
    errors = B.runtime_delta_errors(BASE, now, _delta({'b.py': {'base': 'b0', 'new': 'b1'}}, **over))
    assert errors and any(needle in e for e in errors)


def test_no_delta_still_requires_the_exact_dev_runtime():
    assert B.runtime_delta_errors(BASE, dict(BASE), None) == []
    assert B.runtime_delta_errors(BASE, {**BASE, 'b.py': 'b1'}, None) == ['DEV did not validate this exact runtime']


def test_the_table2_ledger_is_independent_uncapped_and_pauses_on_unknown():
    budget = B.formal_phase_budget()
    assert budget['caps'] == {} and 'combined_tokens_cap' not in budget
    assert budget['halt_on_unknown_usage'] is True and budget['budget_id'] == 't2a-table2-independent'


def test_a_pause_request_stops_dispatch_at_the_next_wave_boundary(tmp_path, monkeypatch):
    # Integrity fail-fast (2026-10-16): the operator requests a pause; running episodes finish, no new wave
    # starts, nothing is retried, and the batch records that it paused instead of completing.
    root = tmp_path
    (root / 'code/memory/scripts').mkdir(parents=True)
    worker = root / 'code/memory/scripts/t2a_batch.py'
    worker.write_text('# worker')
    (root / 'registration.json').write_text('{}')
    plan = [{'task': 't/a', 'arm': a, 'repeat': 1} for a in ('NB', 'TM0', 'RET', 'AWM')]
    reg = {'plan': plan, 'files': {str(worker): B.Q.digest(worker)},
           'scheduler': {'cpu_slots': [[0], [1]], 'load_log_interval_s': 0}}
    monkeypatch.setattr(B, 'preflight', lambda r, s: reg)
    monkeypatch.setattr(B, 'validate_cpu_slots', lambda *a, **k: None)
    launched = []

    class Done:
        returncode = 0
        def poll(self): return 0

    def popen(cmd, **kw):
        launched.append(cmd[cmd.index('--arm') + 1])
        if len(launched) == 2:
            (root / 'dispatch-pause.json').write_text('{"reason": "integrity"}')
        return Done()
    monkeypatch.setattr(B.subprocess, 'Popen', popen)
    B.batch(root, 'TEST')
    assert launched == ['NB', 'TM0']
    assert (root / 'batch-paused.json').exists() and not (root / 'batch-complete.json').exists()


def test_the_table2_operator_flags_only_technical_faults(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('op', ROOT / 'memory/evaluation/tools/t2a_table2_operator.py')
    op = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(op)
    assert op.KNOWN_RESULT_ERRORS == ('Shared repair restoration is unverified',)
    assert any('NOT completed a real seven-arm DEV' in d for d in op.DISCLOSURES)
    assert any('rule_34ed200ab47aeb3e' in d and 'rule_78d25ae8eff3680f' in d for d in op.DISCLOSURES)
    # A measured repair failure is a result; an exception that is not the designed fail-closed stop is a fault.
    import r2g_memory_eval as H
    root = tmp_path
    plan = [{'task': 't/a', 'arm': 'NB', 'repeat': 1}, {'task': 't/a', 'arm': 'TM0', 'repeat': 1}]
    for arm, data in (('NB', {'status': 'NOT_ACCEPTED', 'error': None}),
                      ('TM0', {'status': 'UNVERIFIED_OR_SAFETY_STOP', 'error': 'KeyError: boom'})):
        p = root / 'episodes' / ('t__a__%s__r1' % arm)
        (p / 'reports').mkdir(parents=True)
        (p / 'episode.json').write_text(__import__('json').dumps(
            {**data, 'ended': 1, 'split': 'TEST', 'paper_data': True, 'task': 't/a', 'arm': arm, 'repeat': 1,
             'final': {}}))
        (p / 'reports/resource_ledger.jsonl').write_text('')

    class T:
        @staticmethod
        def accounting_check(e, r): return {'passed': True}
        @staticmethod
        def request_token_totals(e): return {'measurement_complete': True, 'unknown_usage_requests': 0}
        @staticmethod
        def terminal_status(f): return None

    class FakeB:
        pass
    FakeB.T = T
    T.terminal_status = staticmethod(lambda f, _s={'n': 0}: None)
    probs = op.integrity_problems(root, {'plan': plan}, FakeB, H)
    assert any('TM0' in p and 'runtime exception' in p for p in probs)
    assert not any(p.startswith('t__a__NB__r1') and 'exception' in p for p in probs)
