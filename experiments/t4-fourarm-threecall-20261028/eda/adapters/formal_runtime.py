"""Native EDA agents on one shared RTLRepair oracle, with complete metering."""
import argparse
import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess
import resource
import sys
import tempfile
import time
import traceback

from bridge import ROOT,FullMemory
from meter import MeteredProvider,UnknownUsage,OnlineRequestLimit
from provider_clients import flash_key,ScoutClient
from rtlrepair_eval import evaluate,record
from native_serialization import write_episode

OLD=Path('/data1/zhangdy/tehm-experiments/t4-cross-host-online-20261026')


class EpisodeInterrupted(BaseException):pass


def global_stop_roots():
    roots=[ROOT]
    continuation=ROOT/'inputs/continuation.json'
    if continuation.exists():roots.append(Path(json.loads(continuation.read_text())['parent_root']))
    control=ROOT/'inputs/sixarm-control.json'
    if control.exists():roots.append(Path(json.loads(control.read_text())['root']))
    return roots


def source_lineage(task):
    group=task['source_group']
    if group.startswith('sha3_'):return 'rtlrepair:opencores-sha3'
    if group in {'i2c_master','i2c_slave'}:return 'rtlrepair:opencores-i2c'
    if group.startswith('axis-'):return 'rtlrepair:verilog-axis'
    return 'rtlrepair:'+group


class EpisodeProvider(MeteredProvider):
    def request(self,*args,**kwargs):
        if any((root/'reports/GLOBAL-STOP.json').exists() for root in global_stop_roots()):
            raise EpisodeInterrupted('global identity/permission fault; no request sent')
        # Remove the legacy native 2,048-output limit equally for both arms.
        # Provider capacity still applies; a length finish is never a success.
        if args:
            args=({k:v for k,v in args[0].items() if k!='max_tokens'},*args[1:])
        elif 'payload' in kwargs:
            kwargs['payload']={k:v for k,v in kwargs['payload'].items() if k!='max_tokens'}
        try:
            result=super().request(*args,**kwargs)
        except UnknownUsage as exc:
            rows=[json.loads(s) for s in self.journal.path.read_text().splitlines()]
            ended=[r for r in rows if r['event']=='request_ended']
            if ended and ended[-1].get('status') in {401,402,403}:
                for root in global_stop_roots():
                    record(root/'reports/GLOBAL-STOP.json',{'reason':'provider payment failure' if ended[-1]['status']==402 else 'provider permission failure',
                        'status':ended[-1]['status'],'created':time.time()})
            raise EpisodeInterrupted(str(exc)) from exc
        if result['model'] not in {'deepseek-flash','deepseek-v4.1-flash'}:
            for root in global_stop_roots():
                record(root/'reports/GLOBAL-STOP.json',{'reason':'unapproved response model',
                    'model':result['model'],'created':time.time()})
            raise EpisodeInterrupted('provider model identity changed')
        if any(c.get('finish_reason')=='length' for c in result.get('choices',[])):
            raise EpisodeInterrupted('provider_response_truncated; actual usage recorded; no implicit retry')
        return result


def usage(provider):
    rows=[json.loads(s) for s in provider.journal.path.read_text().splitlines()] if provider.journal.path.exists() else []
    ended=[r for r in rows if r['event']=='request_ended']
    known=[r['usage'] for r in ended if isinstance(r.get('usage'),dict)]
    started=sum(r['event']=='request_started' for r in rows)
    unknown=sum(r.get('usage')=='UNKNOWN' for r in ended)
    total=sum(u['total_tokens'] for u in known)
    return {'online_requests':started,'known_tokens':total,
        'known_input_tokens':sum(u['prompt_tokens'] for u in known),
        'known_output_tokens':sum(u['completion_tokens'] for u in known),
        'unknown_requests':unknown,'open_requests':started-len(ended),
        'actual_total_tokens':None if unknown or started!=len(ended) else total,
        'api_cost':None,'api_cost_note':'applicable provider pricing not yet bound; not zero'}


