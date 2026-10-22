"""Repeated known-failing execution is not another EDA opportunity; turns still count."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import t2a_runtime as T
from test_repair_turns import H, OK, _Resp, _spent, ep
from test_t2a_runtime import manifest


@pytest.mark.parametrize('arm', T.ARMS)
@pytest.mark.parametrize('evidence', ['known', 'unknown', 'unbound'])
def test_identical_failed_candidate_is_not_reexecuted_but_spends_all_turns(ep, monkeypatch, arm, evidence):
    project, _, _ = ep
    env = manifest(project)
    (project / 'constraints/constraint.sdc').write_text('set clk_period 10\n')
    (project / 'backend').mkdir()
    monkeypatch.setattr(H, '_task_failure', lambda p: ('setup', 'timing', 1))
    monkeypatch.setattr(H, 'observation', lambda p: {'timing_tier': 'minor'})
    monkeypatch.setattr(T, 'recover_execution', lambda *a, **kw: None)
    import result_vector as V
    monkeypatch.setattr(V, 'capture', lambda *a, **k: {
        'layout': {'def_digest': 'a' * 64} if evidence != 'unbound' else {}, 'signals': {}})
    checks = {key: 'pass' for key in T.ACCEPTANCE_CHECKS}
    verdict = {'accepted': False, 'final_layout': True,
        'acceptance': {'accepted': True, 'checks': checks, 'unknown': [], 'failed': []},
        'envelope': {'status': 'FAIL', 'config': {'ok': True},
                     'area': {'ok': False, 'unverified': [], 'violations': [
                         {'kind': 'area_cap_exceeded', 'metric': 'core', 'relative_change': 1.2, 'cap': 1}]}}}
    if evidence == 'unknown':
        verdict['acceptance'].update(accepted=False, unknown=['A6_setup'])
        verdict['acceptance']['checks']['A6_setup'] = 'unknown'
    monkeypatch.setattr(H, 'final_verdict', lambda *a: copy.deepcopy(verdict))
    runs = []
    def run(cmd, *args):
        if str(cmd[1]).endswith('run_orfs.sh'): runs.append(cmd)
        return 0
    monkeypatch.setattr(H, 'run', run)
    def transport(req, **kwargs):
        messages = json.loads(req.data)['messages']
        response = {'keywords': ['timing']} if 'Only extract query keywords' in messages[0]['content'] else {
            'set': {'CORE_UTILIZATION': '40'}, 'unset': [], 'rerun_from': 'floorplan', 'rationale': 'same'}
        return _Resp(json.dumps({**OK, 'choices': [{'message': {'content': json.dumps(response)}}]}).encode())
    monkeypatch.setattr(H.urllib.request, 'urlopen', transport)
    class Memory:
        def context(self, query, sit, auxiliary=None, **kw):
            if arm == 'AMEM': auxiliary(query)
            return 'frozen SOURCE', ['unit']
    rows, _ = T.strict_turns(H, project, arm, env, 'unused',
        SimpleNamespace(from_project=lambda *a: {}), Memory() if arm in ('RET', 'EXPEL', 'AWM', 'AMEM') else None)
    if evidence == 'known':
        assert len(runs) == 1
        assert [row['status'] for row in rows] == ['FAILED', 'DUPLICATE_NOT_EXECUTED', 'DUPLICATE_NOT_EXECUTED']
        assert all(not row['flow_launched'] for row in rows[1:])
        assert all(row['duplicate_of_turn'] == 1 for row in rows[1:])
    else:
        assert len(runs) == 3
        assert [row['status'] for row in rows] == [('UNVERIFIED' if evidence == 'unknown' else 'FAILED')] * 3
    assert _spent(project, 'repair_attempts') == 3
    assert _spent(project, 'llm_requests') == (6 if arm == 'AMEM' else 3)
    assert T.request_token_totals([json.loads(line) for line in (
        project / 'reports/resource_ledger.jsonl').read_text().splitlines()])['total_tokens'] == (420 if arm == 'AMEM' else 210)


def test_duplicate_identity_requires_bound_layout_and_distinguishes_inputs(tmp_path):
    (tmp_path / 'constraints').mkdir()
    config = tmp_path / 'constraints/config.mk'
    sdc = tmp_path / 'constraints/constraint.sdc'
    config.write_text('export CORE_UTILIZATION = 40\n')
    sdc.write_text('set clk_period 10\n')
    baseline = tmp_path / 'baseline'
    (baseline / 'constraints').mkdir(parents=True)
    (baseline / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\n')
    (baseline / 'constraints/constraint.sdc').write_text('set clk_period 10\n')
    pre = {'layout': {'def_digest': 'a' * 64}}
    manifest = {'task': 'DEV', 'immutable': {}}
    identity = T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', pre)
    assert identity
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'route', pre) != identity
    assert T.candidate_execution_identity({**manifest, 'task': 'other'}, baseline, tmp_path, 'floorplan', pre) != identity
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', {'layout': {'def_digest': 'b' * 64}}) != identity
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', {}) is None
    config.write_text('export CORE_UTILIZATION = 45\n')
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', pre) != identity
    config.write_text('export CORE_UTILIZATION = 40\n')
    sdc.write_text('set clk_period 11\n')
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', pre) != identity
    sdc.write_text('set clk_period 10\n')
    (baseline / 'constraints/config.mk').write_text('export CORE_UTILIZATION = 61\n')
    assert T.candidate_execution_identity(manifest, baseline, tmp_path, 'floorplan', pre) != identity
