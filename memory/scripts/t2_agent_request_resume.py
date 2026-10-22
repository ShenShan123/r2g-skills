#!/usr/bin/env python3
"""Append-only agent9 continuation: request-count policy, provider output maximum.

Old constructors, raw replies, completed memories, NB and accounting prefixes
remain unchanged. A hash-bound entrypoint installs this opt-in policy only in
the new constructors/workers. No production or learning-curve client changes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

CODE_ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(CODE_ROOT/'memory/scripts'),str(CODE_ROOT/'memory')]

PROVIDER_MAX_OUTPUT=393216
POLICY='agent9-request-count-only-provider-maximum-json-v2'
JSON_MODE_POLICY='explicit-json-instruction-before-admission-v1'
DOC='https://api-docs.deepseek.com/api/create-chat-completion/'
RB_OLD=r"r'\s*## Title\s+(.+?)\s*\n## Description\s+(.+?)\s*\n## Content\s+(.+?)\s*'"
RB_NEW=r"r'\s*## Title:?\s+(.+?)\s*\n## Description:?\s+(.+?)\s*\n## Content:?\s+(.+?)\s*'"
ARMS=('RET','RECIPE','EXPEL','AWM','AMEM','REME','REASONINGBANK','TM0')
BUILDS=('EXPEL','AWM','AMEM','REME','REASONINGBANK')
AMEM_CONTEXT_POLICY='amem-retrieval-abstract-no-verbatim-repetition-v1'
AMEM_CONTEXT_INSTRUCTION='''
For this A-MEM evolution, each new_context_neighborhood entry is a retrieval abstract,
not an archive of its neighbor. Keep it concise (normally one to three sentences),
describing the applicable situation, observed action/effect, outcome and rollback
or UNKNOWN evidence, plus a useful relationship to the new note. Preserve uncertainty;
do not turn an observation into a verified causal rule. Do not copy full config files,
raw JSON reports, complete trajectories or local file lists into a context. Refer to
the existing memory ID instead; the original content is already retained unchanged.
Do not repeat a context, configuration or report inside a context. Return each of the
five-or-fewer input neighbors once in its original order, with its context and tags,
using exactly the requested JSON fields. Finish the JSON object after those fields.
'''.strip()


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def save(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=1,allow_nan=False)+'\n')
def rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()] if Path(path).exists() else []


def json_mode_messages(messages,response_format):
    """Satisfy JSON transport requirements before serialisation and cost admission.

    JSON-looking punctuation alone is not the provider's explicit instruction.
    No model request, retries, candidate or memory content is added here.
    """
    if response_format!={'type':'json_object'} or any(
            'json' in str(m.get('content','')).lower() for m in messages):
        return messages
    out=[dict(m) for m in messages]
    if out and out[0].get('role')=='system':
        out[0]['content']+='\nReturn one JSON object only.'
    else:out.insert(0,{'role':'system','content':'Return one JSON object only.'})
    return out


def amem_summary_messages(messages):
    """Keep all source inputs; clarify the native context field's summary role."""
    out=[dict(m) for m in messages]
    out[0]['content']+='\n'+AMEM_CONTEXT_INSTRUCTION
    return out


def verify_amem_contexts(answer):
    """Semantic repetition check, not an output length/token limit."""
    for text in answer.get('new_context_neighborhood',[]):
        if not isinstance(text,str):raise RuntimeError('Invalid A-MEM context type')
        # Long verbatim blocks recurring inside one summary indicate copying,
        # not a useful association. Short names/knobs may legitimately recur.
        blocks=Counter(text[i:i+256] for i in range(0,max(0,len(text)-255),64))
        if any(n>=3 for n in blocks.values()):
            raise RuntimeError('A-MEM context contains repeated verbatim report/config blocks')


