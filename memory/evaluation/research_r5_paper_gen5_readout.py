"""Normalize the one audited gen5 pilot without inventing unseen/final samples."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sys

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from tehm.evaluation.research_r5_paper_protocol import VIEWS, summarize, validate_plan
PILOT=Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')
CASE=PILOT/'transfer/drewbabel-axis-skid-gen5-pilot-r1'
CONTROL=PILOT/'transfer/abarajithan-axis-fifo-nontarget-gen5-r1'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def pinned(path,digest):
    if path.is_symlink() or sha(path)!=digest: raise ValueError('evidence drift: '+str(path))
    return json.loads(path.read_text())
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=False)
    plan=json.loads((APP/'evaluation/research_r5_paper_protocol_v1.json').read_text())
    readiness=validate_plan(plan)
    pilot=pinned(CASE/'run-r1/receipt.json','633f68fce28bdd30b075801001f36e9cb5d5df95151d054d3490488b1c690e07')
    auditor=CASE/'audit.py'
    if sha(auditor)!='714ff75f187117921741d45c2858de2a42ee68586f602abcb4ba6b85a89aa549': raise ValueError('auditor drift')
    spec=importlib.util.spec_from_file_location('pilot_raw_audit',auditor)
    raw=importlib.util.module_from_spec(spec); spec.loader.exec_module(raw); raw.validate(pilot)
    control=pinned(CONTROL/'run-r1/receipt.json','fdc35ef4db30089fbb0a4fead3c453b12ee86e91b133b0225a2761e9391c50c1')
    control_audit=pinned(CONTROL/'audit-r1.json','2cd62d066d4e95c22b02f570d5da6cbbe2650e6d47d2ae12e09e907b84c1ef27')
    if not control_audit['valid'] or control_audit['receipt_sha256']!=sha(CONTROL/'run-r1/receipt.json'): raise ValueError('control audit linkage')
    shared={k:pilot['lock'][k] for k in ('software_head','context_sha256','manifest_sha256','controller_sha256','consumer_sha256','qualification_sha256')}
    contract='sha256:'+hashlib.sha256(canonical(shared)).hexdigest()
    manifest={'role':'PILOT_TRANSFER','views':list(VIEWS),'shared_contract_digest':contract,'candidate_budget':1,
              'tasks':[{'task_id':'pilot_task_004','role':'PILOT_TRANSFER','source_group':'drewbabel_axis_skid_bounded_uncertified',
                        'required_oracles':['target','preservation','native']}], 'shared_inputs':shared}
    normalized=[]
    for attempt in pilot['attempts']:
        if attempt['task']!='fault': continue
        action=attempt['consumer']
        verdicts={o['scope']:'PASS' if o['verdict']=='PASS' else 'FAIL' if o['verdict'].startswith('FAIL_') else 'UNKNOWN' for o in attempt['oracles']}
        normalized.append({'task_id':'pilot_task_004','view':attempt['view'],'shared_contract_digest':contract,
            'candidate_budget':1,'candidates_attempted':1,'source_changed':action['source_changed'],'verdicts':verdicts,
            'oracle_wall_seconds':sum(c['elapsed_seconds'] for o in attempt['oracles'] for c in o['commands'])})
    summary=summarize(manifest,normalized)
    summary['controls']={'same_dut_healthy_counterpart':{'registered_designs':1,'attempts':4,
        'false_activations':sum(a['consumer']['source_changed'] for a in pilot['attempts'] if a['task']=='clean')},
        'distinct_healthy_nontarget':{'registered_designs':1,'attempts':3,'native_case_executions':9,
        'false_activations':sum(a['false_activation'] for a in control['attempts'])}}
    summary['cost_scope']='oracle command wall time only; authority/acquisition/development cost unmeasured, not zero'
    outputs={'manifest.json':manifest,'normalized-results.json':normalized,'summary.json':summary,'readiness.json':readiness}
    for name,value in outputs.items(): (output/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    receipt={'schema':'r5-gen5-reviewed-readout-receipt-v1','pilot_raw_audit_reexecuted':True,
             'control_raw_audit_receipt_sha256':sha(CONTROL/'audit-r1.json'),
             'normalizer_sha256':sha(Path(__file__)),'bookkeeping_sha256':sha(APP/'tehm/evaluation/research_r5_paper_protocol.py'),
             'files':{name:sha(output/name) for name in outputs},'final_test_ready':False,'model_calls':0}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'output':str(output),'receipt_sha256':sha(output/'receipt.json'),'registered_tasks':summary['registered_tasks'],
        'repairs':{v:summary['per_view'][v]['successes'] for v in VIEWS},'final_test_ready':False},indent=2))
if __name__=='__main__': main()
