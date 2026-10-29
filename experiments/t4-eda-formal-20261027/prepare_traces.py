"""Complete the eleven trace-only adapters before paid launch, keeping r1 evidence."""
import copy
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'adapters'))
from trace_adapter import generate
from rtlrepair_eval import evaluate,record


def main():
    source=ROOT/'inputs/rtlrepair-private.json'
    if (ROOT/'inputs/registration.json').exists():raise ValueError('cannot change task inputs after launch freeze')
    original=source.read_text();rows=json.loads(original)
    saved=ROOT/'inputs/rtlrepair-private-output-oracles-v1.json'
    if saved.exists():raise ValueError('trace adaptation already attempted; no implicit retry')
    saved.write_text(original);saved.chmod(0o600)
    results=[]
    for task in rows:
        if any(t['kind']=='output_oracle' for t in task['tests']):continue
        work=ROOT/'qualification'/task['task']
        try:
            test=generate(task,work/'trace-adapter')
            trial=copy.deepcopy(task);trial['tests'].append(test)
            ref=evaluate(trial,Path(task['reference']).read_text(),work/'reference-trace-v2')
            base=evaluate(trial,Path(task['buggy']).read_text(),work/'baseline-trace-v2')
            status='PASS' if ref['status']=='PASS' and base['status'] in {'PASS','FAIL'} else 'BLOCKED'
            # Interface/clock derivation is never accepted on its own. Only a
            # passing reference with complete measured rows admits this adapter.
            if status=='PASS':task['tests'].append(test)
            result={'task':task['task'],'status':status,'reference_status':ref['status'],
                'baseline_status':base['status'],'reference_grade':str(work/'reference-trace-v2/grade.json'),
                'baseline_grade':str(work/'baseline-trace-v2/grade.json'),'model_requests':0,
                'initially_passing':base['status']=='PASS','semantics':test['semantics'],
                'reason':None if status=='PASS' else 'trace replay did not pass differential qualification'}
        except Exception as exc:
            result={'task':task['task'],'status':'BLOCKED','reason':str(exc),'model_requests':0}
        old=work/'qualification.json'
        old.rename(work/'qualification-output-oracles-v1.json')
        record(old,result);results.append(result)
        record(ROOT/'reports/trace-adaptation.json',{'status':'IN_PROGRESS','results':results,'model_requests':0})
    source.write_text(json.dumps(rows,indent=2)+'\n');source.chmod(0o600)
    record(ROOT/'reports/trace-adaptation.json',{'status':'COMPLETE','results':results,
        'model_requests':0,'reference_rtl_in_testbench':False,'created':time.time()})


if __name__=='__main__':main()