def response_has_cache(response):
    """Recognise successful explicit retries without replaying their paid calls."""
    response=Path(response)
    cache=Path(str(response).replace('.try0.response.json','.answer.json'))
    if cache.exists():return True
    suffix='.authorized-output-resume-1.try0.response.json'
    if not response.name.endswith(suffix):return False
    original=Path(str(response)[:-len(suffix)])
    cache=Path(str(original)+'.answer.json')
    if not cache.exists():return False
    data=load(cache);link=data.get('explicit_retry',{})
    prefix=str(response).removesuffix('.try0.response.json')
    raw=load(response)
    import t2a_construct as C
    choices=raw.get('choices',[])
    if (link.get('new_prefix')!=prefix or len(choices)!=1 or choices[0].get('finish_reason')!='stop'
            or C.parse_answer(choices[0].get('message',{}).get('content',''))!=data.get('answer')
            or any(raw.get('usage',{}).get(k)!=data.get('usage',{}).get(k)
                   for k in ('prompt_tokens','completion_tokens'))):
        raise ValueError('Explicit retry cache does not match its raw response: '+str(response))
    for path,key in ((str(original)+'.try0.request.json','request_sha256'),
                     (str(original)+'.try0.response.json','response_sha256')):
        if link.get(key) and sha(path)!=link[key]:raise ValueError('Original retry evidence changed: '+path)
    return True


def policy():
    return {'revision':POLICY,'scope':'agent9_construction_and_online',
        'input_bytes_ceiling':None,'output_tokens':PROVIDER_MAX_OUTPUT,
        'outer_output_ceiling':None,'output_limit_source':'provider hard maximum, not experiment quota',
        'provider_default_if_omitted':8192,'provider_context_tokens':1048576,
        'repair_generations':3,'total_online_requests':3,'scheduler':'agent_selected',
        'transport_retries':0,'full_insights_and_workflows_required':True,
        'provider_documentation':DOC,'cumulative_token_cap':None,
        'json_mode_transport_validation':JSON_MODE_POLICY}


def configure_harness(H):
    """Keep the original metering/transport intact; override only token settings."""
    if getattr(H,'_AGENT_REQUEST_POLICY',None)==POLICY:return
    original=H.llm_call
    H.LLM_MAX_TOKENS=PROVIDER_MAX_OUTPUT
    H.LLM_OUTPUT_CEILING=PROVIDER_MAX_OUTPUT
    # The original client compares bytes to this value, without serializing it.
    # The public receipt uses None, never a non-finite JSON number.
    H.LLM_MAX_INPUT_BYTES=float('inf')
    def call(key,messages,out_prefix,*args,**kwargs):
        if args:raise RuntimeError('Positional retry options are not supported by this policy')
        if kwargs.get('backoff'):raise RuntimeError('Implicit retries remain forbidden')
        override=dict(kwargs.get('sampling_override') or {})
        override['max_tokens']=PROVIDER_MAX_OUTPUT
        kwargs['sampling_override']=override
        messages=json_mode_messages(messages,override.get('response_format',{'type':'json_object'}))
        ledger=kwargs.get('ledger')
        if ledger is not None:
            rc,_=H._ledger(ledger,'event','--type','request_policy','--json',json.dumps(policy()))
            if rc:raise RuntimeError('Cannot record request policy')
        content,usage=original(key,messages,out_prefix,**kwargs)
        response=Path(str(out_prefix)+'.try0.response.json')
        if response.exists():
            try:reasons=[c.get('finish_reason') for c in load(response).get('choices',[])]
            except (ValueError,TypeError,AttributeError):reasons=[]
            if 'length' in reasons:
                # Actual usage is still known/charged, but incomplete content is
                # not a valid memory or a measured candidate-generation failure.
                return None,{**usage,'technical_stop':'provider_output_truncated'}
        return content,usage
    H.llm_call=call;H._AGENT_REQUEST_POLICY=POLICY


def install_online():
    import t2a_runtime as T
    original=T.configure_online
    if getattr(T,'_AGENT_REQUEST_POLICY',None)==POLICY:return
    def configure(H):
        original(H) # Retain the base-client identity checks before opting in.
        configure_harness(H)
        return policy()
    T.configure_online=configure;T.online_policy=policy;T._AGENT_REQUEST_POLICY=POLICY


def ledger_complete(path):
    ev=rows(path)
    holds={e['hold_id'] for e in ev if e.get('type')=='budget_hold'}
    settled={e['hold_id'] for e in ev if e.get('type')=='budget_settle'}
    if holds-settled:raise ValueError('Unsettled prior request: '+str(path))
    for e in ev:
        if e.get('type')!='llm_request':continue
        u=e.get('usage_reported')
        if not isinstance(u,dict) or any(not isinstance(u.get(k),(int,float)) for k in ('prompt_tokens','completion_tokens')):
            raise ValueError('UNKNOWN usage needs separate reconciliation: '+str(path))