class DatasetExecutor:
    """Only a dataset/evaluator boundary; no native search policy is replaced."""
    def __init__(self,task,episode):
        self.task,self.episode,self.index=task,episode,0
    def evaluate(self,problem,completion,test,timeout=20,compile_only=False):
        self.index+=1
        grade=evaluate(self.task,completion,self.episode/'evaluations'/f'{self.index:04d}',
                       compile_only=bool(compile_only))
        compiler='\n'.join(c.get('compiler_log','') for c in grade['checks'])
        errors=sum(c.get('errors',1 if c['status']!='PASS' else 0) for c in grade['checks'])
        count=sum(c.get('checks',0) for c in grade['checks'])
        feedback={'compiler_log':compiler,
            'test_output':('Mismatches: '+str(errors)+' in '+str(count)+' samples\n'+
                json.dumps([{k:c[k] for k in ('status','reason','first_failures') if k in c} for c in grade['checks']]))
                if not compile_only else '',
            'passed':grade['status']=='PASS','candidate_sha256':grade['candidate_sha256'],
            'verification_status':grade['status']}
        if grade['status']=='UNKNOWN':
            # Stop a technically unmeasurable episode rather than teach a false
            # failure or generate extra candidates against an invalid oracle.
            raise EpisodeInterrupted('RTLRepair verifier UNKNOWN: '+json.dumps(grade['checks']))
        return {'task_id':problem['task_id'],'passed':grade['status']=='PASS','feedback':feedback}


def configure_fixer_agent(agent,transport,item):
    agent.toolkit.initialize(item);agent.reset_logs()
    native=agent.agent_executor.agent
    nested=native.llm if hasattr(native,'llm') else native.llm_chain.llm
    for model in (agent.llm,agent.toolkit.llm,nested):
        model.client=transport;model.max_retries=0
    return agent


def finish_native_sample(task,candidate,episode,final,counter,*,request_limit_reached=False,evaluator=None):
    if not isinstance(candidate,str) or not candidate.strip():
        raise EpisodeInterrupted('no executable candidate at native termination')
    (episode/'submitted.v').write_text(candidate)
    grade=(evaluator or evaluate)(task,candidate,episode/'terminal-verification')
    return {'status':'ACCEPTED' if grade['status']=='PASS' else 'NOT_ACCEPTED' if grade['status']=='FAIL' else 'TECHNICAL_STOP',
        'strict_accepted':grade['status']=='PASS' if grade['status']!='UNKNOWN' else None,
        'native_result':final,'native_evaluations':counter,
        'online_request_limit':3,'online_request_limit_reached':request_limit_reached,
        'native_variant':'one native trajectory; at most three total online requests including reflection/RAG/self-verification; existing native RAG/tools retained; final candidate independently graded without another model call'}


def run_fixer(task,memory,provider,episode):
    sys.path[:0]=[str(ROOT/'sources/rtlfixer/src'),str(ROOT/'sources/verilog-eval')]
    from fixer_runtime import FixerEvidence,FixerTransport
    from generators.agents.rtlfixer import CodeAgent,FixAgent
    from generators.model import GPTChat
    import importlib
    module=importlib.import_module('task.react_fix_simulate')
    item={'task_id':task['task'],'prompt':'','solution':Path(task['buggy']).read_text(),
        'test':'','detail_description':'Repair this faulty RTL implementation. Preserve its module interface and all declared support modules. Use compiler and functional-oracle feedback; no golden RTL is provided.'}
    evidence=FixerEvidence(memory,item)
    original_state=evidence.state
    evidence.state=lambda:dataclasses.replace(original_state(),lineage_id=source_lineage(task))
    executor=evidence.bind(DatasetExecutor(task,episode))
    transport=FixerTransport(provider,memory,evidence)
    os.environ['OPENAI_API_KEY']='table4-transport-placeholder'
    captured=[];agents=[]
    def factory(name,model_name,**kwargs):
        if name=='rtlfixer_code':
            # Keep RTLFixer's native human-guidance RAG enabled in BOTH arms.
            agent=CodeAgent(model_name,executor,max_iters=20,toolset=['compiler','rag'],
                            method='rag',compiler='iverilog')
        else:
            agent=FixAgent(model_name,executor,max_iters=20,toolset=[],method='feedback')
        configure_fixer_agent(agent,transport,item);agents.append(agent)
        if name=='rtlfixer_code':
            original=agent.generate_chat
            def generate(messages,*a,**k):
                choice=memory.consult(evidence.state())
                if choice['mode']=='direct_reuse':
                    local=evidence.try_local(executor,choice)
                    if local:return local['candidate']
                return original(messages,*a,**k)
            agent.generate_chat=generate
        return agent
    module.executor_factory=lambda *a,**k:executor
    module.model_factory=lambda _:GPTChat('deepseek-flash')
    module.agent_factory=factory
    class NativeSampleComplete(BaseException):pass
    def collect_one(path,items,append):
        captured.extend(items)
        # The upstream outer corpus/sampling loop ignores num_samples. We run
        # exactly its first completed trajectory, as the requested 1 repeat.
        # Its inner repair/restart/tool policy and simulation budget are intact.
        raise NativeSampleComplete()
    module.write_jsonl=collect_one
    import pdb
    pdb.set_trace=lambda:None # upstream terminal debugger, not a search step
    limit_reached=False
    try:
        module.react_fix_simulate([item],'deepseek-flash','verilog',10,10,1,
                                  str(episode/'native-output.jsonl'),False,agent_feedback='feedback',num_samples=1)
    except NativeSampleComplete:pass
    except OnlineRequestLimit:
        limit_reached=True
    except module.NativeNoProgress as exc:raise EpisodeInterrupted(str(exc)) from exc
    if limit_reached:
        return finish_native_sample(task,evidence.candidate,episode,
            {'stop_reason':'three total online requests exhausted'},evidence.counter,
            request_limit_reached=True)
    if not captured:raise RuntimeError('native simulation loop produced no terminal sample')
    final=captured[-1]
    candidate=final.get('solution')
    return finish_native_sample(task,candidate,episode,final,evidence.counter)


