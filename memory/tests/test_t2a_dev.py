import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import t2a_dev as D
import t2a_batch as B


def test_collector_rejects_raw_not_accepted_without_terminal_verification(tmp_path,monkeypatch):
    reg={'plan':[{'task':D.TASK,'arm':a,'repeat':1} for a in B.T.ARMS],
         'runtime_hashes':{},'external_hashes':{},'control_hashes':{},'phase_binding':{}}
    (tmp_path/'registration.json').write_text('{}')
    monkeypatch.setattr(B,'preflight',lambda *a,**k:reg)
    for row in reg['plan']:
        p=tmp_path/'episodes'/(D.TASK.replace('/','__')+'__'+row['arm']+'__r1')
        (p/'reports').mkdir(parents=True)
        data={'split':'DEV','paper_data':False,'status':'NOT_ACCEPTED','accepted':False,
              'accounting':{'passed':True},'token_usage':{'measurement_complete':True},
              'final':{'accepted':False,'execution_recovery':'UNVERIFIED','recovery_rc':2}}
        (p/'episode.json').write_text(json.dumps(data))
        (p/'reports/resource_ledger.jsonl').write_text(json.dumps({'type':'llm_request','role':'auxiliary'})+'\n'+
            json.dumps({'type':'memory_retrieval'})+'\n')
    D.collect(tmp_path)
    receipt=json.loads((tmp_path/'dev-validation.json').read_text())
    assert receipt['real_seven_arm_dev_validated'] is False
    assert all(not r['criteria_passed'] for r in receipt['records'])


def test_dev_inputs_copy_exact_rtl_and_clock_without_changing_old_project(tmp_path,monkeypatch):
    source=tmp_path/'old';(source/'rtl').mkdir(parents=True);(source/'constraints').mkdir()
    a=source/'rtl/a.v';b=source/'rtl/b.v';a.write_text('module a; endmodule\n');b.write_text('module b; endmodule\n')
    sdc=source/'constraints/constraint.sdc';sdc.write_text('set clk_period 10.0\n')
    config=source/'constraints/config.mk'
    config.write_text('export DESIGN_NAME = riscv_core\nexport PLATFORM = sky130hs\n'+
        'export VERILOG_FILES = '+str(a)+' \\\n  '+str(b)+'\nexport SDC_FILE = '+str(sdc)+'\nexport CORE_UTILIZATION = 19\n')
    run=source/'backend/RUN_INITIAL/logs';run.mkdir(parents=True)
    (run/'2_1_floorplan.json').write_text(json.dumps({v:100 for v in D.M.FLOOR_KEYS.values()}))
    receipt=tmp_path/'qualification.json';receipt.write_text(json.dumps({'project':str(source),
        'design':'riscv_core','variant':'u19','final_layout':True}))
    before=(a.read_bytes(),b.read_bytes(),sdc.read_bytes(),config.read_bytes())
    monkeypatch.setattr(D,'QUALIFICATION',receipt)
    path=D.inputs(tmp_path/'new-inputs');inventory=json.loads(path.read_text())
    assert B.validate_dev_inventory(inventory)==[]
    manifest=json.loads(Path(inventory['tasks'][0]['manifest']).read_text())
    project=Path(manifest['source_project'])
    assert manifest['platform']=='sky130hs' and manifest['expected_clock_period_ns']==10
    assert (project/'rtl/a.v').read_bytes()==a.read_bytes()
    assert (project/'constraints/constraint.sdc').read_bytes()==sdc.read_bytes()
    assert before==(a.read_bytes(),b.read_bytes(),sdc.read_bytes(),config.read_bytes())
    assert manifest['split']=='DEV' and manifest['formal_launch_allowed'] is False
    with pytest.raises(FileExistsError):D.inputs(tmp_path/'new-inputs')


def test_test_family_cannot_enter_the_dev_bypass_path():
    inventory={'status':'FROZEN_DEV','split':'DEV','source_sha256':D.A.SOURCE_SHA,
        'tasks':[{'task':'ibex/u80','family':'lowRISC/ibex','status':'ADMITTED_DEV'}]}
    assert B.validate_dev_inventory(inventory)


def test_missing_real_episodes_cannot_be_published_as_dev_validation(tmp_path,monkeypatch):
    reg={'plan':[{'task':D.TASK,'arm':a,'repeat':1} for a in B.T.ARMS],
         'runtime_hashes':{},'external_hashes':{},'control_hashes':{},'phase_binding':{}}
    (tmp_path/'registration.json').write_text('{}')
    monkeypatch.setattr(B,'preflight',lambda *a,**k:reg)
    D.collect(tmp_path)
    d=json.loads((tmp_path/'dev-validation.json').read_text())
    assert d['real_seven_arm_dev_validated'] is False and len(d['missing'])==7
    assert d['paper_data'] is False and d['formal_launch_allowed'] is False
