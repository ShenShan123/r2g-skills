import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def test_lvs_checker_identity_invalidates_same_worker_cache(tmp_path):
    from t2_signoff_integrity import fingerprint
    tool=tmp_path/'checker';tool.write_text('version1')
    original=fingerprint(tmp_path,{})
    selected=fingerprint(tmp_path,{'R2G_LVS_KLAYOUT_CMD':str(tool),'R2G_LVS_KLAYOUT_SHA256':'one'})
    assert selected!=original
    assert selected!=fingerprint(tmp_path,{'R2G_LVS_KLAYOUT_CMD':str(tool),'R2G_LVS_KLAYOUT_SHA256':'two'})
    tool.write_text('version2')
    assert selected!=fingerprint(tmp_path,{'R2G_LVS_KLAYOUT_CMD':str(tool),'R2G_LVS_KLAYOUT_SHA256':'one'})

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import t2_signoff_integrity as S


def fixture(tmp_path):
    p=tmp_path/'project';(p/'constraints').mkdir(parents=True);(p/'reports').mkdir()
    r=p/'backend/RUN_0';(r/'results').mkdir(parents=True)
    for name in ('config.mk','constraint.sdc'):(p/'constraints'/name).write_text(name)
    (r/'results/6_final.gds').write_text('native layout')
    (p/'manifest.json').write_text('{}')
    return p,r


def test_timeout_is_unknown_not_a_measured_failure(tmp_path):
    p,r=fixture(tmp_path)
    (p/'reports/drc.json').write_text(json.dumps({'status':'stuck','exit_code':124,'run_tag':r.name,
        'reason':'klayout_polygon_op_no_progress','total_violations':None}))
    original={'accepted':False,'final_layout':True,'judged_run':r.name,'verification_stop':1,
              'acceptance':{'accepted':False,'checks':{'A2_drc_full_deck':'fail','A5_antenna':'fail'},
                            'failed':['A2_drc_full_deck','A5_antenna'],'unknown':[]}}
    result=S.classify(p,original)
    assert result['technical_signoff']['kind']=='CHECKER_TIMEOUT'
    assert result['acceptance']['checks']['A2_drc_full_deck']=='unknown'
    assert original['acceptance']['checks']['A2_drc_full_deck']=='fail'
    assert S.known_checker_stop(p,result)


def test_wrong_run_and_non_timeout_cannot_be_acknowledged_as_timeout(tmp_path):
    p,r=fixture(tmp_path)
    for raw in ({'status':'stuck','exit_code':124,'run_tag':'RUN_wrong'},
                {'status':'error','exit_code':1,'run_tag':r.name}):
        (p/'reports/drc.json').write_text(json.dumps(raw))
        result=S.classify(p,{'judged_run':r.name,'accepted':False,'final_layout':True})
        assert not S.known_checker_stop(p,result)


def test_measured_drc_violation_is_still_a_measured_repair_failure(tmp_path):
    p,r=fixture(tmp_path)
    (p/'reports/drc.json').write_text(json.dumps({'status':'fail','total_violations':3,'run_tag':r.name}))
    verdict={'accepted':False,'judged_run':r.name,'final_layout':True}
    assert not S.classify(p,verdict).get('technical_signoff')


def test_same_run_reuses_real_verification_but_not_after_config_layout_or_report_change(tmp_path):
    p,r=fixture(tmp_path);calls=[];events=[]
    def verify(*args):calls.append(1);return {'accepted':True,'judged_run':r.name,'final_layout':True}
    h=SimpleNamespace(final_verdict=verify,FINAL_SIGNOFF_TIMEOUT_S=9000,
                      _ledger=lambda *args:(events.append(args) or (0,'')))
    S.install(h)
    assert h.FINAL_SIGNOFF_TIMEOUT_S>=7200+28800+3600
    h.final_verdict(p,{},p/'log');h.final_verdict(p,{},p/'log')
    assert len(calls)==1
    (p/'constraints/config.mk').write_text('new configuration')
    h.final_verdict(p,{},p/'log');assert len(calls)==2
    (r/'results/6_final.gds').write_text('different layout')
    h.final_verdict(p,{},p/'log');assert len(calls)==3
    (p/'reports/drc.json').write_text('{}')
    h.final_verdict(p,{},p/'log');assert len(calls)==4


def test_guard_never_manufactures_a_verdict_from_preexisting_reports(tmp_path):
    p,r=fixture(tmp_path);calls=[]
    (p/'reports/acceptance.json').write_text('{"accepted":true}')
    h=SimpleNamespace(final_verdict=lambda *args:(calls.append(1) or {'accepted':False,'judged_run':r.name}),
                      FINAL_SIGNOFF_TIMEOUT_S=9000,_ledger=lambda *args:(0,''))
    S.install(h);assert h.final_verdict(p,{},p/'log')['accepted'] is False
    assert len(calls)==1


def test_allows_no_measured_failure_from_unbound_empty_report(tmp_path):
    p,r=fixture(tmp_path)
    (p/'reports/drc.json').write_text(json.dumps({'status':'fail','total_violations':None,'run_tag':r.name}))
    assert S.classify(p,{'accepted':False,'judged_run':r.name,'final_layout':True}).get('technical_signoff')


@pytest.mark.parametrize('status',['crash','stale','not_supported'])
def test_native_checker_non_verdict_cannot_reach_llm_as_repair_failure(tmp_path,status):
    p,r=fixture(tmp_path)
    (p/'reports/lvs.json').write_text(json.dumps({'status':status,'run_tag':r.name}))
    verdict=S.classify(p,{'accepted':False,'judged_run':r.name,'final_layout':True})
    assert verdict['technical_signoff']['kind']=='CHECKER_UNVERIFIED'
    assert not S.known_checker_stop(p,verdict)


@pytest.mark.parametrize('log_info',[{'log_status':'mismatch'},{'tool':'netgen','match':False}])
def test_native_reported_lvs_mismatch_is_measured_even_without_numeric_count(tmp_path,log_info):
    p,r=fixture(tmp_path)
    (p/'reports/lvs.json').write_text(json.dumps({'status':'failed','run_tag':r.name,
                                               'mismatch_count':None,'log_info':log_info}))
    verdict=S.classify(p,{'accepted':False,'judged_run':r.name,'final_layout':True})
    assert not verdict.get('technical_signoff')