def install_constructor(revision):
    import t2a_construct as C
    original=C.Calls
    grant=load(Path(revision)/'authorization.json')
    retry_grants=grant['truncated_requests']
    class Calls(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            configure_harness(self.H)

        def ask(self,ident,prompt,temperature,max_tokens=800):
            guarded=(grant.get('amem_context_policy')==AMEM_CONTEXT_POLICY
                and self.project.name=='AMEM' and ident.startswith('evolution-')
                and not (self.project/(ident+'.answer.json')).exists())
            if not guarded:return self._ask(ident,prompt,temperature,max_tokens)
            wire=self.H.llm_call
            def summary_call(key,messages,prefix,**kwargs):
                content,usage=wire(key,amem_summary_messages(messages),prefix,**kwargs)
                if content is not None:verify_amem_contexts(C.parse_answer(content,False))
                return content,usage
            self.H.llm_call=summary_call
            try:
                answer=self._ask(ident,prompt,temperature,max_tokens)
                cached=self.project/(ident+'.answer.json');data=load(cached)
                data['amem_context_policy']=AMEM_CONTEXT_POLICY
                data['amem_context_instruction_sha256']=hashlib.sha256(AMEM_CONTEXT_INSTRUCTION.encode()).hexdigest()
                save(cached,data)
                return answer
            finally:self.H.llm_call=wire

        def _ask(self,ident,prompt,temperature,max_tokens=800):
            prefix=self.project/ident;cached=Path(str(prefix)+'.answer.json')
            previous=Path(str(prefix)+'.try0.response.json')
            if cached.exists():
                # Preserve exact semantic-prompt signatures and valid old cache
                # bytes. The changed generation ceiling does not change prompts.
                return super().ask(ident,prompt,temperature,max_tokens)
            if not previous.exists():
                answer=super().ask(ident,prompt,temperature,max_tokens)
                data=load(cached);data['request_policy']=policy();save(cached,data)
                return answer
            key=str(previous.resolve())
            if key not in retry_grants:raise RuntimeError('Unregistered previous response: '+ident)
            expected=retry_grants[key]
            request=Path(str(prefix)+'.try0.request.json')
            if sha(previous)!=expected['response_sha256'] or sha(request)!=expected['request_sha256']:
                raise RuntimeError('Truncated request evidence changed')
            raw=load(previous);body=load(request)
            messages=[{'role':'system','content':'Respond with one JSON object only.'},
                      {'role':'user','content':prompt}]
            if (body.get('messages')!=messages or body.get('temperature')!=temperature
                    or body.get('model')!=self.H.LLM_API_MODEL
                    or [c.get('finish_reason') for c in raw.get('choices',[])]!=['length']):
                raise RuntimeError('Explicit truncation retry does not match the same task/prompt')
            ledger_complete(self.project/'reports/resource_ledger.jsonl')
            new=Path(str(prefix)+'.authorized-output-resume-1')
            if list(self.project.glob(new.name+'.try*')):raise RuntimeError('Explicit retry already sent')
            claim=Path(str(new)+'.claim.json')
            with claim.open('x') as f:
                json.dump({'authorization':str(Path(revision)/'authorization.json'),
                    'authorization_sha256':sha(Path(revision)/'authorization.json'),
                    'original_response':key,**expected,'new_policy':POLICY},f,sort_keys=True)
                f.flush();os.fsync(f.fileno())
            rc,_=self.H._ledger(self.project,'event','--type','explicit_output_truncation_retry',
                '--json',json.dumps({'original_response':key,'new_prefix':str(new),'authorization':sha(Path(revision)/'authorization.json')}))
            if rc:raise RuntimeError('Cannot record explicit retry')
            started=time.time()
            content,usage=self.H.llm_call(self.key,messages,new,backoff=(),ledger=self.project,
                role='auxiliary',sampling_override={'temperature':temperature})
            if content is None or usage.get('accounting_error'):
                raise RuntimeError('Explicit truncation retry stopped: '+str(usage))
            answer=C.parse_answer(content,getattr(self,'tolerate',False))
            signature=C.A.fingerprint([messages,temperature,max_tokens])
            save(cached,{'signature':signature,'answer':answer,'usage':usage,
                'wall_s':time.time()-started,'role':'offline_construction','request_policy':policy(),
                'explicit_retry':{'original_response':key,'new_prefix':str(new),**expected}})
            self.count+=1
            return answer
    C.Calls=Calls


def preserved(queue):
    fixed={};prefixes={};states=[]
    dynamic={'dispatch-state.json','table2-results.json','table2-matrix.csv'}
    for p in queue.rglob('*'):
        if not p.is_file() or '__pycache__' in p.parts:continue
        if p.name in ('resource_ledger.jsonl','embedding-calls.jsonl'):
            prefixes[str(p)]={'bytes':p.stat().st_size,'sha256':sha(p)}
        elif p.name in dynamic or p.name.endswith(('-progress.json','-stop.json')):
            states.append(p)
        else:fixed[str(p)]=sha(p)
    return fixed,prefixes,states


def invariant(queue,revision):
    reg=load(revision/'registration.json')
    for p,h in {**reg['files'],**load(revision/'protected-artifacts.json')}.items():
        if sha(p)!=h:raise RuntimeError('Protected artifact changed: '+p)
    for p,want in load(revision/'ledger-prefixes.json').items():
        with Path(p).open('rb') as f:data=f.read(want['bytes'])
        if len(data)!=want['bytes'] or hashlib.sha256(data).hexdigest()!=want['sha256']:
            raise RuntimeError('Prior cost prefix changed: '+p)


def missing_builds(queue):
    out=[]
    for fold in sorted((queue/'folds').iterdir()):
        if not fold.is_dir():continue
        expected=load(fold/'shared/manifest.json')['units_sha256']
        for arm in BUILDS:
            file=fold/'controls'/(arm+'.json')
            if file.exists():
                d=load(file)
                if d.get('status')!='FROZEN' or d.get('units_sha256')!=expected:
                    raise ValueError('Existing representation identity mismatch')
            else:out.append({'target':fold.name,'arm':arm})
    return out


def prepare(queue,revision,tests):
    import xml.etree.ElementTree as ET
    import t2_agent_followup as G
    import t2_nb_followup as F
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)<20 or any(c.find(t) is not None for c in cases for t in ('failure','error','skipped')):
        raise ValueError('Passing resume/request-policy tests required')
    if revision.exists():raise ValueError('No overwrite of a revision')
    if load(queue/'dispatch-state.json')['status']!='PAUSED_TECHNICAL':raise ValueError('Queue not technically paused')
    q=load(queue/'queue-registration.json');F.verify_files(q['files']);F.verify_files(q['external_inputs'])
    nb=Path(q['nb'])
    if not G.nb_gate(nb):raise ValueError('NB not complete')
    if list((queue/'folds').glob('*/episodes/*/episode.json')):raise ValueError('Online episodes already exist')
    for fold in (queue/'folds').iterdir():
        if (fold/'registration.json').exists():raise ValueError('A fold is already sealed')
    ledgers=list(queue.rglob('resource_ledger.jsonl'))
    for p in ledgers:ledger_complete(p)
    truncated={}
    for p in (queue/'folds').glob('*/controls/construction/*/*.try0.response.json'):
        if response_has_cache(p):continue
        raw=load(p)
        if [c.get('finish_reason') for c in raw.get('choices',[])]!=['length']:
            raise ValueError('Unreconciled non-truncation response: '+str(p))
        request=Path(str(p).replace('.response.json','.request.json'))
        truncated[str(p.resolve())]={'response_sha256':sha(p),'request_sha256':sha(request),
            'request_id':raw.get('id'),'old_max_tokens':load(request).get('max_tokens'),'new_max_tokens':PROVIDER_MAX_OUTPUT}
    fixed,prefixes,states=preserved(queue)
    fixed.update(load(queue/'nb-terminal-receipt.json')['raw_inputs'])
    fixed.update(q['external_inputs'])
    revision.mkdir(parents=True)
    shutil.copytree(queue/'code',revision/'code')
    methods=revision/'code/memory/scripts/t2_agent_new_methods.py'
    old=methods.read_text()
    if old.count(RB_OLD)!=1:raise ValueError('Unexpected ReasoningBank parser boundary')
    revised=old.replace(RB_OLD,RB_NEW,1)
    if (Path(__file__).parent/'t2_agent_new_methods.py').read_text()!=revised:
        raise ValueError('Unregistered native-method change beyond optional heading colons')
    methods.write_text(revised)
    entry=revision/'code/memory/scripts'/Path(__file__).name
    shutil.copy2(__file__,entry);shutil.copy2(tests,revision/'offline-tests.xml')
    for p in states:
        target=revision/'prior-state'/p.relative_to(queue);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    save(revision/'protected-artifacts.json',fixed);save(revision/'ledger-prefixes.json',prefixes)
    save(revision/'authorization.json',{'user_text':'修复一下这个问题，然后继续实验',
        'prior_token_policy_authorization':'修复下这个问题，每个记忆机制只是限制调用次数，不用限制上限，然后后台并发启动',
        'user_approved':True,'policy':policy(),'truncated_requests':truncated,'explicit_retry_per_truncated_request':1,
        'amem_context_policy':AMEM_CONTEXT_POLICY,'amem_context_instruction':AMEM_CONTEXT_INSTRUCTION,
        'amem_delta_scope':'unfinished AMEM evolution calls only; no input removal, no implicit retry, prior caches unchanged',
        'no_implicit_retries':True,'no_NB_rerun':True,'reuse_completed_memories':True,
        'online_total_calls_per_episode':3,'construction_calls_separate':True,'prior_costs_preserved':True,
        'NB_policy_difference':'NB used 800 output tokens; retained as recorded; audit actual finish reasons separately'})
    files=F.frozen(revision/'code');files.update(F.frozen(revision/'prior-state'))
    for n in ('offline-tests.xml','protected-artifacts.json','ledger-prefixes.json','authorization.json'):
        files[str(revision/n)]=sha(revision/n)
    save(revision/'registration.json',{'schema':POLICY,'base_queue':str(queue),
        'base_registration_sha256':sha(queue/'queue-registration.json'),'files':files,
        'missing_builds':missing_builds(queue),'online_episodes':64,'concurrency':8,
        'request_policy':policy(),'created':time.time(),
        'constructor_delta':{'file':'t2_agent_new_methods.py','base_sha256':sha(queue/'code/memory/scripts/t2_agent_new_methods.py'),
            'new_sha256':sha(methods),'semantics':'accept optional heading colons; preserve complete field text; no new model request'},
        'previous_controller_revision':load(queue/'dispatch-state.json').get('controller_revision')})
    print(json.dumps({'status':'FROZEN_READY','revision':str(revision),'missing_builds':len(missing_builds(queue)),
        'explicit_truncation_retries':len(truncated),'registration_sha256':sha(revision/'registration.json')}))


