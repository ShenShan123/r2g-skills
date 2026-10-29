"""Native memory policies at the agent-selected host boundary.

No provider client lives here. A-MEM's keyword helper spends the SAME online
allowance as the agent. Every context has a request-bound consumption receipt.
TEHM's legacy policy supplies bounded advice. A separately registered opt-in
policy also permits evidence-gated local procedural trials, not hit-based replay.
RECIPE retains its explicitly declared exact-replay policy. Public catalogue
repair is never implicitly inserted.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import resource
import sys
import time

import t2a_adapters as A
import t2a_runtime as T
import t2a_envelope as E
from t2_agent_dispatch import IntegrityStop
from t2_agent_new_methods import NativeMemory
from t2_tehm_agent_memory import AgentMemory, digest, verify_request

TM0_POLICY='tehm-adaptive-advice-withdrawal-v1'
TM0_THREE_WAY_POLICY='tehm-three-way-evidence-gated-v1'


class Memory:
    def __init__(self,H,project,arm,root,env):
        self.H,self.project,self.arm,self.root,self.env=H,Path(project),arm,Path(root),env
        self.helpers=0;self.used=[];self.native_failures=0;self.receipt=None;self.route_state=None
        self.experience_disabled=False;self.fallback_reason=None;self.guidance_active=False
        self.local_selection=None
        registration=Path(root)/'registration.json'
        self.execution_policy=(json.loads(registration.read_text()).get('tehm_consumer_policy',TM0_POLICY)
                               if registration.exists() else TM0_POLICY)
        if arm=='TM0' and self.execution_policy not in (TM0_POLICY,TM0_THREE_WAY_POLICY):
            raise IntegrityStop('Unregistered TEHM execution policy')
        path=H.SKILL/'knowledge/situation.py'
        spec=importlib.util.spec_from_file_location('agent9_situation',path)
        self.situation=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.situation)
        if arm=='TM0':self.inner=AgentMemory(project,root/'agent-store/agent-construction.json')
        elif arm=='RECIPE':self.inner=None
        elif arm in ('REME','REASONINGBANK'):self.inner=NativeMemory(arm,root/'controls')
        elif arm in ('RET','EXPEL','AWM','AMEM'):self.inner=A.PromptMemory(arm,root/'controls')
        else:raise IntegrityStop('Missing memory adapter: '+arm)

    def current_situation(self):
        check,vclass,count=self.H._task_failure(self.project)
        return self.situation.from_project(self.project,check,vclass,count)

    def agent_context(self,observation,gateway):
        start,cpu=time.time(),time.process_time()
        query=A.stable(observation);sit=self.current_situation();ids=[]
        def keywords(text):
            self.helpers+=1
            content,usage=gateway.call([
                {'role':'system','content':'Extract retrieval keywords only. Return {"keywords":["..."]}. Do not propose repair actions.'},
                {'role':'user','content':text}], self.project/f'amem-query-{self.helpers}',purpose='query')
            try:answer=json.loads(content)
            except (ValueError,TypeError):answer={}
            values=answer.get('keywords') if isinstance(answer,dict) else None
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values):
                # Malformed auxiliary response consumes a call; no extra generation.
                T.event(self.H,self.project,'memory_helper_invalid',{'arm':self.arm,'usage':usage})
                return []
            return values
        if self.arm=='RECIPE':text='No remaining exact verified recipe. Select a tool from current evidence.'
        elif self.arm=='TM0':text,ids=self.adaptive_context(query,sit)
        elif self.arm in ('REME','REASONINGBANK'):text,ids=self.inner.context(query)
        else:text,ids=self.inner.context(query,sit,auxiliary=keywords,max_bytes=None)
        self.receipt={'arm':self.arm,'selected_ids':ids,'context_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'context_bytes':len(text.encode()),'shared_units_sha256':json.loads((self.root/'shared/manifest.json').read_text())['units_sha256'],
            'write_back':False,'wall_s':time.time()-start,'cpu_s':time.process_time()-cpu}
        if self.arm=='TM0':
            self.receipt.update(native_route=self.inner.last_receipt,
                consumer_policy=self.execution_policy if hasattr(self,'execution_policy') else TM0_POLICY,
                mode='experience_assisted' if self.guidance_active else 'no_experience',
                fallback_reason=self.fallback_reason,automatic_execution=False)
        T.event(self.H,self.project,'memory_agent_context',self.receipt)
        return text

    def route_knowledge(self,query,sit):
        text,ids=self.inner.context(query,sit)
        native=self.inner.last_receipt['decision']
        self.route_state={'knowledge_route':native['decision'],
            'no_skill_reason':native.get('no_skill_reason'),
            'risk':native.get('risk') or {},
            'reasons':native.get('abstain_reasons') or [],
            'selected_path_ids':native.get('selected_path_ids') or [],
            'selected_rule_ids':native.get('selected_rule_ids') or [],
            'selected_asset_ids':native.get('selected_asset_ids') or [],
            'selected_context_bytes':len(text.encode()),'write_back':False,
            'automatic_causal_promotion':False}
        T.event(self.H,self.project,'memory_availability',self.route_state)
        return text,ids

    def adaptive_context(self,query,sit):
        """Existing native gates select advice; the agent may adapt or ignore it.

        Trial-admissible procedural evidence is not validated causal knowledge.
        Keep these two channels labelled separately and never promote either
        because it happens to be useful to this prompt.
        """
        self.guidance_active=False
        if self.experience_disabled:
            T.event(self.H,self.project,'memory_execution_choice',{
                'choice':'no_experience_online','reason':self.fallback_reason,
                'task_feedback_retained':True,'extra_requests':0})
            return '',[]
        text,ids=self.route_knowledge(query,sit)
        route=self.route_state['knowledge_route']
        if route in ('INAPPLICABLE','ABSTAIN'):
            return '',[]
        if (getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY
                and self.procedure_route_blocked()):
            return '',[]
        blocks=[];selected=list(ids)
        if route in ('CONSIDER','APPLY') and ids:
            blocks.append({'kind':'validated_mechanism_knowledge','content':text})
        # Listing does not apply a rule. Native admission, typed matching, veto,
        # source isolation and the current task contract still run in diagnose.
        plan=self.diagnose()
        decisions={d['id']:d for d in (plan or {}).get('tehm_v2',{}).get('decisions',[]) if 'id' in d}
        strategies=(plan or {}).get('strategies') or []
        if 'tehm_v2' in (plan or {}) and not plan['tehm_v2'].get('state',{}).get('signature'):
            # The native index's None query means unrestricted lookup. That is
            # not relevance evidence and must not become advice on an unknown fault.
            strategies=[]
        for candidate in strategies:
            decision=decisions.get(candidate['id'],{})
            evidence=decision.get('evidence') or {}
            observed=any(evidence.get(k,0)>0 for k in ('support','harm','partial','ineffective','unconfirmed'))
            native=candidate.get('source') in ('tehm_rule','tehm_compose')
            if not native and not observed:
                continue
            if candidate['id'] in self.used:continue
            item={'kind':'trial_admissible_procedural_observation' if native else 'action_matched_historical_observations',
                'id':candidate['id'],'rule_id':candidate.get('rule_id'),
                'candidate_source':candidate.get('source') or 'public_catalogue',
                'reference_action_edits':candidate.get('config_edits'),
                'reference_rerun_from':candidate.get('rerun_from'),
                'matching_basis':'typed failure, parameter and move direction; not exact numeric equivalence',
                'rationale':candidate.get('rationale'),
                'evidence':evidence or None,
                'evidence_reference':self.evidence_reference(plan,candidate) if observed else None,
                'interpretation':'Support, harm, partial progress, no effect and unconfirmed clears are '
                                 'separate observations. These are not causal effects or guaranteed fixes.',
                'authority':'observed procedure; NOT validated causal mechanism',
                'current_success':'UNKNOWN; requires fresh verification'}
            proposed=blocks+[item]
            if len(A.stable(proposed).encode())>14000:continue
            blocks=proposed;selected.append(candidate['id'])
            if sum(b['kind']!='validated_mechanism_knowledge' for b in blocks)>=2:break
        if not blocks:return '',[]
        block={'mode':'experience_assisted','selected_evidence':blocks,
            'instruction':'Use as fallible reference only. Adapt parameters and action order to current reports, '
                          'or ignore the advice and solve independently. Literal replay is not required. '
                          'Historical success is not verification on this task.',
            'automatic_causal_promotion':False,'write_back':False}
        text=A.stable(block)
        if len(text.encode())>16000:raise IntegrityStop('Bounded TEHM advice exceeds declared limit')
        self.guidance_active=True
        T.event(self.H,self.project,'memory_execution_choice',{
            'choice':'experience_assisted_online','selected_ids':selected,
            'automatic_execution':False,'causal_authority_granted':False,
            'agent_may_adapt_or_ignore':True})
        return text,selected

    def evidence_reference(self,plan,candidate):
        """Bind bounded advice to the SAME frozen, typed and source-isolated index.

        Public actions can have relevant historical observations even when no
        induced TEHM rule is executable. Do not discard that evidence, invent
        a rule, or turn its observations into causal authority.
        """
        from tehm_v2 import policy as P
        path=self.env.get('R2G_TEHM_V2_POLICY')
        if not path:raise IntegrityStop('Observed advice lacks its frozen evidence index')
        policy=json.loads(Path(path).read_text());raw=Path(policy['index']).read_bytes()
        actual=hashlib.sha256(raw).hexdigest()
        if actual!=policy['index_sha256']:raise IntegrityStop('Advice evidence index changed')
        index=json.loads(raw)
        cfg=E.effective_config((self.project/'constraints/config.mk').read_text())
        profile=P.evidence_summary({**candidate,'_moves':P.moves(candidate.get('config_edits'),cfg)},
            index,plan['tehm_v2']['state']['signature'],
            {policy['task_ip_family']} if policy.get('task_ip_family') else set())
        summary=(next(d for d in plan['tehm_v2']['decisions'] if d['id']==candidate['id']).get('evidence') or {})
        if any(profile.get(k)!=v for k,v in summary.items()):
            raise IntegrityStop('Advice evidence does not reproduce native selection')
        units=sorted(set(u for u in profile['units'] if u))
        return {'index_sha256':actual,'selected_unit_ids':units[:4],
                'matched_units':len(units),'matched_unit_ids_sha256':digest(units),
                'signature':plan['tehm_v2']['state']['signature']}

    def observe_decision(self,row):
        """Use executor evidence, not a model's opinion, to leave bad guidance.

        Disabling cross-task advice does not revoke any common tool/knob, erase
        task-local observations, refund a call, or make another model request.
        """
        if self.arm!='TM0' or not self.guidance_active:return
        execution=row.get('execution') or {}
        verdict=execution.get('verdict') or row.get('verdict') or {}
        if verdict.get('accepted'):return
        progress=execution.get('target_progress') or row.get('target_progress') or {}
        if (not execution.get('rolled_back') and progress.get('improved') is True
                and row.get('status') not in ('REJECTED_DECISION','UNVERIFIED')):
            return
        self.experience_disabled=True;self.guidance_active=False
        self.fallback_reason=('memory_guided_attempt_rolled_back' if execution.get('rolled_back') else
                              'memory_guided_attempt_no_verified_improvement')
        T.event(self.H,self.project,'memory_fallback',{
            'reason':self.fallback_reason,'sequence':row['sequence'],
            'trigger_status':row.get('status'),'target_progress':progress,
            'mode':'no_experience','cross_task_context_removed':True,
            'task_feedback_retained':True,'common_tools_and_knobs_unchanged':True,
            'extra_requests':0,'write_back':False})

    def consume(self,messages,prefix):
        if self.receipt is None:raise IntegrityStop('Memory request without selection receipt')
        receipt={**self.receipt,'messages_sha256':digest(messages),'request_prefix':Path(prefix).name}
        bound=verify_request(self.project,receipt)
        if not bound['serialized_request_present']:raise IntegrityStop('Missing serialized consumer request')
        T.event(self.H,self.project,'memory_consumption',bound)

    def diagnose(self,apply=None):
        check=self.current_situation()['check']
        if check=='orfs_stage':check='route'
        if check not in ('drc','lvs','timing','route'):return None
        cmd=[sys.executable,str(self.H.SKILL/'scripts/reports/diagnose_signoff_fix.py'),str(self.project),
             '--check',check,'--exclude',','.join(self.used)]
        cmd+=['--apply',apply] if apply else ['--list']
        start,cpu=time.time(),resource.getrusage(resource.RUSAGE_CHILDREN)
        p=subprocess.run(cmd,env=self.env,capture_output=True,text=True,timeout=120)
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        T.event(self.H,self.project,'tool_call',{'component':'memory','tool':'native_memory_diagnose',
            'command':cmd,'returncode':p.returncode,'wall_s':time.time()-start,
            'cpu_s':after.ru_utime+after.ru_stime-cpu.ru_utime-cpu.ru_stime})
        with (self.project/'native-memory-diagnose.log').open('a') as f:f.write(p.stderr+'\n'+p.stdout+'\n')
        if p.returncode:raise IntegrityStop('Native memory lookup/apply failed')
        try:plan=json.loads(p.stdout)
        except ValueError:raise IntegrityStop('Native memory returned invalid JSON')
        if plan.get('technical_failures'):raise IntegrityStop('Native candidate contract failed: '+A.stable(plan['technical_failures']))
        return plan

    def native_action(self):
        # Historical packages retain advice-only semantics. A new registration
        # is required to opt into procedural execution; no frozen run is changed.
        if self.arm=='TM0' and getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY:
            return self.gated_native_action()
        if self.arm!='RECIPE' or self.native_failures>=3:return None
        plan=self.diagnose()
        if plan is None:return None
        T.event(self.H,self.project,'native_memory_plan',{'arm':self.arm,'plan':plan,
            'public_candidates_not_executed':True,'write_back':False})
        # Preserve native ordering/defer decisions: only a memory candidate at the
        # head may execute. A catalogue head yields to the agent, never auto-runs.
        candidates=plan.get('strategies') or []
        if not candidates:return None
        candidate=candidates[0]
        if candidate.get('source') not in ('tehm_rule','tehm_compose','recipe') or candidate['id'] in self.used:
            return None
        T.event(self.H,self.project,'memory_execution_choice',{
            'choice':'local_experience','candidate':candidate['id'],
            'knowledge_route':(self.route_state or {}).get('knowledge_route'),
            'procedural_route':'native_verified_candidate_trial',
            'causal_authority_granted':False,'current_target_verification_required':True})
        return candidate

    def procedure_route_blocked(self):
        state=self.route_state or {}
        return (state.get('knowledge_route') in ('INAPPLICABLE','ABSTAIN')
                or (state.get('knowledge_route')=='NO_SKILL'
                    and state.get('no_skill_reason') not in (None,'NO_MATCH'))
                or state.get('risk',{}).get('risk_status') in ('HIGH','INVALID'))

    @property
    def require_native_acceptance(self):
        return self.arm=='TM0' and getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY

    def gated_native_action(self):
        from t2_tehm_execution_policy import choose, load_evidence, sha
        start,cpu=time.time(),time.process_time()
        if self.experience_disabled:return None
        self.local_selection=None
        sit=self.current_situation()
        self.route_knowledge(A.stable(self.H.observation(self.project)),sit)
        if self.procedure_route_blocked():
            T.event(self.H,self.project,'memory_execution_choice',{
                'policy':TM0_THREE_WAY_POLICY,'choice':'no_experience_online',
                'reason':'native_router_veto_or_abstention','repair_executed':False})
            return None
        plan=self.diagnose() or {}
        sys.path.insert(0,str(self.H.SKILL/'knowledge'))
        from candidate_contract import contract_errors
        for c in plan.get('strategies') or []:
            if contract_errors(c):raise IntegrityStop('Invalid three-way candidate contract')
        try:units,index,excluded,refs=load_evidence(self.root,self.env)
        except (OSError,ValueError,KeyError) as exc:
            raise IntegrityStop('Three-way evidence integrity failure: '+str(exc)) from exc
        cfgpath=self.project/'constraints/config.mk'
        cfg=E.effective_config(cfgpath.read_text())
        from t2_tehm_effective_state import current_execution
        decision=choose(plan=plan,situation=sit,cfg=cfg,units=units,index=index,
                        exclude_families=excluded,used=self.used,
                        effective_execution=current_execution(self.project,cfgpath))
        action=decision['candidate']
        if action is not None:
            updated=dict(cfg)
            for k,v in action['config_edits'].items():
                if v=='<unset>':updated.pop(k,None)
                else:updated[k]=v
            manifest=json.loads(Path(self.env['R2G_TASK_MANIFEST']).read_text())
            check=E.check_config(manifest,updated,project=self.project,env=self.env)
            if not check['ok']:
                if any(v.get('kind')=='immutable_file_changed' for v in check['violations']):
                    raise IntegrityStop('Three-way immutable task input changed')
                decision.update(choice='experience_assisted_online',candidate=None,
                                reason='current_task_contract_rejects_local_action',violations=check['violations'])
                action=None
        decision.update(evidence_binding=refs,config_sha256=sha(cfgpath),
                        current_target_verification_required=True,repair_executed=False,
                        wall_s=time.time()-start,cpu_s=time.process_time()-cpu)
        self.last_native_decision=decision
        T.event(self.H,self.project,'memory_execution_choice',decision)
        if action is not None:
            self.local_selection={'action':json.loads(json.dumps(action)),
                                  'config_sha256':sha(cfgpath),'evidence_binding':refs,
                                  'selection_sha256':digest(decision)}
        return action

    def apply_native(self,action):
        if self.arm=='TM0' and getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY:
            from t2_tehm_execution_policy import sha
            selected=self.local_selection
            if (not selected or selected['action']!=action or
                    sha(self.project/'constraints/config.mk')!=selected['config_sha256']):
                raise IntegrityStop('Three-way selection/configuration changed before apply')
            current=self.gated_native_action()
            if (current!=action or self.local_selection is None or
                    self.local_selection['evidence_binding']!=selected['evidence_binding']):
                raise IntegrityStop('Three-way applicability/evidence changed before apply')
        plan=self.diagnose(apply=action['id'])
        if not plan or plan.get('status') not in ('applied','applied_no_op','no_effect'):
            raise IntegrityStop('Native candidate no longer applies')
        if plan.get('status') in ('applied_no_op','no_effect'):return None,'no_change'
        if plan.get('applied')!=action['id'] or plan.get('config_edits')!=action['config_edits']:
            raise IntegrityStop('Native candidate changed between selection and apply')
        return action['rerun_from'],plan

    def native_outcome(self,action,result):
        self.used.append(action['id'])
        if self.arm=='TM0' and getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY:
            T.event(self.H,self.project,'memory_procedure_consumption',{
                'policy':TM0_THREE_WAY_POLICY,'candidate':action['id'],
                'selection':getattr(self,'local_selection',None),
                'flow_launched':result.get('flow_launched',False),
                'journal_ref':result.get('journal_ref'),'status':result.get('status'),
                'rolled_back':result.get('rolled_back'),'model_requests':0,
                'causal_authority_granted':False,'write_back':False})
        if result.get('rolled_back') or not (result.get('verdict') or {}).get('accepted'):
            self.native_failures+=1
            if self.arm=='TM0' and getattr(self,'execution_policy',TM0_POLICY)==TM0_THREE_WAY_POLICY:
                self.experience_disabled=True;self.guidance_active=False
                self.fallback_reason=('local_procedure_rolled_back' if result.get('rolled_back') else
                                      'local_procedure_not_fully_accepted')
                T.event(self.H,self.project,'memory_fallback',{
                    'policy':TM0_THREE_WAY_POLICY,'reason':self.fallback_reason,
                    'mode':'no_experience','cross_task_context_removed':True,
                    'task_feedback_retained':True,'common_tools_and_knobs_unchanged':True,
                    'extra_requests':0,'write_back':False})
        # The shared executor writes the canonical task-local step once. Both
        # native repeat control and later annotation consume that same raw row.


def make_memory(H,project,arm,root,env):
    if arm=='ADAMEM':
        from t2_adamem import AdaMemory
        return AdaMemory(H,project,root,env)
    return None if arm=='NB' else Memory(H,project,arm,root,env)
