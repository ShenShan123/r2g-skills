"""AdaMEM-HIGH EDA port, pinned to yunx-z/AdaMEM 4ea93e2 (Apache-2.0).

Ports build_index.py and get_action_with_adamem_high, not the upstream SDK.
Successful trajectory suffixes -> current-state retrieval -> transient strategy
-> refined action. All three generations use the SAME metered OnlineGateway.
No Step-MFT, TEST write-back, causal promotion, hidden retry or provider client.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import time

import t2a_adapters as A
from t2_agent_dispatch import IntegrityStop

UPSTREAM_SHA='4ea93e239f8dbec2fa6013a28bc8555419037e12'
UPSTREAM=Path('/data1/zhangdy/tehm-integration-external/AdaMEM-20261022')
SOURCE_FILES=('LICENSE','README.md','build_index.py','utils.py',
              'examples/prompt_agent/gpt4o_alfworld.py',
              'agent_system/environments/prompts/alfworld.py')
CONFIG={'upstream_commit':UPSTREAM_SHA,'variant':'adamem-high','Step_MFT':False,
        'top_k':1,'successful_episodes_only':True,'step_stride':1,
        'embedding':'all-mpnet-base-v2','index':'exact cosine via shared CPU broker',
        'policy_model':'deepseek-flash','strategy_model':'deepseek-flash',
        'total_online_request_cap':3,'long_term_write_back':False,
        'strategy_lifetime':'one decision only'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def upstream_sources():
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=UPSTREAM_SHA:raise ValueError('AdaMEM upstream commit changed')
    status=subprocess.check_output(['git','-C',str(UPSTREAM),'status','--porcelain'],text=True)
    if status.strip():raise ValueError('AdaMEM upstream checkout is dirty')
    return [(UPSTREAM/rel,Path('AdaMEM')/rel) for rel in SOURCE_FILES]


def step_entries(units):
    """Only complete ACCEPTED episodes, never step-clear-as-episode-success.

    State keys contain ONLY the recorded pre-action situation/configuration and
    earlier rows. Values retain the current action and the successful suffix.
    Target/same-IP exclusion is performed by the shared fold builder BEFORE
    this function; no separate extra history is available to this method.
    """
    buckets=defaultdict(list)
    for unit in units:
        key=(A.task_key(unit),unit.get('episode_trace_id',unit['identity'].get('session')))
        buckets[key].append(unit)
    entries=[];excluded=[]
    for key,rows in sorted(buckets.items(),key=lambda x:A.stable(x[0])):
        outcomes={u.get('episode_status') for u in rows}
        if outcomes!={'ACCEPTED'}:
            excluded.append({'trajectory':A.fingerprint(key),'reason':'not_verified_final_success',
                             'outcomes':sorted(str(v) for v in outcomes)})
            continue
        steps=[u for u in rows if (u.get('raw_row') or {}).get('from_stage')]
        if not steps:continue
        refs={u['unit_id']:[r for r in (u.get('provenance') or {}).get('sources',[])
                           if str(r.get('path','')).endswith('/fix_log.jsonl')
                           and type(r.get('line')) is int and r['line']>0] for u in steps}
        owned=(all(len(refs[u['unit_id']])==1 for u in steps)
               and len({refs[u['unit_id']][0]['path'] for u in steps})==1
               and len({refs[u['unit_id']][0]['line'] for u in steps})==len(steps))
        if owned:
            steps.sort(key=lambda u:refs[u['unit_id']][0]['line'])
        elif (len({u['identity'].get('session') for u in steps})==1
              and all(type(u['capture_context'].get('step_index')) is int for u in steps)
              and len({u['capture_context']['step_index'] for u in steps})==len(steps)):
            steps.sort(key=lambda u:u['capture_context']['step_index'])
        else:
            excluded.append({'trajectory':A.fingerprint(key),'reason':'ambiguous_trajectory_step_order'})
            continue
        for i,u in enumerate(steps):
            raw=u['raw_row']
            state={'task':key[0],'platform':u['identity'].get('platform'),
                   'situation':raw.get('situation'),'config_before':raw.get('config_before'),
                   'target':{'check':raw.get('check'),'class':raw.get('violation_class'),
                             'before':raw.get('before')},
                   'prior_steps':[r['text_view'] for r in steps[:i]]}
            if not raw.get('situation') and not raw.get('config_before'):
                excluded.append({'unit':u['unit_id'],'reason':'missing_recorded_pre_state'});continue
            entries.append({'id':A.fingerprint([key,u['unit_id']]),'source_unit_id':u['unit_id'],
                'query':A.stable(state),'past_state':state,
                'past_action':{'strategy':raw.get('strategy'),'proposal':raw.get('proposal'),
                               'config_delta':raw.get('config_delta'),'from_stage':raw['from_stage']},
                'remaining_trajectory':[r['text_view'] for r in steps[i:]],
                'final_outcome':'ACCEPTED','source_unit_ids':sorted(r['unit_id'] for r in rows)})
    return entries,excluded


def build(controls,shared):
    controls,shared=Path(controls),Path(shared)
    target=controls/'ADAMEM.json'
    if target.exists():raise FileExistsError('Do not overwrite an AdaMEM memory')
    start,cpu=time.monotonic(),time.process_time()
    manifest=json.loads((shared/'manifest.json').read_text())
    source=shared/'shared_units.jsonl';actual=digest(source)
    if actual!=manifest['units_sha256']:raise ValueError('AdaMEM shared history hash mismatch')
    units=[json.loads(s) for s in source.read_text().splitlines() if s.strip()]
    entries,excluded=step_entries(units)
    data={'status':'FROZEN','adapter':'adamem-high-eda-v1','configuration':CONFIG,
          'units_sha256':actual,'source_sha256':actual,'entries':entries,'excluded':excluded,
          'construction_wall_s':time.monotonic()-start,'construction_cpu_s':time.process_time()-cpu,
          'model_requests':0,'tokens':0,'EDA_flows':0,
          'deviations':['EDA pre-state/config and checked action rows replace original environment text',
              'shared pinned all-mpnet CPU exact cosine replaces Qwen3-Embedding-4B HNSW',
              'shared DeepSeek model and JSON transport replace Qwen/vLLM/XML',
              'all online generations share three requests; UNKNOWN stops, not swallowed fallback',
              'no Step-MFT; transient strategy does not enter subsequent-step context'],
          'embedding_build':'runtime index initialization is charged in episode embedding receipts'}
    controls.mkdir(parents=True,exist_ok=True)
    with target.open('x') as f:f.write(json.dumps(data,indent=1,sort_keys=True)+'\n')
    return data


class AdaMemory:
    """Native decision policy: tentative action + request flag, synthesis, action.

    Tentative actions are not executed when refinement succeeds. If fewer than
    two requests remain, retain the valid tentative action and record denied
    adaptation, not a free extra call. Provider/accounting faults ALWAYS escape.
    """
    def __init__(self,H,project,root,env):
        self.H,self.project,self.root=H,Path(project),Path(root)
        self.path=self.root/'controls/ADAMEM.json';self.raw=self.path.read_bytes()
        self.data=json.loads(self.raw)
        manifest=json.loads((self.root/'shared/manifest.json').read_text())
        if (self.data.get('status')!='FROZEN' or self.data.get('configuration')!=CONFIG
                or self.data.get('units_sha256')!=manifest['units_sha256']
                or digest(self.root/'shared/shared_units.jsonl')!=manifest['units_sha256']):
            raise IntegrityStop('AdaMEM frozen history/config identity mismatch')
        self.entries=self.data['entries'];self.index=None;self.calls=[]

    def event(self,kind,data):
        import t2a_runtime as T
        T.event(self.H,self.project,kind,{'arm':'ADAMEM','variant':'adamem-high',**data})

    def agent_context(self,observation,gateway):
        # The agent, not this hook, chooses retrieval in decide(). No raw history
        # or previous transient strategy is injected into the common prompt.
        if self.path.read_bytes()!=self.raw:raise IntegrityStop('AdaMEM memory changed')
        return ''

    def decide(self,messages,gateway,prefix):
        from t2_agent_runtime import decode
        self.calls=[];prefix=Path(prefix)
        def call(msgs,suffix,purpose):
            name=prefix.with_name(prefix.name+'-'+suffix)
            content,usage=gateway.call(msgs,name,purpose=purpose,candidate_generating=True)
            self.calls.append({'prefix':name,'messages':msgs,'usage':usage})
            return content,usage
        opt=('AdaMEM-HIGH memory option: include request_memory (boolean) and memory_reason (string) '
             'alongside operation and arguments. First propose one valid tentative action. '
             'Decide whether similar successful trajectories would help with CURRENT evidence. '
             'A yes requests a fresh state-specific strategy and a refined action (two MORE online '
             'requests). These share, not extend, your three-request allowance. The strategy is '
             'discarded after this decision. No compulsory repair or memory use.')
        initial=[dict(m) for m in messages]
        initial[0]['content']+='\nAdaMEM-specific JSON schema for this generation: operation, arguments, request_memory, memory_reason. '+opt
        content,usage=call(initial,'tentative','planning')
        try:answer=json.loads(content)
        except (ValueError,TypeError):return content,usage
        if (not isinstance(answer,dict) or set(answer)!={'operation','arguments','request_memory','memory_reason'}
                or type(answer['request_memory']) is not bool or not isinstance(answer['memory_reason'],str)):
            self.event('adamem_invalid_request_decision',{'response':content})
            return A.stable({'invalid_adamem_decision':True}),usage
        tentative=A.stable({k:answer[k] for k in ('operation','arguments')})
        if decode(tentative)[1]:return tentative,usage
        self.event('adamem_memory_choice',{'requested':answer['request_memory'],
            'reason':answer['memory_reason'],'requests_remaining':gateway.remaining()})
        if not answer['request_memory']:return tentative,usage
        if gateway.remaining()<2:
            self.event('adamem_adaptation_unavailable',{'reason':'insufficient_online_requests',
                'tentative_retained':True,'extra_requests':0})
            return tentative,usage
        if not self.entries:
            self.event('adamem_adaptation_unavailable',{'reason':'no_successful_trajectory',
                'tentative_retained':True,'extra_requests':0})
            return tentative,usage
        start,cpu=time.monotonic(),time.process_time()
        if self.index is None:self.index=A.DenseIndex(CONFIG['embedding'],[r['query'] for r in self.entries])
        # The last common user message is CURRENT evidence, not the tentative
        # action or a generated strategy. The synthesis sees all task context.
        query=messages[-1]['content']
        selected=[self.entries[i] for i in self.index.search(query,CONFIG['top_k'])]
        self.event('adamem_retrieval',{'selected_ids':[r['id'] for r in selected],
            'query_sha256':A.fingerprint(query),'units_sha256':self.data['units_sha256'],
            'wall_s':time.monotonic()-start,'cpu_s':time.process_time()-cpu,'write_back':False})
        if not selected:
            self.event('adamem_adaptation_unavailable',{'reason':'empty_retrieval',
                'tentative_retained':True,'extra_requests':0})
            return tentative,usage
        synth=[*messages,{'role':'user','content':
            'Synthesize 1-3 concise actionable strategy items tailored to CURRENT state from the '
            'retrieved successful trajectories below. Compare similarities and differences; '
            'state uncertainty, abstract rather than copy. Do not generate the next executable '
            'action. Return ONLY {"strategy":"..."}. Historical success is not current verification.\n'
            +A.stable(selected)}]
        synth[0]=dict(synth[0])
        synth[0]['content']+='\nFor this strategy-only generation, output schema is ONLY {"strategy":"..."}, not operation/arguments. Do not generate an executable candidate.'
        strategy_content,_=call(synth,'strategy','memory_adaptation')
        try:strategy=json.loads(strategy_content)
        except (ValueError,TypeError):strategy=None
        if (not isinstance(strategy,dict) or set(strategy)!={'strategy'}
                or not isinstance(strategy['strategy'],str) or not strategy['strategy'].strip()):
            self.event('adamem_invalid_strategy',{'tentative_retained':True,'retry':False})
            return tentative,usage
        refined=[*messages,{'role':'user','content':
            'Decide the next operation from current reports using or ignoring this transient strategy. '
            'Return ONLY operation and arguments, without another memory request.\n'
            +strategy['strategy']}]
        final,final_usage=call(refined,'refined','planning')
        self.event('adamem_strategy_used',{'selected_ids':[r['id'] for r in selected],
            'strategy_sha256':A.fingerprint(strategy['strategy']),
            'tentative_action':json.loads(tentative),'action_changed':decode(final)[0]!=decode(tentative)[0],
            'strategy_persisted':False,'long_term_write_back':False})
        return final,final_usage

    def consume(self,messages,prefix):
        from t2_tehm_agent_memory import verify_request,digest as message_digest
        for call in self.calls:
            receipt={'messages_sha256':message_digest(call['messages']),
                     'request_prefix':call['prefix'].name}
            bound=verify_request(self.project,receipt)
            if not bound['serialized_request_present']:raise IntegrityStop('Missing AdaMEM request receipt')
            self.event('memory_consumption',{**bound,'units_sha256':self.data['units_sha256']})

    def close(self):
        if self.index is not None and hasattr(self.index,'close'):self.index.close()
