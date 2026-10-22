"""Offline tests against the real ledger/provider boundary (fake HTTP only)."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_repair_turns import ep, H, OK, _Resp, _spent
import t2_agent_dispatch as D


def gateway(ep):
    project, calls, respond = ep
    Path(os.environ['R2G_BUDGET_FILE']).write_text(json.dumps(D.episode_budget()))
    return D.OnlineGateway(H, project, 'unused'), project, calls, respond


def call(g, project, i, purpose='repair'):
    return g.call([{'role':'user','content':'offline boundary test'}], project/f'call-{i}',
                  purpose=purpose, candidate_generating=purpose=='repair')


@pytest.mark.parametrize('roles', [('repair',)*3, ('query','repair','judge'), ('query',)*3])
def test_all_online_purposes_share_three_and_fourth_never_reaches_transport(ep, roles):
    g,p,calls,_=gateway(ep)
    for i,role in enumerate(roles):
        content,usage=call(g,p,i,role)
        assert content
        assert ('repair_turn_id' in usage)==(role=='repair')
    with pytest.raises(D.OnlineAllowanceSpent): call(g,p,4)
    assert len(calls)==3 and _spent(p,'llm_requests')==3
    assert _spent(p,'tokens_in')==150 and _spent(p,'tokens_out')==60


def test_another_gateway_cannot_reset_the_episode_allowance(ep):
    g,p,calls,_=gateway(ep)
    for i in range(3):call(g,p,i,'query')
    other=D.OnlineGateway(H,p,'unused')
    with pytest.raises(D.OnlineAllowanceSpent):call(other,p,4)
    assert len(calls)==3


def test_unknown_usage_stops_without_refunding_or_retry(ep):
    g,p,calls,respond=gateway(ep)
    respond({k:v for k,v in OK.items() if k!='usage'})
    with pytest.raises(D.IntegrityStop):call(g,p,1)
    with pytest.raises(D.IntegrityStop):call(g,p,2)
    assert len(calls)==1 and _spent(p,'llm_requests')==1


def test_malformed_candidate_is_still_a_spent_online_call(ep):
    g,p,calls,respond=gateway(ep)
    respond({**OK,'choices':[{'message':{'content':'malformed'}}]})
    for i in range(3):assert call(g,p,i)[0]=='malformed'
    with pytest.raises(D.OnlineAllowanceSpent):call(g,p,4)
    assert len(calls)==3


def test_request_receipts_cannot_be_overwritten(ep):
    g,p,calls,_=gateway(ep)
    call(g,p,1)
    with pytest.raises(D.IntegrityStop):call(g,p,1)
    assert len(calls)==1


def test_no_silent_switch_back_to_six_amem_calls(ep):
    g,p,_,_=gateway(ep)
    budget=Path(os.environ['R2G_BUDGET_FILE'])
    value=D.episode_budget();value['caps']['llm_requests']=6
    budget.write_text(json.dumps(value))
    with pytest.raises(D.IntegrityStop):call(g,p,1)
    with pytest.raises(D.IntegrityStop):D.OnlineGateway(H,p,'unused')


def test_dispatch_has_no_compulsory_catalogue_phase_and_checks_actual_acceptance():
    calls=[];events=[]
    def choose(observation,gateway):
        calls.append('policy')
        return D.Tool('chosen_memory_action',{})
    def tool():calls.append('memory');return {'accepted':True}
    def final():calls.append('verify');return {'accepted':True}
    result=D.dispatch(SimpleNamespace(next_action=choose),
        {'chosen_memory_action':tool,'catalogue':lambda:pytest.fail('not selected')},
        {'accepted':False},None,final,lambda *args:events.append(args))
    assert calls==['policy','memory','verify'] and result['final']['accepted']
    assert [x[0] for x in events]==['agent_tool_selected','agent_tool_outcome']


def test_allowance_exhaustion_still_requires_final_verification():
    def choose(*args):raise D.OnlineAllowanceSpent()
    result=D.dispatch(SimpleNamespace(next_action=choose),{}, {'accepted':False},None,
                      lambda:{'accepted':False,'unknown':True},lambda *args:None)
    assert result['stop_reason']=='online_allowance_spent'
    assert result['final']['unknown'] # not converted into measured NOT_ACCEPTED


def test_initially_accepted_task_makes_no_policy_request():
    result=D.dispatch(SimpleNamespace(next_action=lambda *a:pytest.fail('no request')),
        {},{'accepted':True},None,lambda:{'accepted':True},lambda *args:None)
    assert result['final']['accepted']
