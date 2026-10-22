"""Selection integrates the frozen DEV profile without bypassing the catalogue."""
import json
import sys
from types import SimpleNamespace

import pytest
import diagnose_signoff_fix as D


@pytest.mark.parametrize('evidence',['none','matched','wrong_clock'])
def test_priority_order_is_earned_from_full_task_evidence(tmp_path,monkeypatch,capsys,evidence):
    memory=__import__('pathlib').Path(__file__).resolve().parents[3]/'memory'
    sys.path.insert(0,str(memory/'scripts'))
    from t2a_mechanisms import quality_context,SCHEMA
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True);(project/'reports').mkdir()
    (project/'constraints/config.mk').write_text('export CORE_UTILIZATION = 60\nexport PLATFORM = sky130hd\n')
    task={'platform':'sky130hd','expected_clock_period_ns':10,'initial_config':{'CORE_UTILIZATION':'60','PLATFORM':'sky130hd'},
          'allowlist':{'CORE_UTILIZATION':{'type':'number','min':10,'max':90}},'immutable':{},'area':{}}
    manifest=project/'manifest.json';manifest.write_text(json.dumps(task))
    cat={'id':'catalogue','auto_apply':True,'config_edits':{'CORE_UTILIZATION':'52'},'rerun_from':'floorplan','recheck':'drc'}
    mem={**cat,'id':'tehm_m','rule_id':'r','config_edits':{'CORE_UTILIZATION':'45'}}
    monkeypatch.setattr(D,'build_plan',lambda *a,**k:{'check':'drc','status':'fail','strategies':[cat]})
    monkeypatch.setattr(D,'_current_situation',lambda *a:{'check':'drc'})
    monkeypatch.setitem(sys.modules,'runtime_router',SimpleNamespace(signoff_strategies=lambda **kw:[mem],signoff_vetoes=lambda **kw:{}))
    row={'rule_id':'r','situation':{'check':'drc'},'sample_id':'DEV1','split':'DEV','rolled_back':False,
         'quality_context':quality_context({**task,'expected_clock_period_ns':20 if evidence=='wrong_clock' else 10}),
         'memory_status':'ACCEPTED','baseline_status':'NOT_ACCEPTED',
         'costs':{a:{'flows':3,'tokens':10,'wall_s':30} for a in ('memory','baseline')}}
    policy=tmp_path/'priority.json';policy.write_text(json.dumps({'schema':SCHEMA,'task_entry_fallback':False,
          'cost_priority':True,'priority_samples':[] if evidence=='none' else [row]}))
    monkeypatch.setenv('R2G_MEMORY_BACKEND','tehm');monkeypatch.delenv('R2G_RECIPE_FILE',raising=False)
    monkeypatch.setenv('R2G_TASK_MANIFEST',str(manifest));monkeypatch.setenv('R2G_TEHM_PRIORITY_FILE',str(policy))
    assert D.main([str(project),'--check','drc','--list'])==0
    plan=json.loads(capsys.readouterr().out)
    assert [s['id'] for s in plan['strategies']]==(['tehm_m','catalogue'] if evidence=='matched' else ['catalogue','tehm_m'])
