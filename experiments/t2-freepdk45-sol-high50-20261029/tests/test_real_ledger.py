"""Real resource-ledger subprocesses with a fake provider: zero paid calls."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]
import sol_install as I
import sol_provider as P


def test_actual_ledger_enforces_three_and_preserves_reasoning_usage(tmp_path,monkeypatch):
    import r2g_memory_eval as H
    import t2a_runtime as T
    project=tmp_path/'episode';(project/'reports').mkdir(parents=True)
    phase=tmp_path/'phase';phase.mkdir()
    budget=tmp_path/'episode-budget.json';budget.write_text(json.dumps({
        'budget_id':'integration','caps':{'llm_requests':3,'repair_attempts':3},'reserve':{}}))
    phase_budget=tmp_path/'phase-budget.json';phase_budget.write_text(json.dumps({
        'budget_id':'phase-integration','caps':{},'reserve':{},'halt_on_unknown_usage':False}))
    monkeypatch.setattr(H,'SKILL',ROOT/'code/r2g-skills/signoff-loop')
    monkeypatch.setenv('R2G_BUDGET_FILE',str(budget));monkeypatch.setenv('R2G_REQUIRE_LLM_BUDGET','1')
    monkeypatch.setenv('R2G_PHASE_LEDGER',str(phase/'resource_ledger.jsonl'))
    monkeypatch.setenv('R2G_PHASE_BUDGET_FILE',str(phase_budget))
    monkeypatch.delenv('R2G_LEDGER_PATH',raising=False)
    monkeypatch.setattr(P,'credentials',lambda *a:('fixture','https://api.openai.com/v1/responses'))
    monkeypatch.setattr(P,'ROOT',tmp_path)
    calls=[]
    response={'model':P.MODEL,'reasoning':{'effort':'high'},'status':'completed',
        'output':[{'type':'message','content':[{'type':'output_text','text':'{"operation":"stop","arguments":{"reason":"fixture"}}'}]}],
        'usage':{'input_tokens':10,'output_tokens':30,'total_tokens':40,'output_tokens_details':{'reasoning_tokens':25}}}
    def fake(body,*args,**kw):calls.append(body);return response,{'http_status':200,'wall_s':0.1}
    monkeypatch.setattr(P,'transport',fake)
    for i in range(3):
        content,usage=I.governed_call(H,'fixture',[],project/('request-'+str(i)),ledger=project)
        assert content and usage['repair_turn_id']
    assert I.governed_call(H,'fixture',[],project/'request-4',ledger=project)[0] is None
    assert len(calls)==3
    events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
    assert T.accounting_check(events,[])['passed'] is True
    assert T.request_token_totals(events)['total_tokens']==120
    rc,raw=H._ledger(project,'summary','--json');summary=json.loads(raw)
    assert rc==0 and summary['open_holds']==0
    assert summary['spent']['llm_requests']==3
    assert summary['spent']['tokens_in']==30 and summary['spent']['tokens_out']==90
