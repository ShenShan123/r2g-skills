#!/usr/bin/env python3
"""Append-only construction delta: AWM [] -> no workflows; resume missing work only.

No NB rerun, no workflow fabrication, no provider retry. Original code, frozen
representations, raw replies and ledger prefixes remain bound and preserved.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(REPO/'memory/scripts'),str(REPO/'memory')]
import t2_nb_followup as F

QUEUE=Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2-nb-memory-followup-20261020-r6')
DELTAS=(
    ('def workflow_text(value):\n',
     "def workflow_text(value):\n    if isinstance(value, list) and not value:\n        return ''  # explicit zero workflows; never invent text or retry\n"),
    ('            workflows = {}', '            workflows, empty_workflows = {}, []'),
    ("                    results.append(workflow_text(answer.get('workflows')))",
     "                    text = workflow_text(answer.get('workflows'))\n                    if text:\n                        results.append(text)\n                    else:\n                        empty_workflows.append({'call_id': 'workflow-%02d-%02d' % (i,j), 'group': group})"),
    ("                    results=[workflow_text(answer.get('workflows'))]",
     "                    results=[workflow_text(answer.get('workflows'))]\n                    if not results[0]:\n                        empty_workflows.append({'call_id': 'workflow-merge-%02d' % i, 'group': group})"),
    ("            data.update(workflows=workflows, input_mode='official WebArena template deduplication',",
     "            data.update(workflows=workflows, empty_workflow_results=empty_workflows,\n                        empty_workflow_policy='EXPLICIT_EMPTY_LIST_IS_ZERO_WORKFLOWS_NO_RETRY',\n                        construction_code_sha256=Q.digest(Path(__file__)), input_mode='official WebArena template deduplication',"),
)


def validate_builder_delta(old,new):
    expected=old
    for before,after in DELTAS:
        if expected.count(before)!=1:raise ValueError('unexpected base constructor')
        expected=expected.replace(before,after,1)
    if expected!=new:raise ValueError('unregistered constructor change')


def prefix_snapshot(paths):
    return {str(p):{'bytes':len(p.read_bytes()),'sha256':F.sha(p)} for p in paths}


def verify_prefixes(prior):
    for path,want in prior.items():
        data=Path(path).read_bytes()
        if len(data)<want['bytes'] or hashlib.sha256(data[:want['bytes']]).hexdigest()!=want['sha256']:
            raise ValueError('prior cost prefix changed: '+path)


def preserved(queue):
    """Immutable artifacts stay byte-identical; accounting files may only append."""
    fixed={};prefixes=[];state=[]
    for n in ('dispatch-state.json','table2-results.json','table2-matrix.csv','run.log','controller-start.json'):
        if (queue/n).exists():state.append(queue/n)
    for fold in (queue/'folds').iterdir():
        for name in ('code','source','ann','shared','tehm-import','store','tasks'):
            fixed.update(F.frozen(fold/name))
        for n in ('offline-ready.json','exclusions.json','verification.json','overrides.json','evidence-index.json',
                  'tehm-v2-policy.json','episode-budget.json','online-input-authorization.json','registration.json'):
            if (fold/n).exists():fixed[str(fold/n)]=F.sha(fold/n)
        controls=fold/'controls'
        for n in ('registration.json','budget.json',*('{}.json'.format(a) for a in ('RET','RECIPE','EXPEL','AWM','AMEM'))):
            if (controls/n).exists():fixed[str(controls/n)]=F.sha(controls/n)
        for p in controls.glob('*-*.json'):
            if p.name.endswith(('-progress.json','-stop.json')):state.append(p)
        for p in (controls/'construction').rglob('*'):
            if not p.is_file():continue
            if p.name=='resource_ledger.jsonl' or p.name=='embedding-calls.jsonl':prefixes.append(p)
            elif p.name.endswith(('.answer.json','.request.json','.response.json')):fixed[str(p)]=F.sha(p)
        if (controls/'phase/resource_ledger.jsonl').exists():prefixes.append(controls/'phase/resource_ledger.jsonl')
    prefixes.append(queue/'online-phase/resource_ledger.jsonl')
    return fixed,prefix_snapshot(prefixes),state


def missing_builds(queue):
    missing=[]
    for hid in F.N.IDS:
        fold=queue/'folds'/hid;expected=F.load(fold/'shared/manifest.json')['units_sha256']
        for arm in ('EXPEL','AWM','AMEM'):
            p=fold/'controls'/(arm+'.json')
            if p.exists():
                data=F.load(p)
                if data.get('status')!='FROZEN' or data.get('units_sha256')!=expected:
                    raise ValueError('existing frozen representation invalid: '+str(p))
            else:missing.append({'target':hid,'arm':arm})
    return missing


def prepare(queue,revision):
    if revision.exists():raise ValueError('revision exists; never overwrite')
    q=F.load(queue/'queue-registration.json');F.verify_files(q['files']);F.verify_files(q['external_inputs'])
    if F.load(queue/'dispatch-state.json').get('status')!='PAUSED_TECHNICAL':raise ValueError('queue not paused technically')
    if list((queue/'folds').glob('*/episodes/*/episode.json')):raise ValueError('online episodes exist; separate continuation required')
    nb=Path(q['nb'])
    if not F.nb_gate(nb):raise ValueError('NB not complete')
    for fold in (queue/'folds').iterdir():
        if F.load(fold/'verification.json').get('gate')!='PASS':raise ValueError('offline verification not PASS')
        for led in (fold/'controls').rglob('resource_ledger.jsonl'):
            ev=F.rows(led)
            if F.ledger_cost(led)['unknown_requests']:raise ValueError('UNKNOWN request requires separate authorisation')
            holds={v['hold_id'] for v in ev if v.get('type')=='budget_hold'}
            settled={v['hold_id'] for v in ev if v.get('type')=='budget_settle'}
            if holds-settled:raise ValueError('open construction holds')
    old=queue/'code/memory/scripts/t2a_construct.py';new=Path(__file__).parent/'t2a_construct.py'
    validate_builder_delta(old.read_text(),new.read_text())
    fixed,prefixes,state=preserved(queue)
    fixed.update(F.load(queue/'nb-terminal-receipt.json')['raw_inputs'])
    revision.mkdir(parents=True)
    shutil.copytree(queue/'code',revision/'code')
    shutil.copy2(new,revision/'code/memory/scripts/t2a_construct.py')
    shutil.copy2(__file__,revision/'code/memory/scripts/t2_nb_awm_resume.py')
    for p in state:
        dst=revision/'prior-state'/p.relative_to(queue);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
    F.save(revision/'protected-artifacts.json',fixed);F.save(revision/'ledger-prefixes.json',prefixes)
    F.save(revision/'authorization.json',{'user_text':'处理 AWM 空结果的兼容逻辑，再保留已有产物和成本接续',
        'scope':'explicit empty workflows list is zero workflows; reuse caches and frozen arms; continue only unfinished construction then 48 never-started episodes',
        'no_NB_rerun':True,'no_provider_retry':True,'no_new_cumulative_cap':True,'learning_curve_untouched':True})
    files=F.frozen(revision/'code');files.update(F.frozen(revision/'prior-state'))
    files.update({str(revision/n):F.sha(revision/n) for n in ('protected-artifacts.json','ledger-prefixes.json','authorization.json')})
    F.save(revision/'registration.json',{'schema':'nb8-awm-empty-construction-delta-v1','base_queue':str(queue),
        'base_queue_registration_sha256':F.sha(queue/'queue-registration.json'),'files':files,
        'base_constructor_sha256':F.sha(old),'new_constructor_sha256':F.sha(revision/'code/memory/scripts/t2a_construct.py'),
        'changed_semantics':'AWM explicit [] -> zero workflows; missing/null/malformed still fail',
        'missing_builds':missing_builds(queue),'online_pending':48,'concurrency':7,'created':time.time()})
    print(json.dumps({'revision':str(revision),'missing_builds':len(missing_builds(queue)),'model_calls':0,'EDA_flows':0}),flush=True)


def invariant(queue,revision):
    r=F.load(revision/'registration.json');q=F.load(queue/'queue-registration.json')
    if F.sha(queue/'queue-registration.json')!=r['base_queue_registration_sha256']:raise ValueError('base queue changed')
    F.verify_files(r['files']);F.verify_files(q['files']);F.verify_files(q['external_inputs'])
    F.verify_files(F.load(revision/'protected-artifacts.json'))
    verify_prefixes(F.load(revision/'ledger-prefixes.json'))


def run(queue,revision):
    import t2a_batch as B
    if Path(__file__).resolve()!=revision/'code/memory/scripts/t2_nb_awm_resume.py':raise ValueError('frozen private resume required')
    lock=(queue/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    invariant(queue,revision);q=F.load(queue/'queue-registration.json');nb=Path(q['nb'])
    if F.load(queue/'dispatch-state.json').get('status')!='PAUSED_TECHNICAL':raise ValueError('base queue no longer paused')
    if list((queue/'folds').glob('*/episodes/*/episode.json')):raise ValueError('refuse duplicate online run')
    os.environ.update(R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'],
                      TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1')
    if not set(c for s in F.SLOTS for c in s)<=os.sched_getaffinity(0):raise ValueError('frozen CPUs unavailable')
    with (revision/'resume-start.json').open('x') as f:json.dump({'started':time.time(),'registration_sha256':F.sha(revision/'registration.json')},f)
    registry=F.load(nb/'origin-registry.json')
    try:
        for item in missing_builds(queue):
            hid,arm=item['target'],item['arm'];fold=queue/'folds'/hid
            state={'status':'RESUMING_MEMORY_CONSTRUCTION','target':hid,'arm':arm,'pending':48,'running':[],
                   'controller_revision':str(revision),'updated':time.time()}
            F.save(queue/'dispatch-state.json',state);F.save(revision/'dispatch-state.json',state)
            cmd=[sys.executable,str(revision/'code/memory/scripts/t2a_construct.py'),'build','--root',str(fold/'controls'),'--arm',arm]
            log=revision/'logs'/('build-'+hid+'-'+arm+'.log');log.parent.mkdir(exist_ok=True)
            with log.open('x') as f:
                subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True,env={**os.environ,
                    'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                    'R2G_EMBEDDING_METRICS_FILE':str(fold/'controls/construction'/arm/'reports/embedding-calls.jsonl')})
            invariant(queue,revision);F.collect(queue)
        folds=[queue/'folds'/hid for hid in F.N.IDS]
        for fold in folds:
            if (fold/'registration.json').exists():
                B.preflight(fold,'TEST')  # already frozen: do not overwrite
            else:
                F.seal_runtime(queue,fold,nb,registry,fold.name)
                reg=F.load(fold/'registration.json')
                reg['construction_revision']={'path':str(revision/'registration.json'),'sha256':F.sha(revision/'registration.json')}
                reg['files'][str(revision/'registration.json')]=F.sha(revision/'registration.json')
                reg['files'].update(F.load(revision/'registration.json')['files'])
                F.save(fold/'registration.json',reg);B.preflight(fold,'TEST')
        jobs=[{'root':str(f),'task':registry[f.name]['task'],'arm':a} for f in folds for a in F.ARMS]
        if (queue/'frozen-memory-plan.json').exists():raise ValueError('prior online plan exists; no implicit restart')
        F.save(queue/'frozen-memory-plan.json',{'jobs':jobs,'registrations':{str(f/'registration.json'):F.sha(f/'registration.json') for f in folds},
            'NB_reused':8,'construction_delta':str(revision/'registration.json')})
        invariant(queue,revision)
        state=F.dispatch(queue,jobs)
        F.save(revision/'dispatch-state.json',state)
        verify_prefixes(F.load(revision/'ledger-prefixes.json'))
    except Exception as exc:
        state={'status':'PAUSED_TECHNICAL','reason':str(exc),'controller_revision':str(revision),'updated':time.time()}
        F.save(queue/'dispatch-state.json',state);F.save(revision/'dispatch-state.json',state);F.collect(queue)
        raise


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','run'))
    ap.add_argument('--queue',type=Path,default=QUEUE);ap.add_argument('--revision',type=Path,required=True)
    a=ap.parse_args();queue=a.queue.resolve();revision=a.revision.resolve()
    if a.command=='prepare':prepare(queue,revision)
    else:run(queue,revision)

if __name__=='__main__':main()
