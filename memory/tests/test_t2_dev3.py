"""Small-DEV package helpers (no EDA, no model): slot choice, history binding, dispatcher gating."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'memory' / 'scripts'))
sys.path.insert(0, str(ROOT / 'memory'))
import t2_dev3 as V  # noqa: E402
import t2a_batch as B  # noqa: E402


def test_cpu_slots_are_disjoint_siblings_pairs():
    slots = V.pick_slots(3)
    assert len(slots) == 3 and all(len(s) == 16 for s in slots)
    flat = [c for s in slots for c in s]
    assert len(flat) == len(set(flat))
    for s in slots:
        lo = s[:8]
        assert s[8:] == [c + 96 for c in lo]


def make_package(tmp_path, units='U'):
    pkg = tmp_path / 'pkg'
    (pkg / 'tehm-import').mkdir(parents=True)
    (pkg / 'shared_units.jsonl').write_text('{}\n')
    digest = V.sha(pkg / 'shared_units.jsonl')
    (pkg / 'manifest.json').write_text(json.dumps({'view': 'dev_verification', 'units_sha256': digest,
                                                   'admitted': 6, 'units': 1}))
    (pkg / 'tehm-import/source_manifest.json').write_text(json.dumps({'export_sha256': 'imp'}))
    store = tmp_path / 'store'
    store.mkdir()
    (store / 'store_receipt.json').write_text(json.dumps({'source_export_sha256': 'imp', 'd1': True, 'caliber': 'main',
                                                         'rules': 3, 'admitted_trial_main': [1]}))
    recipe = tmp_path / 'recipe.json'
    recipe.write_text(json.dumps({'status': 'FROZEN', 'units_sha256': digest, 'recipes': []}))
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'package': {'units_sha256': digest}, 'entries': []}))
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'schema': 'tehm-v2-policy-v1', 'mechanisms': {}, 'index': str(index),
                                  'index_sha256': V.sha(index)}))
    return pkg, store, recipe, policy, digest


def test_history_binding_accepts_a_consistent_package(tmp_path):
    pkg, store, recipe, policy, digest = make_package(tmp_path)
    assert V.check_history(pkg, store, recipe, policy)['units_sha256'] == digest


@pytest.mark.parametrize('break_it', ['store', 'recipe', 'index', 'view'])
def test_history_binding_fails_closed_on_any_foreign_artifact(tmp_path, break_it):
    pkg, store, recipe, policy, digest = make_package(tmp_path)
    if break_it == 'store':
        (store / 'store_receipt.json').write_text(json.dumps({'source_export_sha256': 'other', 'd1': True, 'caliber': 'main'}))
    elif break_it == 'recipe':
        recipe.write_text(json.dumps({'status': 'FROZEN', 'units_sha256': 'other', 'recipes': []}))
    elif break_it == 'index':
        idx = json.loads(Path(json.loads(policy.read_text())['index']).read_text())
        idx['package']['units_sha256'] = 'other'
        Path(json.loads(policy.read_text())['index']).write_text(json.dumps(idx))
        pol = json.loads(policy.read_text())
        pol['index_sha256'] = V.sha(pol['index'])
        policy.write_text(json.dumps(pol))
    else:
        m = json.loads((pkg / 'manifest.json').read_text())
        m['view'] = 't2'
        (pkg / 'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError):
        V.check_history(pkg, store, recipe, policy)


ROW = {'task': 'chacha_core/u72', 'arm': 'NB', 'repeat': 1}


def write_episode(root, row, **over):
    d = V.episode_dir(root, row)
    (d / 'reports').mkdir(parents=True)
    rec = {'status': 'NOT_ACCEPTED', 'accounting': {'passed': True}, 'token_usage': {'measurement_complete': True}, **over}
    (d / 'episode.json').write_text(json.dumps(rec))
    return d


def test_wave_gate_flags_technical_problems_only(tmp_path):
    write_episode(tmp_path, ROW)
    ok = V.wave_gate(tmp_path, [ROW])
    assert ok in ([], ['chacha_core__u72__NB__r1: chain: ' + m for m in []])      # no attempts is not a problem
    write_episode(tmp_path, {**ROW, 'arm': 'TM0'}, status='ACCOUNTING_FAILURE', accounting={'passed': False})
    bad = V.wave_gate(tmp_path, [{**ROW, 'arm': 'TM0'}])
    assert any('ACCOUNTING_FAILURE' in b for b in bad) and any('accounting did not pass' in b for b in bad)
    assert V.wave_gate(tmp_path, [{**ROW, 'arm': 'RECIPE'}])[0].endswith('episode.json missing or unreadable')
    write_episode(tmp_path, {**ROW, 'repeat': 2}, token_usage={'llm_requests': 2, 'measurement_complete': False})
    assert any('token measurement incomplete' in b for b in V.wave_gate(tmp_path, [{**ROW, 'repeat': 2}]))
    write_episode(tmp_path, {**ROW, 'repeat': 3}, token_usage={'llm_requests': 0, 'measurement_complete': False})
    assert not any('token' in b for b in V.wave_gate(tmp_path, [{**ROW, 'repeat': 3}]))


def test_unknown_flow_amounts_are_never_counted_as_zero(tmp_path):
    d = write_episode(tmp_path, ROW)
    (d / 'reports/resource_ledger.jsonl').write_text(
        json.dumps({'type': 'budget_charge', 'resource': 'flows', 'amount': 2}) + '\n')
    assert V.flows_charged(tmp_path, [ROW]) == 2
    (d / 'reports/resource_ledger.jsonl').write_text(
        json.dumps({'type': 'budget_charge', 'resource': 'flows', 'amount': 'UNKNOWN'}) + '\n')
    with pytest.raises(RuntimeError):
        V.flows_charged(tmp_path, [ROW])


class Done:
    returncode = 0

    def poll(self):
        return 0


def fake_reg(slots=3):
    plan = [{'task': 'chacha_core/u72', 'arm': a, 'repeat': r} for r in (1, 2) for a in ('NB', 'RECIPE', 'TM0')]
    return {'plan': plan, 'scheduler': {'cpu_slots': [[0, 1], [2, 3], [4, 5]][:slots]}}


def test_dispatcher_pauses_at_the_first_wave_gate_and_never_reruns_an_episode(tmp_path, monkeypatch):
    reg = fake_reg()
    monkeypatch.setattr(B, 'preflight', lambda root, split: reg)
    monkeypatch.setattr(B, 'validate_cpu_slots', lambda *a, **k: None)
    launched = []
    broken = {'on': True}

    def launcher(row, slot, log):
        launched.append((row['arm'], row['repeat']))
        write_episode(tmp_path, row, **({'status': 'ACCOUNTING_FAILURE', 'accounting': {'passed': False}}
                                        if broken['on'] and row['arm'] == 'TM0' else {}))
        return Done()

    st = V.dispatch(tmp_path, launcher=launcher, poll_s=0)
    assert st['status'] == 'PAUSED' and st['next_wave'] == 1 and (tmp_path / 'batch-paused.json').is_file()
    assert sorted(launched) == [('NB', 1), ('RECIPE', 1), ('TM0', 1)]
    broken['on'] = False
    st = V.dispatch(tmp_path, launcher=launcher, poll_s=0)         # the operator continues after inspecting
    assert st['status'] == 'COMPLETED'
    assert sorted(launched) == sorted([(a, r) for r in (1, 2) for a in ('NB', 'RECIPE', 'TM0')])   # wave 1 not re-run


def test_dispatcher_refuses_a_partial_wave_and_honours_the_flow_cap(tmp_path, monkeypatch):
    reg = fake_reg()
    monkeypatch.setattr(B, 'preflight', lambda root, split: reg)
    monkeypatch.setattr(B, 'validate_cpu_slots', lambda *a, **k: None)
    write_episode(tmp_path, reg['plan'][0])                          # one episode of wave 0 exists
    with pytest.raises(RuntimeError, match='partial wave'):
        V.dispatch(tmp_path, launcher=lambda *a: Done(), poll_s=0)
    root2 = tmp_path / 'b'
    d = write_episode(root2, reg['plan'][0])
    (d / 'reports/resource_ledger.jsonl').write_text(
        json.dumps({'type': 'budget_charge', 'resource': 'flows', 'amount': 5}) + '\n')
    for row in reg['plan'][1:3]:
        write_episode(root2, row)
    st = V.dispatch(root2, launcher=lambda *a: Done(), flow_cap=5, poll_s=0)
    assert st['status'] == 'PAUSED' and 'flow cap' in st['reason']
