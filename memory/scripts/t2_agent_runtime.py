"""Agent-first R2G host loop using existing checked tools and metered provider.

This is an opt-in runtime, separate from the historical deterministic-first loop.
Native memory adapters are injected; this file does not relabel generic retrieval
as ReMe or ReasoningBank. The first supported launch stage is the NB baseline.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

import t2_agent_dispatch as D
import t2a_runtime as T
import t2a_envelope as E
import t2_agent_verdict as AV
from t2_agent_tools import PublicTools
import t2_agent_journal as J


def instructions(H, platform, manifest):
    knobs=', '.join(H.executable_knobs(manifest))
    return (
        f'You are the physical-design repair agent for R2G/ORFS on {platform}. '
        'Decide your next tool operation from the current reports and prior tool results. '
        'There is no compulsory repair order. You have at most THREE total online model requests, '
        'including any memory helper requests. You may finish earlier. '
        'Return ONE JSON object with operation and arguments. Available operations:\n'
        '1. repair: {"operation":"repair","arguments":{"set":{"KNOB":"value"},'
        '"unset":[],"rerun_from":"synth|floorplan|place|cts|route",'
        '"rationale":"brief justification"}}. Applies your edit, runs the flow and strict checks; '
        'regressions or unverifiable edits are rolled back with charged recovery.\n'
        '2. catalogue: {"operation":"catalogue","arguments":{}}. Calls the public deterministic '
        'R2G repair loop, which may run MULTIPLE flows and checks; all costs count. '
        'Select it only if you want that automatic strategy sequence. No cross-task memory is used by this tool.\n'
        '3. tool: {"operation":"tool","arguments":{"tool_id":"an offered tool_id"}}. '
        'Select ONE public repair from available_deterministic_tools in the observation. '
        'Its rationale, exact edits and rerun stage are shown there; these are existing R2G tools, '
        'available equally in every arm. Use only the currently offered descriptions and parameters. '
        'These descriptions do not guarantee success. Choosing a tool spends this model request, '
        'with no extra LLM call.\n'
        '4. stop: {"operation":"stop","arguments":{"reason":"brief reason"}}. '
        'Finishes with tool-based verification; your claim does not determine success.\n'
        f'Allowed edit knobs: {knobs}. RTL, SDC and clock are immutable. '
        'Respect the full task contract, area limits, knob units and bounds in the observation. '
        'CORE_UTILIZATION is a percentage; PLACE_DENSITY is a separate setting. '
        'Do not invent results or treat historical advice as current-task verification.')


def target_progress(pre,post,target):
    """Conservative observed progress; UNKNOWN is not unchanged or improved.

    This controls advice withdrawal only, not success, admission or execution
    permissions. It uses the same run-bound vectors as the regression checker.
    """
    import math
    key='orfs' if target=='orfs_stage' else target
    a=(pre.get('signals') or {}).get(key) or {}
    b=(post.get('signals') or {}).get(key) or {}
    result={'target':target,'pre':a,'post':b,'improved':None,'basis':'unmeasured'}
    if not a.get('fresh') or not b.get('fresh'):return result
    metric={'drc':'total_violations','route':'total_violations','lvs':'mismatch_count',
            'timing':'wns_ns'}.get(key)
    x,y=a.get(metric),b.get(metric)
    if (metric and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
                       and abs(v)<1e30 for v in (x,y))):
        if key=='timing' and 'unconstrained' in (a.get('tier'),b.get('tier')):return result
        result.update(improved=y>x if key=='timing' else y<x,basis=metric)
    elif key=='orfs' and a.get('status') in ('fail','partial') and b.get('status') in ('complete','pass'):
        result.update(improved=True,basis='flow_completion')
    return result


def decode(content):
    try:
        value=json.loads(content)
    except (ValueError,TypeError):
        return None,'malformed_json'
    if not isinstance(value,dict):return None,'decision_must_be_a_JSON_object'
    if value.get('type')=='json_object':value={k:v for k,v in value.items() if k!='type'}
    if set(value)!={'operation','arguments'}:
        return None,'invalid_top_level_fields: expected operation,arguments; unexpected='+','.join(sorted(set(value)-{'operation','arguments'}))
    op,args=value['operation'],value['arguments']
    if op not in ('repair','catalogue','stop','tool') or not isinstance(args,dict):
        return None,'unknown_tool_or_arguments'
    if op=='catalogue' and args:
        return None,'catalogue_takes_no_arguments'
    if op=='stop' and (set(args)!={'reason'} or not isinstance(args['reason'],str)):
        return None,'malformed_stop'
    if op=='tool' and (set(args)!={'tool_id'} or not isinstance(args['tool_id'],str) or not args['tool_id']):
        return None,'tool_requires_one_offered_tool_id'
    if op=='repair':
        unknown=set(args)-{'set','unset','rerun_from','rationale'}
        if unknown:return None,'unknown_repair_fields:'+','.join(sorted(unknown))
        if (not isinstance(args.get('set',{}),dict) or not isinstance(args.get('unset',[]),list)
                or args.get('rerun_from') not in ('synth','floorplan','place','cts','route')):
            return None,'repair_requires_set_object_unset_array_and_valid_rerun_from'
    return value,None


def execute_candidate(H,project,env,manifest,content,usage,sequence,failed_executions,
                      native_apply=None,component=None,require_full_acceptance=False):
    """Same knob, full-check, rollback and recovery boundaries as strict_turns."""
    sys.path.insert(0,str(H.SKILL/'knowledge'))
    import result_vector as V
    check,_,_=H._task_failure(project)
    state=T.snapshot_state(project,sequence,env=env)
    pre=V.capture(project)
    component=component or ('memory' if native_apply else 'llm_repair')
    entry=J.begin(H,project,sequence,component,content)
    if native_apply:
        try:stage,applied=native_apply()
        except Exception:
            T.restore(project,state)
            raise
    else:stage,applied=H.apply_llm(project,content,turn=usage.get('repair_turn_id'),manifest=manifest)
    row={'sequence':sequence,'turn':sequence,'repair_turn_id':usage.get('repair_turn_id'),'usage':usage,
         'proposal':content,'flow_launched':False,'rolled_back':False}
    if stage is None:
        return {**row,'status':'REJECTED_PROPOSAL','reason':applied}
    allowed=E.check_config(manifest,E.effective_config((project/'constraints/config.mk').read_text()),
                           project=project,env=env)
    if not allowed['ok']:
        T.restore(project,state)
        return {**row,'status':'CONSTRAINT_REJECTION','rolled_back':True,'violations':allowed['violations']}
    identity=T.candidate_execution_identity(manifest,state,project,stage,pre)
    if identity is not None and identity in failed_executions:
        T.restore(project,state)
        return {**row,'status':'DUPLICATE_NOT_EXECUTED','rolled_back':True,
                'duplicate_of':failed_executions[identity]}
    flow_env={**env,'R2G_BUDGET_COMPONENT':component,'FROM_STAGE':stage,'R2G_ACTION_ID':entry['action_id']}
    rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],flow_env,project/'eval_arm.log',28800)
    row.update(flow_launched=rc!=75,flow_rc=rc,applied=applied)
    if rc in (75,76,-9,124,137):
        row.update(status='SAFETY_OR_ACCOUNTING_STOP',rolled_back=True)
        J.finish(project,entry,row,stage,component)
        T.restore(project,state,execution_changed=rc!=75)
        T.event(H,project,'repair_candidate',{**row,'status':'SAFETY_OR_ACCOUNTING_STOP','rolled_back':True})
        raise D.IntegrityStop('Candidate tool was technically terminated')
    H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],
          env,project/'eval_arm.log',600)
    verdict=H.final_verdict(project,{**env,'R2G_VERIFICATION_PURPOSE':'repair'},project/'eval_arm.log')
    verdict=AV.attach_failure(project,env,verdict)
    known_native=bool(verdict.get('native_failure_receipt'))
    post=V.capture(project)
    comparison=V.compare(pre,post,check)
    unverified=not known_native and bool(comparison['unknowns'] or (verdict.get('acceptance') or {}).get('unknown')
                    or (verdict.get('envelope') or {}).get('status')=='UNVERIFIED')
    reject=bool(rc or comparison['regressions'] or unverified or (verdict.get('envelope') or {}).get('status')=='FAIL'
                or (require_full_acceptance and not verdict.get('accepted')))
    row.update(comparison=comparison,target_progress=target_progress(pre,post,check),verdict=verdict,status='FAILED_NATIVE' if known_native else 'UNVERIFIED' if unverified else
               'REGRESSION' if comparison['regressions'] else 'FAILED' if reject else 'RETAINED')
    row['rolled_back']=reject
    J.finish(project,entry,row,stage,component)
    if identity is not None and rc==0 and reject and not unverified and T.terminal_status(verdict)=='NOT_ACCEPTED':
        failed_executions[identity]=sequence
    if reject:
        T.restore(project,state,execution_changed=True)
        row['rolled_back']=True
    return row


def run(H,project,arm,env,key,initial,mark_engineer_restore,memory=None):
    if arm not in D.ARMS:
        raise ValueError('Unregistered arm')
    if arm!='NB' and memory is None:
        raise D.IntegrityStop('Memory arm has no native policy adapter')
    project=Path(project)
    manifest=json.loads(Path(env['R2G_TASK_MANIFEST']).read_text())
    gateway=D.OnlineGateway(H,project,key)
    public_tools=PublicTools(H,project,env)
    transcript=[];records=[];last=initial;failed={}
    stop_reason='initially_accepted' if initial.get('accepted') else None
    while not last.get('accepted'):
        recovered=T.recover_execution(H,project,env)
        if recovered is not None:
            if not T.repair_continuation_verified(recovered):
                raise D.IntegrityStop('Recovery is not verified')
            last=recovered
            if last.get('accepted'):stop_reason='accepted_after_recovery';break
        # Optional native deterministic reuse. Never insert the public catalogue.
        # The selected candidate is re-bound by its native executor before edits;
        # it receives the SAME verification/rollback boundary as model edits.
        if memory is not None and hasattr(memory,'native_action'):
            action=memory.native_action()
            if action is not None:
                sequence=len(records)+1
                trial_options=({'require_full_acceptance':True}
                               if getattr(memory,'require_native_acceptance',False) else {})
                result=execute_candidate(H,project,env,manifest,json.dumps(action),{},sequence,failed,
                                         native_apply=lambda:memory.apply_native(action),**trial_options)
                memory.native_outcome(action,result)
                row={'sequence':sequence,'status':result['status'],'native_memory':action,'execution':result}
                T.event(H,project,'repair_route',{'choice':'local_experience','arm':arm,
                    'candidate':action['id'],'model_requests_used':3-gateway.remaining(),
                    'evidence_authority':'native_candidate_gates; fresh target verification required'})
                records.append(row);T.event(H,project,'memory_candidate',row)
                if not result['rolled_back'] and result.get('verdict'):last=result['verdict']
                transcript.append({'role':'user','content':json.dumps(row,sort_keys=True)})
                continue
        if gateway.remaining()==0:
            stop_reason='online_allowance_spent';break
        observation=H.observation(project)
        observation['task_contract']=T.public_task_contract(manifest)
        observation['available_deterministic_tools']=public_tools.discover()
        runs=sorted((project/'backend').glob('RUN_*'))
        if runs:observation['task_quality_feedback']=T.envelope_feedback(E.envelope(manifest,project,runs[-1],env))
        observation['online_requests_remaining']=gateway.remaining()
        context=''
        if memory is not None:
            # The method must implement this policy boundary. An old PromptMemory
            # or a stub is intentionally not accepted as a complete native adapter.
            try:context=memory.agent_context(observation,gateway)
            except D.OnlineAllowanceSpent:stop_reason='online_allowance_spent';break
            if arm=='TM0':
                observation['experience_mode']='experience_assisted' if context else 'no_experience'
                if not context:
                    observation['experience_instruction']=(
                        'Solve independently from current reports and task-local feedback. '
                        'Do not persist with prior cross-task advice. All common tools remain available.')
        messages=[{'role':'system','content':instructions(H,H.CFG['platform'],manifest)},*transcript,
                  {'role':'user','content':'Current evidence:\n'+json.dumps(observation,sort_keys=True)+
                   ('\nSelected historical memory:\n'+context if context else '')}]
        sequence=len(records)+1
        T.event(H,project,'repair_route',{'choice':'online_llm','arm':arm,
            'reason':'agent chooses next operation; historical advice is optional and adaptable',
            'experience_present':bool(context),
            'requests_remaining':gateway.remaining()})
        try:
            if arm=='ADAMEM':
                # Native three-stage decision, using exactly the SAME gateway.
                # All other arms retain the existing single-decision boundary.
                content,usage=memory.decide(messages,gateway,project/f'agent-decision-{sequence}')
            else:
                content,usage=gateway.call(messages,project/f'agent-decision-{sequence}',
                                           purpose='planning',candidate_generating=True)
        except D.OnlineAllowanceSpent:stop_reason='online_allowance_spent';break
        if memory is not None and hasattr(memory,'consume'):
            memory.consume(messages,project/f'agent-decision-{sequence}')
        decision,error=decode(content)
        row={'sequence':sequence,'usage':usage,'response':content,'decision':decision}
        transcript.append({'role':'assistant','content':content})
        if error:
            row.update(status='REJECTED_DECISION',reason=error)
        elif decision['operation']=='stop':
            row.update(status='AGENT_STOP',reason=decision['arguments']['reason'])
            records.append(row);T.event(H,project,'agent_decision',row)
            stop_reason='agent_stop';break
        elif decision['operation']=='catalogue':
            T.event(H,project,'repair_route',{'choice':'deterministic_tool','arm':arm,'tool':'catalogue'})
            T.event(H,project,'agent_tool_selected',{'sequence':sequence,'tool':'catalogue','arm':arm})
            clean=dict(env)
            for name in ('TEHM_DB','TEHM_ARTIFACTS_ROOT','R2G_RECIPE_FILE','R2G_TEHM_V2_POLICY','R2G_TEHM_PRIORITY_FILE'):
                clean.pop(name,None)
            clean.update(R2G_MEMORY_BACKEND='none',R2G_BUDGET_COMPONENT='catalogue',
                         R2G_ENGINEER_TIMEOUT_S='28800')
            sys.path.insert(0,str(H.SKILL/'knowledge'))
            import result_vector as V
            pre=V.capture(project);target=H._task_failure(project)[0]
            last=H.run_engineer(project,'NB',clean)
            if last.get('loop_rc') in (75,76,-9,124,137):
                T.event(H,project,'agent_tool_technical_stop',{'tool':'catalogue','loop_rc':last['loop_rc']})
                raise D.IntegrityStop('Catalogue tool exceeded a safety/accounting boundary')
            T.raise_on_technical_failure(project)
            mark_engineer_restore(project)
            restored=T.recover_execution(H,project,clean)
            if restored is not None:
                if not T.repair_continuation_verified(restored):raise D.IntegrityStop('Catalogue recovery unverified')
                last=restored
            row.update(status='CATALOGUE_RETURNED',verdict=last,
                       target_progress=target_progress(pre,V.capture(project),target))
        elif decision['operation']=='tool':
            ident=decision['arguments']['tool_id']
            if ident not in public_tools.offered:
                row.update(status='REJECTED_DECISION',reason='tool_id_not_offered_for_current_state')
            else:
                T.event(H,project,'repair_route',{'choice':'deterministic_tool','arm':arm,'tool':ident})
                result=execute_candidate(H,project,env,manifest,json.dumps(decision['arguments']),usage,
                    sequence,failed,native_apply=lambda:public_tools.apply(ident),component='catalogue')
                T.event(H,project,'repair_candidate',result)
                row.update(status=result['status'],execution=result)
                if not result['rolled_back'] and result.get('verdict'):last=result['verdict']
        else:
            T.event(H,project,'agent_tool_selected',{'sequence':sequence,'tool':'repair','arm':arm})
            result=execute_candidate(H,project,env,manifest,json.dumps(decision['arguments']),usage,sequence,failed)
            T.event(H,project,'repair_candidate',result)
            row.update(status=result['status'],execution=result)
            if not result['rolled_back'] and result.get('verdict'):last=result['verdict']
        if memory is not None and hasattr(memory,'observe_decision'):
            memory.observe_decision(row)
        records.append(row);T.event(H,project,'agent_decision',row)
        # Preserve task-local feedback; no external-memory writeback occurs here.
        transcript.append({'role':'user','content':json.dumps(row,sort_keys=True)})
    return {'decisions':records,'stop_reason':stop_reason or 'accepted','last':last,
            'scheduler':D.VERSION,'online_request_cap':3}
