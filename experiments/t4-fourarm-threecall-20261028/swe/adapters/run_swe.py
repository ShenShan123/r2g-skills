"""One registered SWE20 episode. Never retries or overwrites an episode."""
from __future__ import annotations
import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
PREP=Path('/data1/zhangdy/tehm-experiments/t4-swe-agent-adaptation-20261026')
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path.insert(0,str(PREP/'sources/SWE-agent'))
from bridge import FullMemory, Verification, digest, stable
from host import SoftwareState, build_agent
from local_sandbox import native_environment
from meter import MeteredProvider, UnknownUsage, OnlineRequestLimit
from provision import record, eval_worktree
from admission import active_registration, admission_blockers
from sweagent.agent.problem_statement import TextProblemStatement


def flash_key(path='/data1/zhangdy/.llm_env'):
    blocks,current=[],{}
    for line in Path(path).read_text().splitlines():
        m=re.match(r"\s*export\s+(DEEPSEEK_[A-Z_]+)=(.*)$",line)
        if not m:
            continue
        name,value=m[1],m[2].strip().strip('"').strip("'")
        if name=='DEEPSEEK_API_KEY' and current:
            blocks.append(current);current={}
        current[name]=value
    if current:
        blocks.append(current)
    found=[b['DEEPSEEK_API_KEY'] for b in blocks
           if b.get('DEEPSEEK_MODEL')=='deepseek-v4.1-flash' and b.get('DEEPSEEK_API_KEY')]
    if len(found)!=1:
        raise ValueError('exactly one approved Flash credential block required')
    return found[0]


class EpisodeInterrupted(BaseException):
    pass


def global_stop_roots():
    roots=[ROOT]
    control=ROOT/'inputs/sixarm-control.json'
    if control.exists():roots.append(Path(json.loads(control.read_text())['root']))
    return roots


class BatchProvider(MeteredProvider):
    def request(self,payload,**kwargs):
        if any((r/'reports/GLOBAL-STOP.json').exists() for r in global_stop_roots()):
            self.journal.append('request_refused',reason='global identity/payment/permission fault',sent=False)
            raise EpisodeInterrupted('global identity/payment/permission fault; no request sent')
        payload={k:v for k,v in payload.items() if k!='max_tokens'}
        try:
            result=super().request(payload,**kwargs)
        except UnknownUsage as exc:
            rows=[json.loads(s) for s in self.journal.path.read_text().splitlines()]
            ended=[r for r in rows if r.get('event')=='request_ended' and r.get('usage')=='UNKNOWN']
            if ended:
                record(ROOT/'reports/unknown-incidents'/(ended[-1]['request_local_id']+'.json'),
                    {'source':str(self.episode),'event':ended[-1],'actual_usage':'UNKNOWN','retry':False})
                if ended[-1].get('status') in {401,402,403}:
                    for root in global_stop_roots():
                        record(root/'reports/GLOBAL-STOP.json',{'reason':'provider authorization/payment failure',
                            'status':ended[-1]['status'],'actual_usage':'UNKNOWN','created':time.time()})
            raise EpisodeInterrupted(str(exc)) from exc
        if result.get('model') not in {'deepseek-flash','deepseek-v4.1-flash'}:
            for root in global_stop_roots():
                record(root/'reports/GLOBAL-STOP.json',{'reason':'unapproved response model',
                    'provider_model':result.get('model'),'source':str(self.episode),
                    'actual_usage':'known in request ledger','created':time.time()})
            raise EpisodeInterrupted('provider returned an unapproved model; usage retained, stop')
        if any(c.get('finish_reason')=='length' for c in result.get('choices',[])):
            raise EpisodeInterrupted('provider_response_truncated; actual usage retained; no retry')
        return result


def collect_usage(provider):
    rows=[json.loads(x) for x in provider.journal.path.read_text().splitlines()]
    started=[r for r in rows if r['event']=='request_started']
    ended=[r for r in rows if r['event']=='request_ended']
    unknown=[r for r in ended if r.get('usage')=='UNKNOWN']
    known=[r['usage'] for r in ended if isinstance(r.get('usage'),dict)]
    open_count=len(started)-len(ended)
    return {'requests':len(started),'known_tokens':sum(r['total_tokens'] for r in known),
            'known_input_tokens':sum(r['prompt_tokens'] for r in known),
            'known_output_tokens':sum(r['completion_tokens'] for r in known),
            'unknown_requests':len(unknown),'open_requests':open_count,
            'actual_total_tokens':None if unknown or open_count else sum(r['total_tokens'] for r in known),
            'api_cost':None,'api_cost_note':'not zero; monetary pricing not yet bound'}


def native_submission(agent):
    patch=agent.info.get('submission')
    if not isinstance(patch,str):
        raise ValueError('native submission is not a raw patch string')
    return patch


def patch_at_request_limit(repo):
    # Match the native submit tool's inclusion of newly created files. The
    # repository is an episode-private initial copy, with no private test patch.
    subprocess.run(['git','-C',str(repo),'add','-N','--','.'],check=True,capture_output=True)
    return subprocess.check_output(['git','-C',str(repo),'diff','--binary','HEAD'],text=True)


