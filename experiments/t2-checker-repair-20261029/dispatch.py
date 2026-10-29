"""Two checker-only lanes, gated on four old/new clean control checks."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import checker_probe as P


def run_job(job):
    log=P.HERE/(job+'.worker.log')
    with log.open('x') as stream:
        result=subprocess.run([sys.executable,str(P.HERE/'checker_probe.py'),'run',job],stdout=stream,stderr=subprocess.STDOUT)
    receipt=P.HERE/'probes-v2'/job/'receipt.json'
    if result.returncode or not receipt.exists():return {'job':job,'status':'TECHNICAL_ERROR','returncode':result.returncode}
    r=json.loads(receipt.read_text());return {'job':job,'status':r['verdict']['status'],'receipt':str(receipt),'wall_s':r['timing']['wall_s']}


def main():
    plan=json.loads((P.HERE/'plan-v2.json').read_text())
    control=[j for j in plan['jobs'] if j['id'].startswith('C02982')]
    for job in control:
        receipt=json.loads((P.HERE/'probes-v2'/job['id']/'receipt.json').read_text())
        P.check_hashes(job['pins'])
        if receipt['verdict']['status']!='clean' or not receipt['inputs_unchanged']:
            raise ValueError('Control not validated: '+job['id'])
    state={'pid':os.getpid(),'started':time.time(),'paper_data':False,'model_requests':0,'eda_flows':0,
           'max_concurrency':2,'status':'RUNNING','results':[],'plan_sha256':P.sha(P.HERE/'plan-v2.json')}
    P.atomic_json(P.HERE/'dispatch-state.json',state)
    pending=[j['id'] for j in plan['jobs'] if not j['id'].startswith('C02982')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run_job,j) for j in pending]):
            state['results'].append(future.result());P.atomic_json(P.HERE/'dispatch-state.json',state)
    state.update(status='COMPLETE_DIAGNOSTIC_ONLY',ended=time.time())
    P.atomic_json(P.HERE/'dispatch-state.json',state)


if __name__=='__main__': main()
