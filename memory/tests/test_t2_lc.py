"""Learning curve LC-v1 (no EDA, no model): plan, growth binding rules, verified-write rules, dispatcher."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
sys.path.insert(0, str(ROOT / 'memory'))
import t2_lc as L  # noqa: E402

LEARN = [f'C{i:05d}__d{i}/initial' for i in range(1, 21)]
PROBE = [f'C{i:05d}__p{i}/initial' for i in range(101, 109)]


def test_plan_has_84_steps_with_probes_at_0_5_10_20_learn_designs():
    steps = L.plan_steps(LEARN, PROBE)
    assert len(steps) == 84
    assert sum(s['role'] == 'LEARN' for s in steps) == 20 and sum(s['role'] == 'PROBE' for s in steps) == 64
    learned = 0
    for s in steps:
        if s['role'] == 'LEARN':
            learned += 1
            assert s['order'] == learned and s['task'] == LEARN[learned - 1]
        else:
            assert s['checkpoint'] == learned                 # a checkpoint-c probe sees exactly c LEARN designs
    names = [s['name'] for s in steps]
    assert len(set(names)) == 84                              # every episode has its own directory
    assert {(s['checkpoint'], s['repeat']) for s in steps if s['task'] == PROBE[0]} == {
        (c, r) for c in (0, 5, 10, 20) for r in (1, 2)}


def make_store(path):
    """A minimal sealed TEHM-like store: tehm.sqlite only (bind_snapshot copies via sqlite backup)."""
    import sqlite3
    path.mkdir(parents=True)
    with sqlite3.connect(path / 'tehm.sqlite') as c:
        c.execute('CREATE TABLE t (x)')
    return path


@pytest.fixture
def growth(tmp_path, monkeypatch):
    from tehm import snapshot as S
    monkeypatch.setattr(S, '_referenced_artifacts', lambda conn: {})
    monkeypatch.setattr('tehm.db.read_watermark', lambda conn: 0, raising=False)
    root = tmp_path / 'pkg'
    root.mkdir()
    m0 = make_store(tmp_path / 'm0')
    head = L.init_growth(root, m0, {'entries': [], 'schema': 'x'}, LEARN[:2], PROBE[:1])
    return root, head


def test_only_the_next_learn_may_bind_and_probes_bind_read_only(growth, tmp_path):
    root, head = growth
    with pytest.raises(ValueError):
        L.bind(root, LEARN[1], 'LEARN', tmp_path / 'x1')      # not the next one
    with pytest.raises(ValueError):
        L.bind(root, 'C99999__other/initial', 'PROBE', tmp_path / 'x2')
    rec = L.bind(root, LEARN[0], 'LEARN', tmp_path / 'ok', expect_version='v000')
    assert rec['growth_version'] == 'v000' and (tmp_path / 'ok/growth-binding.json').is_file()
    L.bind(root, PROBE[0], 'PROBE', tmp_path / 'probe')
    with pytest.raises(RuntimeError):
        L.bind(root, PROBE[0], 'PROBE', tmp_path / 'p2', expect_version='v001')


def test_a_learn_with_no_executed_action_counts_as_experienced_without_a_write(growth, tmp_path, monkeypatch):
    root, head = growth
    project = tmp_path / 'episodes' / 'C00001__d1__initial__TM0__L01'
    (project / 'reports').mkdir(parents=True)
    L.bind(root, LEARN[0], 'LEARN', project / '.tehm-store')
    monkeypatch.setattr(L, 'T2_LOCK', tmp_path / 'lock.json')
    (tmp_path / 'lock.json').write_text(json.dumps({'entries': []}))
    src = tmp_path / 'v1'
    src.mkdir()
    (src / 'source_export.jsonl').write_text('')
    (src / 'source_manifest.json').write_text(json.dumps({'split': {'family_of_design': {}}}))
    monkeypatch.setattr(L, 'SOURCE_V1', src)
    step = {'role': 'LEARN', 'task': LEARN[0], 'name': project.name}
    r = L.commit_verified(root, step, project, {}, 'lineage_x')
    head2 = json.loads((root / 'growth/head.json').read_text())
    assert r['status'] == 'NO_WRITE' and head2['version'] == 'v000'
    assert head2['committed_learn'] == [LEARN[0]] and head2['experienced_learn_designs'] == 1
    with pytest.raises((RuntimeError, ValueError)):            # a second write for the same step is refused
        L.commit_verified(root, step, project, {}, 'lineage_x')


def test_success_needs_area_and_no_failure_in_a_check_unmeasured_before(tmp_path, monkeypatch):
    import t2a_envelope as E
    from ann_v2 import fields as FD
    proj = tmp_path / 'p'
    for run in ('R1', 'R2', 'R3'):
        (proj / 'backend' / run).mkdir(parents=True)
    area = {'R1': {'ok': True, 'violations': [], 'unverified': []}, 'R2': {'ok': False, 'violations': [], 'unverified': ['core']},
            'R3': {'ok': True, 'violations': [], 'unverified': []}}
    monkeypatch.setattr(E, 'area_metrics', lambda run: run.name)
    monkeypatch.setattr(E, 'check_area', lambda man, run: area[run])
    monkeypatch.setattr(FD, 'measure', lambda run: {'drc': {'measured': True, 'present': run.name == 'R3'}})
    rec = lambda uid, run, nf=None: {'unit_id': uid, 'admission': {'status': 'ADMITTED'},  # noqa: E731
                                     'binding': {'post': {'run': run}}, 'new_failures': nf or {}}
    ov = L.unit_overrides([rec('a', 'R1', {'drc': 'NOT_MEASURED_BEFORE'}), rec('b', 'R2'),
                           rec('c', 'R3', {'drc': 'NOT_MEASURED_BEFORE'}), rec('d', 'GONE'),
                           {'unit_id': 'e', 'admission': {'status': 'NOT_ADMITTED'}}], proj, {})
    assert 'a' not in ov and 'e' not in ov
    assert ov['b']['admitted'] is False and 'UNVERIFIED' in ov['b']['reason']
    assert ov['c']['outcome_class'] == 'cleared_new_failure'
    assert ov['d']['admitted'] is False


class Done:
    returncode = 0

    def poll(self):
        return 0


def lsu_timeout_fixture(root, create=True):
    step = {'name': 'lsu_probe', 'role': 'PROBE',
            'task': 'C02309__lsu__miaow/initial'}
    rec = {**step, 'status': 'TECHNICALLY_TERMINATED',
           'episode_status': 'UNVERIFIED_OR_SAFETY_STOP',
           'accounting_passed': True, 'write': 'NOT_ATTEMPTED'}
    project = root / 'episodes' / step['name']
    episode = {'accepted': False, 'status': 'UNVERIFIED_OR_SAFETY_STOP',
               'terminal_reason': 'restoration_unverified_or_safety_stop',
               'final': {'execution_recovery': 'SAFETY_STOP', 'recovery_rc': 124},
               'accounting': {'passed': True, 'errors': []},
               'resources': {'open_holds': 0}}
    if create:
        project.mkdir(parents=True)
        (project / 'episode.json').write_text(json.dumps(episode))
    return step, rec, episode, project


def test_registered_lsu_timeout_is_kept_but_does_not_block_next_probe(tmp_path, monkeypatch):
    import t2a_batch as B
    step, rec, episode, project = lsu_timeout_fixture(tmp_path, create=False)
    steps = [step, {**step, 'name': 'next_probe'}]
    monkeypatch.setattr(B, 'preflight', lambda *a:
                        {'steps': steps, 'scheduler': {'cpu_slots': [[0]]}, 'files': {}})
    launched = []
    def launcher(i):
        launched.append(i)
        if i == 0:
            project.mkdir(parents=True)
            (project / 'episode.json').write_text(json.dumps(episode))
        (tmp_path / 'steps').mkdir(exist_ok=True)
        L.save(tmp_path / 'steps' / f'{i:03d}.json', rec if i == 0 else {'status': 'OK'})
        return Done()
    state = L.dispatch(tmp_path, launcher=launcher, poll_s=0)
    assert state['status'] == 'COMPLETED' and launched == [0, 1]
    assert state['technical_skips'] == [0]
    assert L.load(tmp_path / 'steps/000.json') == rec


@pytest.mark.parametrize('mutation', ['learn', 'other_task', 'write', 'accounting',
                                    'open_hold', 'wrong_rc', 'accepted'])
def test_lsu_continuation_never_masks_other_faults(tmp_path, mutation):
    step, rec, episode, project = lsu_timeout_fixture(tmp_path)
    if mutation == 'learn': step['role'] = 'LEARN'
    elif mutation == 'other_task': step['task'] = 'C99999__other/initial'
    elif mutation == 'write': rec['write'] = 'WRITTEN'
    elif mutation == 'accounting': episode['accounting']['passed'] = False
    elif mutation == 'open_hold': episode['resources']['open_holds'] = 1
    elif mutation == 'wrong_rc': episode['final']['recovery_rc'] = 76
    elif mutation == 'accepted': episode['accepted'] = True
    (project / 'episode.json').write_text(json.dumps(episode))
    assert not L.known_lsu_probe_timeout(tmp_path, step, rec)


def test_dispatcher_pauses_when_the_first_learn_fails_and_never_reruns(tmp_path, monkeypatch):
    import t2a_batch as B
    steps = L.plan_steps(LEARN, PROBE[:1])[:4]                # 2 probes (c0), then LEARN 1..2
    reg = {'steps': steps, 'scheduler': {'cpu_slots': [[0, 1]]}, 'files': {}}
    monkeypatch.setattr(B, 'preflight', lambda root, split: reg)
    monkeypatch.setattr(L, 'e2e_check', lambda root, step, rec: {'ok': rec['status'] == 'OK', 'checks': {'write_ok': rec['status'] == 'OK'}})
    launched, outcome = [], {'learn': 'WRITE_FAILED'}

    def launcher(i):
        launched.append(i)
        status = outcome['learn'] if steps[i]['role'] == 'LEARN' else 'OK'
        (tmp_path / 'steps').mkdir(exist_ok=True)
        (tmp_path / 'steps' / f'{i:03d}.json').write_text(json.dumps({'status': status}))
        return Done()

    st = L.dispatch(tmp_path, launcher=launcher, poll_s=0)
    assert st['status'] == 'PAUSED' and 'end-to-end validation failed' in st['reason'] and launched == [0, 1, 2]
    (tmp_path / 'batch-paused.json').unlink()
    st = L.dispatch(tmp_path, launcher=launcher, poll_s=0)     # the failed LEARN record stays; never re-run
    assert launched == [0, 1, 2, 3]


def test_dispatcher_pauses_for_disk_safety_before_launching(tmp_path, monkeypatch):
    import shutil as _sh
    import t2a_batch as B
    steps = L.plan_steps(LEARN, PROBE[:1])[:2]
    monkeypatch.setattr(B, 'preflight', lambda root, split: {'steps': steps, 'scheduler': {'cpu_slots': [[0]]}, 'files': {}})
    monkeypatch.setattr(_sh, 'disk_usage', lambda p: type('U', (), {'free': 10 * 2 ** 30})())
    launched = []
    st = L.dispatch(tmp_path, launcher=lambda i: launched.append(i), poll_s=0)
    assert st['status'] == 'PAUSED' and 'safety' in st['reason'] and launched == []


def test_phase_gate_stops_before_any_expensive_episode(tmp_path, monkeypatch):
    import t2a_batch as B
    steps = L.plan_steps(LEARN, PROBE[:1])[:2]
    monkeypatch.setattr(B, 'preflight', lambda *a: {
        'steps': steps, 'scheduler': {'cpu_slots': [[0]]}, 'files': {}})
    monkeypatch.setattr(L, 'phase_admission', lambda *a: (False, 'unresolved UNKNOWN'))
    launched = []
    state = L.dispatch(tmp_path, launcher=lambda i: launched.append(i), poll_s=0)
    assert state['status'] == 'PAUSED' and state['next_step'] == 0
    assert 'unresolved UNKNOWN' in state['reason'] and launched == []
    assert not (tmp_path / 'episodes').exists()


def test_phase_gate_uses_frozen_ledger_and_preserves_actual_unknown(tmp_path):
    import importlib.util
    source = ROOT / 'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    target = tmp_path / 'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    budget = tmp_path / 'phase-budget.json'
    L.save(budget, {'budget_id': 'test', 'caps': {}, 'reserve': {}, 'halt_on_unknown_usage': True})
    ledger = tmp_path / 'phase/resource_ledger.jsonl'
    ledger.parent.mkdir()
    events = [
        {'type': 'budget_hold', 'hold_id': 'lost', 'needs': {'tokens_in': 8, 'tokens_out': 2}, 'ts': 1},
        {'type': 'budget_settle', 'hold_id': 'lost', 'booked': {'tokens_in': 8, 'tokens_out': 2},
         'actual': {'tokens_in': 'UNKNOWN', 'tokens_out': 'UNKNOWN'}, 'ts': 2}]
    ledger.write_text(''.join(json.dumps(e)+'\n' for e in events))
    reg = {'phase_binding': {'ledger_path': str(ledger), 'budget_file': str(budget),
                            'budget_sha256': L.sha(budget)}, 'files': {str(target): L.sha(target)}}
    assert L.phase_admission(tmp_path, reg)[0] is False
    grant = tmp_path / 'approval.json'
    L.save(grant, {'schema': 'r2g-unknown-occupancy-authorization-v1', 'user_approved': True,
                  'targets': [{'ledger_path': str(ledger.resolve()), 'hold_id': 'lost',
                               'upper_bound': {'tokens_in': 8, 'tokens_out': 2}}]})
    events.append({'type': 'budget_unknown_occupancy', 'hold_id': 'lost',
                   'upper_bound': {'tokens_in': 8, 'tokens_out': 2}, 'authorization': str(grant),
                   'authorization_sha256': L.sha(grant), 'ledger_path': str(ledger), 'ts': 3})
    ledger.write_text(''.join(json.dumps(e)+'\n' for e in events))
    assert L.phase_admission(tmp_path, reg)[0] is True
    assert '"actual": {"tokens_in": "UNKNOWN"' in ledger.read_text()
    with ledger.open('a') as f:
        f.write(json.dumps({'type': 'budget_hold', 'hold_id': 'open', 'needs': {}, 'ts': 4})+'\n')
    assert L.phase_admission(tmp_path, reg)[0] is False
    budget.write_text('{}')
    assert L.phase_admission(tmp_path, reg)[0] is False


def test_the_runtime_copy_needs_nothing_from_excluded_directories():
    """The package copies code without 'evaluation' directories; the write must read the split
    contract from the package root, never from the code tree."""
    src = (ROOT / 'memory/scripts/t2_lc.py').read_text()
    body = src[src.index('def commit_verified'):src.index('# ------------------------------------------------------------------------------------ prepare')]
    assert 'SPLIT_CONTRACT' not in body and 'root / "split-contract.json"' in body


def test_runtime_copy_keeps_tehm_evaluation_and_drops_only_top_level_records(tmp_path):
    import shutil
    repo = tmp_path / 'repo'
    for d in ('memory/evaluation', 'memory/tehm/evaluation', 'memory/tests', 'memory/scripts'):
        (repo / d).mkdir(parents=True)
        (repo / d / 'x.py').write_text('x = 1\n')
    shutil.copytree(repo / 'memory', tmp_path / 'out/memory', ignore=L.runtime_ignore(repo))
    assert (tmp_path / 'out/memory/tehm/evaluation/x.py').is_file()
    assert not (tmp_path / 'out/memory/evaluation').exists() and not (tmp_path / 'out/memory/tests').exists()
    assert (tmp_path / 'out/memory/scripts/x.py').is_file()
