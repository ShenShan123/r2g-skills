import importlib.util
import hashlib
import json
import sys
import pytest
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/'r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
spec=importlib.util.spec_from_file_location('t2a_effective',PATH)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
import t2a_envelope as E


def test_allowed_knob_survives_platform_assignment_in_native_make(tmp_path, monkeypatch):
    project=tmp_path/'project';(project/'constraints').mkdir(parents=True)
    cfg=project/'constraints/config.mk';cfg.write_text('export MAX_ROUTING_LAYER = metal3\n')
    flow=tmp_path/'flow';flow.mkdir()
    (flow/'Makefile').write_text('include $(DESIGN_CONFIG)\nexport MAX_ROUTING_LAYER = metal10\n')
    backend=project/'backend/RUN_test';backend.mkdir(parents=True)
    manifest={'initial_config':{},'immutable':{},'allowlist':{
        'MAX_ROUTING_LAYER':{'type':'enum','values':['metal3','metal10']}}}
    path=project/'manifest.json';path.write_text(json.dumps(manifest))
    monkeypatch.setenv('R2G_TASK_MANIFEST',str(path))
    assert C.capture(project,backend,flow,cfg,'test')==0
    receipt=json.loads((backend/'effective-config.json').read_text())
    assert receipt['effective']['MAX_ROUTING_LAYER']=={'origin':'command line','value':'metal3'}
    assert 'MAX_ROUTING_LAYER=metal3' in receipt['command']
    assert C.make_arguments(manifest,project,{})==['MAX_ROUTING_LAYER=metal3']


@pytest.mark.parametrize('config,env',[
    ('export MAX_ROUTING_LAYER = bad_layer\n',{}),
    ('export MAX_ROUTING_LAYER = metal3\n',{'MAX_ROUTING_LAYER':'metal3'}),
    ('export MAX_ROUTING_LAYER = metal3\nexport CLOCK_PERIOD = 1\n',{}),
])
def test_make_arguments_do_not_bypass_contract(tmp_path,config,env):
    (tmp_path/'constraints').mkdir();(tmp_path/'constraints/config.mk').write_text(config)
    manifest={'initial_config':{},'immutable':{'knobs':['CLOCK_PERIOD']},
              'allowlist':{'MAX_ROUTING_LAYER':{'type':'enum','values':['metal3']}}}
    with pytest.raises(ValueError):C.make_arguments(manifest,tmp_path,env)


def test_same_make_arguments_are_used_by_flow_and_lvs():
    for name in ('run_orfs.sh','run_lvs.sh'):
        text=(PATH.parent/name).read_text()
        assert '_formal_make_args.sh' in text
        assert 'r2g_formal_make_args "$PROJECT_DIR"' in text
        assert 'R2G_FORMAL_MAKE_ARGS[@]' in text


def test_shell_bridge_preserves_rectangle_as_one_argument_and_reports_failure(tmp_path):
    (tmp_path/'constraints').mkdir()
    cfg=tmp_path/'constraints/config.mk';cfg.write_text('export DIE_AREA = 0 0 40 40\n')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'initial_config':{},'allowlist':{'DIE_AREA':{'type':'rectangle'}}}))
    env={**os.environ,'R2G_TASK_MANIFEST':str(manifest)}
    env.pop('DIE_AREA',None)
    cmd=['bash','-c','source "$1"; r2g_formal_make_args "$2" || exit $?; printf "%s\\n" "${R2G_FORMAL_MAKE_ARGS[@]}"',
         '_',str(PATH.parent/'_formal_make_args.sh'),str(tmp_path)]
    result=subprocess.run(cmd,env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==['DIE_AREA=0 0 40 40']
    cfg.write_text('export DIE_AREA = not-a-rectangle\n')
    result=subprocess.run(cmd,env=env,text=True,capture_output=True)
    assert result.returncode==76 and not result.stdout


def test_shell_bridge_is_noop_without_formal_manifest(tmp_path):
    env=dict(os.environ);env.pop('R2G_TASK_MANIFEST',None)
    result=subprocess.run(['bash','-c','source "$1"; r2g_formal_make_args "$2"; echo "${#R2G_FORMAL_MAKE_ARGS[@]}"',
        '_',str(PATH.parent/'_formal_make_args.sh'),str(tmp_path)],env=env,text=True,capture_output=True)
    assert result.returncode==0 and result.stdout.strip()=='0'


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