def finish_scout_sample(task,agent,episode,final,counter,*,request_limit_reached=False,evaluator=None):
    best=agent.best_eval
    if best:
        root=episode/'best_design'
        path=(root/(best.get('design_file') or 'design.v')).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('native best candidate outside best-design directory')
    else:
        path=episode/'workspace/design.v'
    result=finish_native_sample(task,path.read_text(),episode,final,counter,
        request_limit_reached=request_limit_reached,evaluator=evaluator)
    result['native_variant']='native RTLScout ReAct/tools/synthesis; at most three total online requests including summary; best or current candidate independently graded at the limit'
    return result


def run_scout(task,memory,provider,episode):
    sys.path.insert(0,str(ROOT/'sources/rtlscout'))
    from rtlscout.agent import RTLAgent
    from rtlscout.correctness import CorrectnessResult
    from rtlscout.evaluation import EvaluationResult
    import rtlscout.agent as native_module
    from hooks import HostObserver,attach_rtlscout
    from local_reuse import ScoutReuse
    from native_evidence import ScoutEvidence
    workspace=episode/'workspace'
    workspace.mkdir(exist_ok=True)
    (workspace/'design.v').write_text(Path(task['buggy']).read_text())
    # The compiler and oracle use private, declared support sources, not a whole
    # golden tree. Provide read-only knowledge of support module interfaces only.
    for i,source in enumerate(task['sources']):
        if source!=task['reference']:shutil.copy2(source,workspace/f'support_{i:03d}.v')
    (workspace/'README.txt').write_text('Repair design.v; preserve its interface. Support files are fixed. The shared RTLRepair evaluator returns measured functional feedback. Golden RTL and oracle files are not exposed. Optimize synthesis cost only after correctness.\n')
    counter=[0]
    def native_evaluate(workdir,top_module=None,*,cost_metric,design_file=None,**kwargs):
        counter[0]+=1
        path=(Path(workdir)/(design_file or 'design.v')).resolve()
        if not path.is_relative_to(workspace.resolve()) or path.suffix not in {'.v','.sv'}:
            raise RuntimeError('candidate outside registered task workspace')
        grade=evaluate(task,path.read_text(),episode/'evaluations'/f'{counter[0]:04d}')
        if grade['status']=='UNKNOWN':raise EpisodeInterrupted('shared RTLRepair verification UNKNOWN')
        count=sum(c.get('checks',0) for c in grade['checks'])
        errors=sum(c.get('errors',1 if c['status']!='PASS' else 0) for c in grade['checks'])
        correctness=CorrectnessResult(passed=grade['status']=='PASS',lint_ok=not any(c.get('compiler_log') for c in grade['checks']),
            sim_ok=count>0,lint_stdout='',lint_stderr='\n'.join(c.get('compiler_log','') for c in grade['checks']),
            sim_stdout=json.dumps(grade['checks']),sim_stderr='',sim_returncode=0,
            total_checks=max(count,1),passed_checks=max(0,count-errors))
        # Native metric and synthesis flow are retained; no TEHM-only EDA tool.
        cost_folder=workspace/'.tmp'/f'cost-{counter[0]:04d}'
        cost_folder.mkdir(parents=True)
        shutil.copy2(path,cost_folder/'design.v')
        for i,source in enumerate(task['sources']):
            if source!=task['reference']:shutil.copy2(source,cost_folder/f'support_{i:03d}.v')
        for source in Path(task['include_dir']).rglob('*.vh'):
            relative=source.relative_to(task['include_dir']);target=cost_folder/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        cost=cost_metric.evaluate(cost_folder,top_module,design_file=None)
        return EvaluationResult(correctness=correctness,cost=cost,
            passed=correctness.passed and cost.ok,cost_value=cost.value,
            cost_metric_name=cost_metric.metric_name,pass_rate=correctness.pass_rate)
    native_module.evaluate=native_evaluate
    agent=RTLAgent(ScoutClient(provider),workdir=episode,max_steps=20,language='verilog',run_cec=True)
    agent.design_top_module=task['top']
    evidence=ScoutEvidence(memory,agent,task['task'])
    original_state=evidence.state
    evidence.state=lambda:dataclasses.replace(original_state(),lineage_id=source_lineage(task))
    observer=HostObserver(memory,evidence.state,evidence.verify)
    attach_rtlscout(agent,observer);observer.direct_driver=ScoutReuse(agent,observer,evidence)
    # Replace string-prefix path acceptance with an exact workspace boundary.
    original_safe=agent._safe_path
    def safe(name):
        path=original_safe(name)
        return path if path is not None and Path(path).resolve().is_relative_to(workspace.resolve()) else None
    agent._safe_path=safe
    limit=False
    try:
        result=agent.run('Repair the faulty design in design.v using native tools and measured RTLRepair functional feedback. Preserve interfaces and support modules. Then minimize native synthesis cost.',task['task'])
        final=result.to_dict()
    except OnlineRequestLimit:
        limit=True
        final={'stop_reason':'three total online requests exhausted',
               'best_eval':agent.best_eval,'all_evals':agent.all_evals,'best_cost':agent.best_cost}
    return finish_scout_sample(task,agent,episode,final,counter[0],request_limit_reached=limit)


