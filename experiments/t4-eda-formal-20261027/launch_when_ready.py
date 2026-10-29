"""Authorized dependency dispatcher: qualify, freeze, launch four EDA lanes."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'adapters'))
from formal_queue import freeze,validate,record,sha,summary,PYTHONS


def main():
    with (ROOT/'reports/launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        launch()


def launch():
    if (ROOT/'inputs/registration.json').exists():raise ValueError('already frozen; no duplicate launch')
    while True:
        path=ROOT/'reports/trace-adaptation.json'
        state=json.loads(path.read_text()) if path.exists() else {}
        if state.get('status')=='COMPLETE':break
        record(ROOT/'reports/launch-state.json',{'status':'WAITING_TRACE_QUALIFICATION',
            'registered_episodes':308,'traces_checked':len(state.get('results',[])),
            'paid_requests_launched':0,'updated':time.time()})
        time.sleep(10)
    branches={}
    for host,name,receipt in [('rtlscout','test_native_scout_tools_and_cost_offline','modern-tests-final.xml'),
                               ('rtlfixer','test_native_fixer_functional_loop_and_rag_offline','fixer-tests-final.xml')]:
        cases=list(ET.parse(ROOT/'reports'/receipt).getroot().iter('testcase'))
        if any(c.find('failure') is not None or c.find('error') is not None for c in cases):
            raise ValueError('offline regression failed')
        chosen=[c for c in cases if c.get('name')==name]
        if len(chosen)!=1 or chosen[0].find('skipped') is not None:raise ValueError('native host path not exercised')
        branches[host]={'status':'PASS','test':name,'receipt':receipt,
            'passed':sum(c.find('skipped') is None for c in cases),
            'skipped':sum(c.find('skipped') is not None for c in cases)}
    bootstrap=json.loads((ROOT/'inputs/bootstrap.json').read_text())
    for rel,h in bootstrap['copied_files'].items():
        if rel.startswith('code/') and sha(ROOT/rel)!=h:raise ValueError('TEHM core changed')
    record(ROOT/'reports/native-offline-smoke.json',{'status':'PASS','paper_data':False,
        'synthetic_offline_fixtures':True,'actual_model_requests':0,'host_paths':branches,
        'unchanged_TEHM_core':True,'created':time.time()})
    record(ROOT/'inputs/formal-authorization.json',{
        'user_requests':['那么table 4把rtl repair的测试集完整接入，剩下几组的正式实验也跑一下',
                         'RTLFixer 和 RTLScout 都接入完整 RTLRepair','继续实验'],
        'tasks':77,'engineering_projects':24,'hosts':['rtlfixer','rtlscout'],
        'arms':['native','tehm'],'repeat':1,'registered_episodes':308,
        'model':'deepseek-flash','provider':'https://api.deepseek.com/chat/completions',
        'inputs':'public faulty RTL, public specifications and compiler/functional feedback; no golden RTL or keys in model messages',
        'cumulative_token_money_cap':None,'implicit_provider_retries':0,
        'interruption_policy':'preserve raw UNKNOWN, skip affected episode, no original-queue reruns; separate retry round later',
        'memory':'private initially empty, online write-back; evidence/risk and governed promotion unchanged',
        'previous_integration_and_SWE_results':'preserved separately, never overwritten',
        'scope':'local RTLRepair verification adapter, not full ASPLOS VCS/gate-level reproduction',
        'created':time.time()})
    freeze();validate();summary()
    workers=[]
    for host in ('rtlfixer','rtlscout'):
        log=(ROOT/'reports'/f'{host}-dispatcher.log').open('x')
        proc=subprocess.Popen([str(PYTHONS['rtlscout']),str(ROOT/'adapters/formal_queue.py'),
            'run','--host',host],stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
        workers.append((host,proc,log))
    record(ROOT/'reports/launch-state.json',{'status':'RUNNING','supervisors':{h:p.pid for h,p,_ in workers},
        'registered_episodes':308,'updated':time.time()})
    for host,proc,log in workers:
        proc.wait();log.close()
    rows=summary()
    record(ROOT/'reports/launch-state.json',{'status':'QUEUE_PASS_ENDED','remaining_not_started':sum(r['status']=='NOT_STARTED' for r in rows),
        'returncodes':{h:p.returncode for h,p,_ in workers},'automatic_retries':0,'updated':time.time()})


if __name__=='__main__':
    try:main()
    except Exception as exc:
        record(ROOT/'reports/launch-state.json',{'status':'GLOBAL_BLOCKED','reason':str(exc),
            'exception':type(exc).__name__,'updated':time.time()})
        raise
