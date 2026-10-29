"""One no-model qualification pass over all 77 registered RTLRepair cases."""
import argparse
import json
from pathlib import Path
import time
import traceback
from rtlrepair_data import ROOT
from rtlrepair_eval import evaluate,record


def main():
    rows=json.loads((ROOT/'inputs/rtlrepair-private.json').read_text())
    ready=[];blocked=[]
    for task in rows:
        work=ROOT/'qualification'/task['task'];path=work/'qualification.json'
        if path.exists():raise ValueError('qualification pass already started; no automatic retry')
        record(ROOT/'reports/qualification-state.json',{'status':'QUALIFYING','task':task['task'],
            'qualified':len(ready),'blocked':len(blocked),'model_requests':0,'updated':time.time()})
        try:
            reference=evaluate(task,Path(task['reference']).read_text(),work/'reference')
            baseline=evaluate(task,Path(task['buggy']).read_text(),work/'baseline')
            ok=reference['status']=='PASS' and baseline['status'] in {'PASS','FAIL'}
            result={'status':'PASS' if ok else 'BLOCKED','task':task['task'],
                'reference_status':reference['status'],'baseline_status':baseline['status'],
                'initially_passing':baseline['status']=='PASS','model_requests':0,
                'reference_grade':str(work/'reference/grade.json'),
                'baseline_grade':str(work/'baseline/grade.json'),
                'reason':None if ok else 'reference/buggy evaluation not qualified',
                'scope':reference['scope'],'created':time.time()}
        except Exception as exc:
            result={'status':'BLOCKED','task':task['task'],'reason':str(exc),
                'traceback':traceback.format_exc(),'model_requests':0,'created':time.time()}
        record(path,result)
        (ready if result['status']=='PASS' else blocked).append(task['task'])
    record(ROOT/'reports/qualification-state.json',{'status':'COMPLETE','registered':77,
        'qualified':ready,'blocked':blocked,'automatic_retries':0,'model_requests':0,'updated':time.time()})


if __name__=='__main__':main()