def run(host,arm,instance):
    start=time.monotonic();cpu=time.process_time()
    from formal_queue import validate
    registration=validate()
    if registration.get('schema')=='table4-fourarm-threecall-v1':
        if host not in registration['hosts'] or arm not in registration['arms'] or instance not in registration['task_order']:
            raise ValueError('host/arm/task explicitly excluded from this rerun')
    if registration and registration.get('schema')=='rtlfixer-payment-recovery-round-v1':
        if host!='rtlfixer' or not any(r['task']==instance and r['arm']==arm for r in registration['retry_cells']):
            raise ValueError('episode outside authorized payment recovery cells')
    task=next(t for t in json.loads((ROOT/'inputs/rtlrepair-private.json').read_text()) if t['task']==instance)
    qualification=json.loads((ROOT/'qualification'/instance/'qualification.json').read_text())
    if qualification['status']!='PASS':raise ValueError('task unqualified')
    os.environ['PATH']=str(ROOT/'bin')+':/usr/bin:/bin'
    os.environ['RTLSCOUT_ENV_FILE']=str(ROOT/'inputs/empty.env')
    os.environ['RTLSCOUT_CACHE_DIR']=str(ROOT/'hosts'/host/arm/'cache')
    memory=FullMemory(host,arm=arm,root=ROOT)
    first_sequence=sum(1 for _ in memory.journal.path.open()) if memory.journal.path.exists() else 0
    native_run=subprocess.run
    def observed_run(*args,**kwargs):
        entered=time.monotonic();children=resource.getrusage(resource.RUSAGE_CHILDREN)
        argv=args[0] if args else kwargs.get('args')
        result=None
        try:
            result=native_run(*args,**kwargs)
            return result
        finally:
            after=resource.getrusage(resource.RUSAGE_CHILDREN)
            memory.journal.append('toolchain_process',task=instance,
                executable=str(argv[0]) if isinstance(argv,(list,tuple)) and argv else 'shell',
                wall_s=time.monotonic()-entered,
                children_cpu_s=after.ru_utime+after.ru_stime-children.ru_utime-children.ru_stime,
                returncode=getattr(result,'returncode',None))
    subprocess.run=observed_run
    episode=memory.arm_root/'episodes'/instance;episode.mkdir(exist_ok=False)
    work=episode/'workspace';work.mkdir();(work/'.tmp').mkdir();(work/'error_log').mkdir()
    tempfile.tempdir=str(work/'.tmp');os.environ['TMPDIR']=tempfile.tempdir;os.chdir(work)
    provider=EpisodeProvider(episode/'provider',flash_key())
    terminal={'host':host,'arm':arm,'task':instance,'source_group':task['source_group'],
        'initially_passing':qualification.get('initially_passing',False),
        'dataset':'RTLRepair ASPLOS24 complete 77-variant registry','paper_data':True,
        'runtime':registration.get('runtime_label','formal-eda-v1'),'started':time.time(),'pid':os.getpid(),
        'memory_policy':registration.get('memory_policy','private empty store, full TEHM online write-back; current action cannot read its future outcome') if registration else 'private empty store, full TEHM online write-back; current action cannot read its future outcome'}
    if registration and registration.get('schema')=='rtlfixer-payment-recovery-round-v1':
        source=next(r for r in registration['retry_cells'] if r['task']==instance and r['arm']==arm)
        terminal.update(recovery_round=1,original_terminal=source['original_terminal'],
                        original_terminal_sha256=source['original_terminal_sha256'],
                        first_attempt_replacement=False,runtime='formal-eda-payment-recovery-v1')
    record(episode/'started.json',terminal)
    try:
        terminal.update((run_fixer if host=='rtlfixer' else run_scout)(task,memory,provider,episode))
    except EpisodeInterrupted as exc:
        terminal.update(status='ACCOUNTING_FAILURE' if provider.blocked else 'TECHNICAL_STOP',
                        strict_accepted=None,reason=str(exc))
    except Exception as exc:
        terminal.update(status='TECHNICAL_STOP',strict_accepted=None,reason=str(exc),
                        exception=type(exc).__name__,traceback=traceback.format_exc())
    finally:
        if provider.blocked:terminal['status']='ACCOUNTING_FAILURE';terminal['strict_accepted']=None
        evaluations=[json.loads(p.read_text()) for p in (episode/'evaluations').glob('*/grade.json')]
        if (episode/'terminal-verification/grade.json').exists():
            evaluations.append(json.loads((episode/'terminal-verification/grade.json').read_text()))
        subprocess.run=native_run
        try:memory.close()
        except Exception as exc:
            terminal.update(status='TECHNICAL_STOP',strict_accepted=None,memory_close_error=str(exc))
        events=[json.loads(s) for s in memory.journal.path.read_text().splitlines()][first_sequence:] if memory.journal.path.exists() else []
        terminal['cost']={**usage(provider),'worker_wall_s':time.monotonic()-start,
                          'controller_cpu_s':time.process_time()-cpu,
                          'verifier_tool_calls':sum(v['tool_calls'] for v in evaluations),
                          'toolchain_process_calls':sum(e['event']=='toolchain_process' for e in events),
                          'toolchain_children_cpu_s':sum(e.get('children_cpu_s',0) for e in events if e['event']=='toolchain_process'),
                          'native_agent_tool_calls':sum(e['event']=='native_tool_cost' for e in events),
                          'memory_consults':sum(e['event']=='memory_consult' for e in events),
                          'memory_writes':sum(e['event']=='memory_write' for e in events)}
        previous=None;harmful=0
        for grade in evaluations:
            if grade['scope']=='compile_only':continue
            if previous=='PASS' and grade['status']=='FAIL':harmful+=1
            previous=grade['status']
        terminal['harmful_attempts_observed']=harmful
        terminal['harmful_attempts_definition']='measured functional PASS -> FAIL across candidate evaluations; not all unsuccessful trials'
        terminal['failed_candidate_evaluations']=sum(v['status']=='FAIL' for v in evaluations)
        terminal['ended']=time.time()
        write_episode(episode,terminal)
    return 0 if terminal['status'] in {'ACCEPTED','NOT_ACCEPTED'} else 2


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',choices=['rtlfixer','rtlscout'],required=True)
    p.add_argument('--arm',choices=['native','tehm'],required=True);p.add_argument('--task',required=True)
    a=p.parse_args();raise SystemExit(run(a.host,a.arm,a.task))
