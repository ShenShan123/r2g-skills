import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sixcontrol',ROOT/'control.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)


def test_scope_has_four_lanes_and_excludes_both_rtlfixer_arms():
    assert len(c.LANES)==4
    assert all(h!='rtlfixer' for h,a in c.LANES)
    assert len(c.tasks('rtlscout'))==77
    assert len(c.tasks('swe-agent'))==20
    assert sum(len(c.tasks(h)) for h,a in c.LANES)==194


def fixture(tmp_path,monkeypatch,n=3,unknown=False,status='NOT_ACCEPTED'):
    monkeypatch.setattr(c,'ROOT',tmp_path)
    path=c.episode('rtlscout','tehm','fixture');(path/'provider').mkdir(parents=True)
    rows=[]
    for i in range(n):
        rows.extend([{'event':'request_started','request_local_id':str(i)},
                     {'event':'request_ended','request_local_id':str(i),'usage':'UNKNOWN' if unknown else {'total_tokens':5}}])
    (path/'provider/requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    c.record(path/'terminal-accounting.json',{'status':status,'cost':{'online_requests':n,
             'known_tokens':0 if unknown else 5*n,'actual_total_tokens':None if unknown else 5*n}})
    return path


def test_three_requests_are_auditable(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    assert c.audit_episode('rtlscout','tehm','fixture')['requests']==3


def test_four_requests_are_never_accepted(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch,4)
    with pytest.raises(ValueError,match='limit violated'):c.audit_episode('rtlscout','tehm','fixture')


def test_unknown_remains_incomplete_not_zero(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch,1,True,'ACCOUNTING_FAILURE')
    assert c.audit_episode('rtlscout','tehm','fixture')['unknown_or_open']


def test_unknown_not_scored_as_repair_failure(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch,1,True,'NOT_ACCEPTED')
    with pytest.raises(ValueError,match='measured outcome'):c.audit_episode('rtlscout','tehm','fixture')


def test_missing_terminal_blocks_continuation(tmp_path,monkeypatch):
    path=fixture(tmp_path,monkeypatch);(path/'terminal-accounting.json').unlink()
    with pytest.raises(ValueError,match='missing terminal'):c.audit_episode('rtlscout','tehm','fixture')


def test_freeze_rejects_runtime_tampering(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'ROOT',tmp_path)
    (tmp_path/'run.py').write_text('initial')
    c.record(tmp_path/'registration.json',{'launch_authorized':True,'files':{'run.py':c.sha(tmp_path/'run.py')},'package_registrations':{}})
    c.validate();(tmp_path/'run.py').write_text('changed')
    with pytest.raises(ValueError,match='freeze mismatch'):c.validate()
