import importlib.util
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/'r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
spec=importlib.util.spec_from_file_location('t2a_effective',PATH)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
import t2a_envelope as E


def test_real_make_expands_effective_values_without_executing_eda(tmp_path,monkeypatch):
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True)
    cfg=project/'constraints/config.mk';cfg.write_text('export CORE_UTILIZATION = 80\n')
    flow=tmp_path/'flow';flow.mkdir()
    (flow/'Makefile').write_text('include $(DESIGN_CONFIG)\nPLACE_DENSITY = 0.66\nall:\n\t@false\n')
    backend=project/'backend/RUN_test';backend.mkdir(parents=True)
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'initial_config':{'CORE_UTILIZATION':'80'},'immutable':{},
                                   'allowlist':{'CORE_UTILIZATION':{'type':'number','min':30,'max':90}}}))
    monkeypatch.setenv('R2G_TASK_MANIFEST',str(manifest))
    assert C.capture(project,backend,flow,cfg,'test')==0
    result=json.loads((backend/'effective-config.json').read_text())
    assert result['status']=='PASS' and result['effective']['CORE_UTILIZATION']['value']=='80'
    assert result['effective']['PLACE_DENSITY']['value']=='0.66'
    (flow/'Makefile').write_text('include $(DESIGN_CONFIG)\noverride CORE_UTILIZATION = 10\n')
    assert C.capture(project,backend,flow,cfg,'test')==76
    result=json.loads((backend/'effective-config.json').read_text())
    assert result['errors'][0]['kind']=='execution_config_mismatch'


def test_formal_envelope_never_accepts_missing_execution_receipt(tmp_path):
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True)
    (project/'constraints/config.mk').write_text('export CORE_UTILIZATION = 80\n')
    manifest={'task':'dev','require_execution_binding':True,'initial_config':{'CORE_UTILIZATION':'80'}}
    result=E.envelope(manifest,project,None)
    assert result['status']=='UNVERIFIED' and 'binding' in result['reason']


def test_execution_receipt_is_bound_to_this_manifest_not_only_config(tmp_path):
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True)
    cfg=project/'constraints/config.mk';cfg.write_text('export CORE_UTILIZATION = 80\n')
    run=project/'backend/RUN_test';(run/'logs').mkdir(parents=True)
    (run/'logs/6_report.json').write_text(json.dumps({k:100 for k in E.AREA_KEYS.values()}))
    (run/'stage_log.jsonl').write_text(json.dumps({'stage':'finish','status':0})+'\n')
    man={'task':'dev','require_execution_binding':True,'initial_config':{'CORE_UTILIZATION':'80'},
         'area':{k:{'ref':100,'max_rel_increase':1} for k in E.AREA_KEYS}}
    path=tmp_path/'manifest.json';path.write_text(json.dumps(man))
    receipt={'status':'PASS','run_dir':str(run),'project_config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),
             'manifest_sha256':'other-manifest'}
    binding=run/'effective-config.json';binding.write_text(json.dumps(receipt))
    env={'R2G_TASK_MANIFEST':str(path)}
    assert E.envelope(man,project,run,env)['status']=='UNVERIFIED'
    receipt['manifest_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();binding.write_text(json.dumps(receipt))
    assert E.envelope(man,project,run,env)['status']=='PASS'
    assert E.envelope({**man,'task':'other'},project,run,env)['status']=='UNVERIFIED'


def test_post_query_fast_overrides_are_not_certified(tmp_path):
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True)
    cfg=project/'constraints/config.mk';cfg.write_text('export CORE_UTILIZATION = 80\n')
    values={'CORE_UTILIZATION':{'origin':'file','value':'80'}}
    errors=C.verify({'initial_config':{'CORE_UTILIZATION':'80'}},project,cfg,values,{'PLACE_FAST':'1'})
    assert {'kind':'unbound_fast_override','knob':'PLACE_FAST'} in errors