def construct(revision,controls,arm):
    install_constructor(revision)
    if arm in ('REME','REASONINGBANK'):
        from t2_agent_new_methods import build
        build(controls,arm)
    else:
        import t2a_construct as C
        C.construct(controls,arm)
    # This file is newly produced, not one of the reused frozen memories. Keep
    # the old metadata for audit without mislabelling the resumed wire policy.
    path=controls/(arm+'.json');data=load(path)
    data['request_policy_revision']={'path':str(revision/'registration.json'),'sha256':sha(revision/'registration.json')}
    data['request_policy']=policy()
    if 'construction_input_bytes_ceiling' in data:
        data['legacy_declared_construction_input_bytes_ceiling']=data['construction_input_bytes_ceiling']
        data['construction_input_bytes_ceiling']=None
    data['reused_cache_policy']='Prior successful responses kept byte-identical; all prior costs included'
    save(path,data)


def run(queue,revision):
    import t2_agent_followup as G
    import t2_nb_followup as F
    if Path(__file__).resolve()!=revision/'code/memory/scripts'/Path(__file__).name:
        raise RuntimeError('Use the frozen resume controller')
    lease=(queue/'supervisor.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    invariant(queue,revision)
    if load(queue/'dispatch-state.json')['status']!='PAUSED_TECHNICAL':raise RuntimeError('Queue no longer paused')
    if list((queue/'folds').glob('*/episodes/*/episode.json')):raise RuntimeError('No duplicate episodes')
    q=load(queue/'queue-registration.json');nb=Path(q['nb'])
    G.configure_reuse()
    os.environ.update(R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'])
    with (revision/'resume-start.json').open('x') as f:json.dump({'started':time.time(),'registration_sha256':sha(revision/'registration.json')},f)
    (revision/'logs').mkdir(exist_ok=False)
    pending=missing_builds(queue);live=[];done=[];failure=None
    try:
        while pending or live:
            while pending and len(live)<4 and not failure:
                item={**pending.pop(0),'started':time.time()};fold=queue/'folds'/item['target'];arm=item['arm']
                log=(revision/'logs'/('build-'+fold.name+'-'+arm+'.log')).open('x')
                cmd=[sys.executable,str(Path(__file__)),'construct','--revision',str(revision),'--controls',str(fold/'controls'),'--arm',arm]
                p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,
                    'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                    'R2G_EMBEDDING_METRICS_FILE':str(fold/'controls/construction'/arm/'reports/embedding-calls.jsonl')})
                live.append((p,log,item))
            for p,log,item in list(live):
                if p.poll() is None:continue
                log.close();live.remove((p,log,item))
                done.append({**item,'returncode':p.returncode,'ended':time.time(),'wall_s':time.time()-item['started']})
                save(revision/'construction-attempts.json',{'attempts':done,'running':[i for p,l,i in live]})
                if p.returncode:failure='Construction failed: '+item['target']+'/'+item['arm']
            save(queue/'dispatch-state.json',{'status':'PAUSED_DRAINING_BUILDERS' if failure else 'RESUMING_MEMORY_CONSTRUCTION',
                'construction_done':done,'construction_pending':len(pending),'running':[{'pid':p.pid,**i} for p,l,i in live],
                'pending':64,'reason':failure,'controller_revision':str(revision),'updated':time.time()})
            if failure and not live:raise RuntimeError(failure)
            if pending or live:time.sleep(5)
        invariant(queue,revision)
        save(revision/'construction-completed.json',{'jobs':done,'legacy_completed_reused':40-len(done),
            'new_policy':policy(),'costs':'all previous and resumed request ledgers retained'})
        install_online()
        registry=load(nb/'origin-registry.json');folds=[queue/'folds'/hid for hid in F.N.IDS]
        for fold in folds:
            entry=fold/'code/memory/scripts'/Path(__file__).name
            if entry.exists():raise RuntimeError('Policy entrypoint already exists')
            shutil.copy2(__file__,entry)
            G.seal(queue,fold,nb,registry)
            reg=load(fold/'registration.json')
            reg['request_policy_revision']={'path':str(revision/'registration.json'),'sha256':sha(revision/'registration.json')}
            reg['online_entrypoint']=str(entry)
            reg['files'][str(revision/'registration.json')]=sha(revision/'registration.json')
            reg['files'].update(load(revision/'registration.json')['files'])
            save(fold/'registration.json',reg)
            import t2a_batch as B
            B.preflight(fold,'TEST')
        jobs=[{'root':str(f),'task':registry[f.name]['task'],'arm':a} for f in folds for a in ARMS]
        if (queue/'frozen-memory-plan.json').exists():raise RuntimeError('Online plan already exists')
        save(queue/'frozen-memory-plan.json',{'jobs':jobs,'registrations':{str(f/'registration.json'):sha(f/'registration.json') for f in folds},
            'request_policy_revision':str(revision/'registration.json'),'NB_reused':8})
        invariant(queue,revision)
        def popen(cmd,**kwargs):
            cmd=list(cmd);script=Path(cmd[4])
            if script.name!='t2a_batch.py' or cmd[5]!='episode':raise RuntimeError('Unexpected online worker command')
            cmd[4]=str(script.with_name(Path(__file__).name))
            return subprocess.Popen(cmd,**kwargs)
        state=F.dispatch(queue,jobs,popen=popen)
        save(revision/'dispatch-state.json',state);invariant(queue,revision)
    except Exception as exc:
        state={'status':'PAUSED_TECHNICAL','reason':str(exc),'controller_revision':str(revision),'updated':time.time()}
        save(queue/'dispatch-state.json',state);save(revision/'dispatch-state.json',state)
        raise


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','run','construct','episode'))
    ap.add_argument('--queue',type=Path);ap.add_argument('--revision',type=Path);ap.add_argument('--tests',type=Path)
    ap.add_argument('--controls',type=Path);ap.add_argument('--arm');ap.add_argument('--root',type=Path)
    ap.add_argument('--task');ap.add_argument('--repeat',type=int,default=1);ap.add_argument('--split',default='TEST')
    a=ap.parse_args()
    if a.command=='prepare':prepare(a.queue.resolve(),a.revision.resolve(),a.tests.resolve())
    elif a.command=='run':run(a.queue.resolve(),a.revision.resolve())
    elif a.command=='construct':construct(a.revision.resolve(),a.controls.resolve(),a.arm)
    else:
        install_online()
        import t2a_batch as B
        reg=load(a.root/'registration.json')
        if str(Path(__file__).resolve())!=reg.get('online_entrypoint'):raise RuntimeError('Wrong policy entrypoint')
        B.episode(a.root.resolve(),a.task,a.arm,a.repeat,a.split)


if __name__=='__main__':main()
