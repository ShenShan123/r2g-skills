import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import dispatch as D


def raw():
    return {'status':'NOT_ACCEPTED','accounting':{'passed':True,'errors':[],'llm_requests':0},
            'agent_run':{'local_memory_attempts':0},'token_usage':{'unknown_usage_requests':0}}


def test_episode_namespace_uses_variant_not_internal_tm0():
    paths=[D.episode_path({'root':'/tmp/job','task':'same/task','variant':v}) for v in D.E.VARIANTS]
    assert len(set(paths))==3


def test_recipe_zero_calls_is_valid():
    assert D.audit_episode(raw(),[],'RECIPE')['requests']==0


@pytest.mark.parametrize('n',[1,3,4])
def test_recipe_never_gets_an_online_request(n):
    r=raw();r['accounting']['llm_requests']=n
    with pytest.raises(ValueError):D.audit_episode(r,[{'type':'llm_request'}]*n,'RECIPE')


def test_three_is_allowed_and_four_is_not():
    r=raw();r['accounting']['llm_requests']=3
    assert D.audit_episode(r,[{'type':'llm_request'}]*3,'TEHM_FIXED')['requests']==3
    r['accounting']['llm_requests']=4
    with pytest.raises(ValueError):D.audit_episode(r,[{'type':'llm_request'}]*4,'TEHM_FIXED')


def test_unknown_transport_is_retained_and_skip_does_not_relabel_usage():
    r=raw();r['status']='ACCOUNTING_FAILURE';r['accounting'].update(passed=False,
        errors=['provider identity/input/usage violation'],llm_requests=1)
    r['token_usage']['unknown_usage_requests']=1
    before=copy.deepcopy(r)
    assert D.audit_episode(r,[{'type':'llm_request','model_reported':None}],'TEHM_FIXED')['unknown_usage_requests']==1
    assert r==before


def test_unknown_does_not_mask_structural_accounting_or_identity_fault():
    r=raw();r['accounting'].update(passed=False,errors=['flow/run/charge correspondence'])
    r['token_usage']['unknown_usage_requests']=1
    with pytest.raises(ValueError):D.audit_episode(r,[],'TEHM_SELECTOR')
    r=raw();r['accounting']['llm_requests']=1
    with pytest.raises(ValueError):D.audit_episode(r,[{'type':'llm_request','model_reported':'wrong-model'}],'TEHM_FIXED')


def test_local_attempts_use_ledger_even_when_agent_metadata_missing():
    r=raw();r.pop('agent_run')
    with pytest.raises(ValueError):D.audit_episode(r,[{'type':'figure2_local_attempt'}]*4,'TEHM_FIXED')


def test_actual_frozen_plan_is_pending_only_and_disjoint():
    plan=D.E.load(D.REVISION/'plan.json')
    assert len(plan['jobs'])==126
    assert D.validate_namespaces(plan)
    assert all(not (Path(j['root'])/'episodes').exists() for j in plan['jobs'])