def check_freeze():
    registration=active_registration(ROOT)
    if not registration['launch_authorized']:
        raise ValueError('launch not authorized')
    if any((r/'reports/GLOBAL-STOP.json').exists() for r in global_stop_roots()):
        raise ValueError('global stop')
    for rel,h in registration['files'].items():
        if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=h:
            raise ValueError('registered file changed: '+rel)
    for filename,h in registration.get('external_files',{}).items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest()!=h:
            raise ValueError('registered native file changed: '+filename)
    upstream=PREP/'sources/SWE-agent'
    if subprocess.check_output(['git','-C',str(upstream),'rev-parse','HEAD'],text=True).strip()!=registration['native_commit']:
        raise ValueError('native upstream changed')
    if subprocess.check_output(['git','-C',str(upstream),'status','--porcelain'],text=True).strip():
        raise ValueError('native upstream dirty')
    return registration


def final_write(memory,task,before,after,patch,grade,episode,grade_path=None):
    """Only terminal verification can label the task-level patch positive.

    No reference patch, hidden test contents or hidden expected outputs are
    written into model-visible state or experience action text.
    """
    action={'domain':'software.GIT_PATCH','transformation_family':'native_patch',
            'payload':{'git_patch':patch}}
    verdict=grade['status']
    path=episode/'terminal-verification.json'
    complete=verdict in {'PASS','FAIL'}
    data={'producer':'native_evaluator','verdict':verdict,'oracle_type':'REGRESSION',
          'scope':'swe-bench-verified:local-reproduction:terminal',
          'complete':complete,'before_measured':True,
          'original_failure_removed':verdict=='PASS' if complete else None,
          'regressions':[], 'before_digest':digest(before.canonical()),
          'after_digest':digest(after.canonical()),'action_digest':digest(action),
          'raw':{'grade_receipt':str(grade_path or episode/'evaluation/grade.json'),
                 'official_docker_execution':False,'terminal_only':True}}
    path.write_text(stable(data)+'\n')
    verification=Verification(verdict,'REGRESSION',data['scope'],complete,str(path),
        hashlib.sha256(path.read_bytes()).hexdigest(),True,data['original_failure_removed'])
    # No false assertion that a failing whole task introduced a new regression;
    # complete P2P failures are recorded separately in the final grade receipt.
    if grade.get('report'):
        regressions=grade['report'].get('PASS_TO_PASS',{}).get('failure',[])
        data['regressions']=regressions
        path.write_text(stable(data)+'\n')
        verification=dataclasses.replace(verification,regressions=tuple(regressions),
                    evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return memory.write(before,action,after,verification,rolled_back=False)


def run(instance,arm):
    import yaml
    check_freeze()
    task=next(t for t in json.loads((ROOT/'inputs/public-tasks.json').read_text())['public_tasks']
              if t['instance_id']==instance)
    private=next(t for t in json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
                 if t['instance_id']==instance)
    qualification=json.loads((ROOT/'qualification'/instance/'qualification.json').read_text())
    if qualification['status']!='PASS' or qualification['base_commit']!=task['base_commit']:
        raise ValueError('task environment has not qualified')
    qroot=ROOT/'qualification'/instance
    fallback='-r3' if qualification.get('previous_receipt')=='qualification-before-runtime-pythonpath.json' else ''
    for label,wanted in [('baseline','FAIL'),('reference','PASS')]:
        path=Path(qualification.get(label+'_grade',str(qroot/(label+'-eval'+fallback)/'grade.json')))
        grade=json.loads(path.read_text())
        output=path.parent/'test-output.txt'
        if (grade['status']!=wanted or grade['instance_id']!=instance
                or hashlib.sha256(output.read_bytes()).hexdigest()!=grade['test_output_sha256']
                or (label=='reference' and grade['returncode']!=0)):
            raise ValueError('qualification test proof absent, stale or inconsistent: '+label)
    memory=FullMemory('swe-agent',arm=arm,root=ROOT)
    episode=memory.arm_root/'episodes'/instance
    episode.mkdir(exist_ok=False)
    record(episode/'started.json',{'task':instance,'arm':arm,'started':time.time(),
                                  'pid':os.getpid(),
                                  'paper_data':True,'evaluation':'custom local reproduction subset'})
    provider=BatchProvider(episode/'provider',flash_key())
    started,cpu=time.monotonic(),time.process_time()
    env=None
    status='TECHNICAL_STOP'
    result={'task':instance,'arm':arm,'paper_data':True,'strict_accepted':None,
            'initial_memory':'empty private store, chronological online write-back',
            'harmful_attempts':None,'harmful_attempts_note':'step-level harm not fully verified; not zero',
            'evaluation':'local reproduction using pinned SWE-bench v4.1.0 parsers; not official Docker score'}
    try:
        if arm=='tehm':
            result['task_snapshot']=memory.bind_task_snapshot(episode)
        repo=episode/'repo'
        shutil.copytree(Path(qualification['repo']),repo,symlinks=True)
        home=episode/'home';home.mkdir()
        env=native_environment(repo,home,qualification['environment_prefix'],episode/'runtime.log')
        env.start()
        state=SoftwareState(memory,task,lambda:subprocess.check_output(
            ['git','-C',str(repo),'diff','--binary','HEAD'],text=True))
        state.check={'status':'FAIL','scope':'qualified baseline; hidden test details not exposed'}
        entry=state()
        config=yaml.safe_load((ROOT/f'inputs/{arm}.yaml').read_text())['agent']
        from public_verifier import PublicDirectDriver
        public_verifier_task={**task,'version':private['version']}
        agent=build_agent(config,memory,provider,state,direct_driver_factory=(
            lambda a,m,s:PublicDirectDriver(a,m,s,public_verifier_task)) if arm=='tehm' else None)
        statement=TextProblemStatement(text=task['problem_statement'],id=instance)
        # setup() installs native tools and verifies their presence before the
        # first paid call; run the upstream step/save loop unchanged afterwards.
        agent.setup(env=env,problem_statement=statement,output_dir=episode/'trajectory')
        record(episode/'native-setup.json',{'status':'PASS','paid_requests_before_setup':0})
        agent._chook.on_run_start()
        limit=False
        try:
            while True:
                step=agent.step()
                agent.save_trajectory()
                if step.done:
                    break
            agent._chook.on_run_done(trajectory=agent.trajectory,info=agent.info)
        except OnlineRequestLimit:
            limit=True
            agent.save_trajectory()
        result.update(native_exit_status='online_request_limit' if limit else agent.info.get('exit_status'),
                      native_steps=len(agent.trajectory),online_request_limit=3,
                      online_request_limit_reached=limit)
        # The native submit tool writes raw patch bytes. PTY observations are
        # escaped display strings, not an artifact serialization interface.
        patch=patch_at_request_limit(repo) if limit else native_submission(agent)
        (episode/'submitted.patch').write_text(patch)
        terminal=state()
        env.close();env=None
        if provider.blocked:
            status='ACCOUNTING_FAILURE'
        elif not limit and agent.info.get('exit_status')!='submitted':
            status='TECHNICAL_STOP'
            result['reason']='native agent ended without a verified submission'
        else:
            grade=eval_worktree(private,Path(qualification['repo']),home,
                               Path(qualification['environment_prefix']),episode/'evaluation',patch)
            result['terminal_verifier_runs']=1
            result['terminal_verifier_wall_s']=grade.get('wall_s')
            status='ACCEPTED' if grade['status']=='PASS' else 'NOT_ACCEPTED' if grade['status']=='FAIL' else 'VERIFICATION_UNKNOWN'
            result['strict_accepted']=grade.get('resolved') if grade['status']!='UNKNOWN' else None
            if arm=='tehm':
                terminal=dataclasses.replace(terminal,reports={'tests':{
                    'status':grade['status'],'scope':'terminal independently verified issue tests'}})
                result['terminal_memory_write']=final_write(memory,task,entry,terminal,patch,grade,episode)
        events=[json.loads(s) for s in memory.journal.path.read_text().splitlines()]
        tools=[s for s in events if s['event']=='native_tool_cost' and s.get('task')==instance]
        result['tool_calls']=len(tools)
        result['tool_wall_s']=sum(s.get('wall_s') or 0 for s in tools)
        result['memory_consults']=sum(s['event']=='memory_consult' and s.get('task')==instance for s in events)
        result['memory_selected']=sum(s['event']=='memory_consumption' and s.get('task')==instance
                                      and bool(s.get('selected_ids')) for s in events)
    except (EpisodeInterrupted,Exception) as exc:
        status='ACCOUNTING_FAILURE' if provider.blocked else 'TECHNICAL_STOP'
        result.update(reason=str(exc),exception=type(exc).__name__)
    finally:
        if env is not None:
            try:
                env.close()
            except Exception as exc:
                result['cleanup_error']=str(exc)
                status='TECHNICAL_STOP'
        # Account for native tools even when artifact import or grading fails.
        if memory.journal.path.exists():
            events=[json.loads(s) for s in memory.journal.path.read_text().splitlines()]
            tools=[s for s in events if s['event']=='native_tool_cost' and s.get('task')==instance]
            result['tool_calls']=len(tools)
            result['tool_wall_s']=sum(s.get('wall_s') or 0 for s in tools)
        memory.close()
        usage=collect_usage(provider) if provider.journal.path.exists() else {
            'requests':0,'known_tokens':0,'unknown_requests':0,'open_requests':0,'actual_total_tokens':0}
        result.update(status=status,wall_s=time.monotonic()-started,
                      controller_cpu_s=time.process_time()-cpu,usage=usage,finished=time.time())
        record(episode/'result.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--arm',choices=['native','tehm'],required=True)
    a=p.parse_args(); print(json.dumps(run(a.task,a.arm),indent=2))
