"""Operator F2 gates must preserve spending and never grant failure/success from UNKNOWN."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH=Path(__file__).resolve().parents[1]/'evaluation/tools/t2a_formal_control.py'
spec=importlib.util.spec_from_file_location('formal_control',PATH)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


def fixture(root):
    root.mkdir();old=root.with_name(root.name+'-old');(old/'phase').mkdir(parents=True)
    bank=old/'phase/resource_ledger.jsonl';bank.write_text('{}\n')
    (root/'phase').symlink_to(old/'phase',target_is_directory=True)
    write(root/'registration.json',{'plan':[]});write(root/'phase-budget.json',{'caps':{'llm_requests':336}})
    dev=root.with_name(root.name+'-dev');write(dev/'dev-validation.json',{})
    write(dev/'completion-audit.json',{'passed':True})
    grant={'controller_sha256':C.digest(C.__file__),'registration_sha256':C.digest(root/'registration.json'),
       'original_ledger':str(bank),'prior_ledger_bytes':3,'prior_ledger_sha256':C.digest(bank),
       'budget_sha256':C.digest(root/'phase-budget.json'),'dev_root':str(dev),
       'dev_receipt_sha256':C.digest(dev/'dev-validation.json'),'dev_audit_sha256':C.digest(dev/'completion-audit.json')}
    write(root/'formal-control-registration.json',grant)
    return bank,grant


def test_appending_new_spending_is_allowed_but_erasing_old_spending_is_not(tmp_path):
    root=tmp_path/'new';bank,_=fixture(root)
    with bank.open('a') as out:out.write('{}\n')
    assert C.check_binding(root)
    bank.write_text('')
    with pytest.raises(RuntimeError,match='spending prefix'):C.check_binding(root)


def test_changing_the_bank_alias_or_allocation_is_not_authorized(tmp_path):
    root=tmp_path/'new';bank,grant=fixture(root)
    other=tmp_path/'other';other.mkdir();(other/'resource_ledger.jsonl').write_bytes(bank.read_bytes())
    (root/'phase').unlink();(root/'phase').symlink_to(other,target_is_directory=True)
    with pytest.raises(RuntimeError,match='Payment bank'):C.check_binding(root)
    (root/'phase').unlink();(root/'phase').symlink_to(bank.parent,target_is_directory=True)
    write(root/'phase-budget.json',{'caps':{'llm_requests':999}})
    with pytest.raises(RuntimeError,match='allocation'):C.check_binding(root)


def test_collector_preserves_all_denominators_and_unknown_pairs(tmp_path,monkeypatch):
    root=tmp_path/'new';_,grant=fixture(root)
    plan=[{'task':'d/v','arm':a,'repeat':1} for a in ('NB','TM0')]
    reg={'plan':plan,'arms':['NB','TM0'],'tasks':[{'task':'d/v'}],'repeats':1}
    write(root/'inventory.json',{'tasks':[{'task':'d/v','family':'source'}]})
    for row in plan:
        project=root/'episodes'/('d__v__'+row['arm']+'__r1')
        write(project/'episode.json',{**row,'split':'TEST','paper_data':True,
             'status':'UNVERIFIED_OR_SAFETY_STOP' if row['arm']=='NB' else 'ACCEPTED',
             'accepted':row['arm']=='TM0','final':{},'wall_s':1})
        write(project/'reports/resource_ledger.jsonl',{'type':'llm_request'})
    tokens={'measurement_complete':True,'llm_requests':1,'repair_requests':1,'auxiliary_requests':0,
            'input_tokens':10,'output_tokens':2,'known_input_tokens':10,'known_output_tokens':2}
    B=SimpleNamespace(preflight=lambda *a:reg,T=SimpleNamespace(
      terminal_status=lambda final:'ACCEPTED' if not getattr(B.T,'once',False) else 'UNVERIFIED_OR_SAFETY_STOP',
      accounting_check=lambda *a:{'passed':True,'flows':0},request_token_totals=lambda *a:tokens))
    status=iter(['UNVERIFIED_OR_SAFETY_STOP','ACCEPTED'])
    B.T.terminal_status=lambda *a:next(status)
    result=C.collect(root,B)
    assert result['registered']==2 and result['execution_complete'] and result['data_audit_passed']
    tm=result['by_arm']['TM0']
    assert tm['registered']==1 and tm['strict_successes']==1
    assert tm['paired_vs_NB']['G']==0 and tm['paired_vs_NB']['unverified_or_missing_pairs']==1
    assert tm['mean_total_tokens_all_registered']==12
    assert (root/'table2-matrix.csv').exists()


def test_completed_prior_must_be_hash_bound_and_not_an_active_batch(tmp_path,monkeypatch):
    root=tmp_path/'prior'
    write(root/'registration.json',{})
    write(root/'batch-complete.json',{'episodes':56})
    write(root/'formal-results.json',{'execution_complete':True,'data_audit_passed':True,
          'registered':56,'records':[{'status':'NOT_ACCEPTED'}]*56})
    prior={'root':str(root),'registration_sha256':C.digest(root/'registration.json'),
           'completion_sha256':C.digest(root/'batch-complete.json'),
           'results_sha256':C.digest(root/'formal-results.json'),
           'supervisor':{'pid':123,'start_ticks':'456'}}
    monkeypatch.setattr(C,'identity',lambda pid:None)
    assert C.verify_completed_prior({'completed_prior':prior})
    monkeypatch.setattr(C,'identity',lambda pid:{'pid':pid,'start_ticks':'456','state':'S'})
    with pytest.raises(RuntimeError,match='remains active'):
        C.verify_completed_prior({'completed_prior':prior})
    monkeypatch.setattr(C,'identity',lambda pid:None)
    write(root/'formal-results.json',{'execution_complete':False})
    with pytest.raises(RuntimeError,match='evidence changed'):
        C.verify_completed_prior({'completed_prior':prior})
