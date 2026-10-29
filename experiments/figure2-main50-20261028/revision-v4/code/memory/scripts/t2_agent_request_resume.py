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
    if hasattr(H,'final_verdict'):
        from t2_signoff_integrity import install
        install(H)
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


MALFORMED_RETRY_POLICY='explicit-malformed-output-retry-1-v1'
MALFORMED_RETRY_SUFFIX='.malformed-retry-1'
TRANSPORT_RETRY_SUFFIX='.transport-retry-1'
RETRY_SUFFIXES=(MALFORMED_RETRY_SUFFIX,TRANSPORT_RETRY_SUFFIX)
STANDING_TRANSPORT_RULE='construction-no-response-occupy-and-retry-once-v1'


def retry_kinds(ident):
    """Retry kinds already used along an ident's chain, e.g. x.transport-retry-1.malformed-retry-1."""
    return ['malformed']*ident.count(MALFORMED_RETRY_SUFFIX)+['transport']*ident.count(TRANSPORT_RETRY_SUFFIX)


def can_retry(ident,kind):
    """User rule 2026-10-23 ('Allow + wait 30 min'): each failure kind keeps its own single retry,
    so a request is sent at most 3 times; any further failure pauses for the user."""
    kinds=retry_kinds(ident)
    return kind not in kinds and len(kinds)<2
# Shape errors only: valid JSON whose field has the wrong form (user rule 2026-10-23).
AMEM_MALFORMED={'Invalid A-MEM context type'}


def amem_malformed_reason(response):
    """The registered shape check, replayed on a saved, never-cached response."""
    import t2a_construct as C
    try:raw=load(response);choices=raw.get('choices',[])
    except (ValueError,AttributeError):return None # e.g. a saved transport error, not a model output
    if [c.get('finish_reason') for c in choices]==['length']:return None
    try:verify_amem_contexts(C.parse_answer(choices[0]['message']['content'],False))
    except RuntimeError as exc:return str(exc) if str(exc) in AMEM_MALFORMED else None
    except (ValueError,KeyError,IndexError,TypeError):return None
    return None


def invalid_output_reason(response,tolerate=False):
    """A saved, metered model reply whose content is not one valid JSON object (user rule
    2026-10-23, 'Extend one retry'): e.g. trailing provider tool-call markup. Not truncation."""
    import t2a_construct as C
    try:raw=load(response);choices=raw.get('choices',[]);content=choices[0]['message']['content']
    except (ValueError,AttributeError,KeyError,IndexError,TypeError):return None
    if [c.get('finish_reason') for c in choices]==['length'] or not isinstance(content,str):return None
    try:C.parse_answer(content,tolerate)
    except ValueError as exc:return 'Invalid JSON output: '+str(exc)[:200]
    return None


def install_constructor(revision):
    import t2a_construct as C
    original=C.Calls
    grant=load(Path(revision)/'authorization.json')
    retry_grants=grant['truncated_requests']
    class Calls(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            configure_harness(self.H)

        def transport_retry(self,ident,prompt,temperature,max_tokens):
            """One explicit re-send after a registered transport failure (status -1 or 402, UNKNOWN usage).

            Only for a request the user named (hash-bound in the grant), and only after its UNKNOWN
            occupancy was recorded in this ledger. Never repeated; actual usage stays UNKNOWN."""
            path=os.environ.get('R2G_MALFORMED_RETRY_GRANT')
            if not path:return None
            if sha(path)!=os.environ.get('R2G_MALFORMED_RETRY_GRANT_SHA256'):raise RuntimeError('Retry grant changed')
            if not can_retry(ident,'transport'):return None
            retry=ident+TRANSPORT_RETRY_SUFFIX
            if (self.project/(retry+'.claim.json')).exists() and list(self.project.glob(retry+'.try*')):
                # Already sent once: continue with the retry's own handling; it is never re-sent.
                return self.ask(retry,prompt,temperature,max_tokens)
            response=self.project/(ident+'.try0.response.json')
            g=load(path);entry=(g.get('transport_retries') or {}).get(str(response.resolve()))
            if not entry:
                entry=self.standing_transport_occupancy(ident,g,path)
                if not entry:return None
            request=self.project/(ident+'.try0.request.json')
            if sha(response)!=entry['response_sha256'] or sha(request)!=entry['request_sha256']:
                raise RuntimeError('Transport-failure evidence changed: '+ident)
            ledger=self.project/'reports/resource_ledger.jsonl';ev=rows(ledger)
            call=[e for e in ev if e.get('type')=='llm_request' and e.get('hold_id')==entry['hold_id']]
            # -1: connection failure; 402: provider refused for insufficient balance (user, 2026-10-23).
            if (len(call)!=1 or call[0].get('status') not in (-1,402) or call[0].get('usage_reported')!='UNKNOWN'
                    or call[0].get('request_id') is not None):
                raise RuntimeError('Not the registered transport failure: '+ident)
            if not any(e.get('type')=='budget_unknown_occupancy' and e.get('hold_id')==entry['hold_id'] for e in ev):
                raise RuntimeError('UNKNOWN occupancy not recorded before the retry: '+ident)
            if (self.project/(retry+'.answer.json')).exists():return self.ask(retry,prompt,temperature,max_tokens)
            if list(self.project.glob(retry+'.try*')):raise RuntimeError('Transport retry already sent: '+ident)
            lineage={'policy':'explicit-transport-retry-1-v1','original_ident':ident,'retry_ident':retry,
                'original_hold_id':entry['hold_id'],'grant':str(Path(path).resolve()),'grant_sha256':sha(path)}
            with (self.project/(retry+'.claim.json')).open('x') as f:
                json.dump(lineage,f,sort_keys=True);f.flush();os.fsync(f.fileno())
            rc,_=self.H._ledger(self.project,'event','--type','explicit_transport_retry','--json',json.dumps(lineage))
            if rc:raise RuntimeError('Cannot record transport retry')
            return self.ask(retry,prompt,temperature,max_tokens)

        def standing_transport_occupancy(self,ident,g,path):
            """User standing rule 2026-10-23: a construction request with no response (status -1)
            occupies its booked bound in the build and shared ledgers, then gets one explicit re-send.
            Only the build's LAST request, and only when it is the build's single uncached response."""
            if g.get('standing_transport_rule')!=STANDING_TRANSPORT_RULE:return None
            ledger=self.project/'reports/resource_ledger.jsonl';ev=rows(ledger)
            calls=[e for e in ev if e.get('type')=='llm_request']
            # An original whose explicit retry already has an answer is resolved, not outstanding
            # (2026-10-23, C01878: evolution-0021 malformed+retried, then attribute-0050 dropped).
            def resolved(p):
                base=str(p)[:-len('.try0.response.json')]
                return (Path(base+'.answer.json').exists()
                        or bool(list(Path(base).parent.glob(Path(base).name+'.*-retry-1*.answer.json'))))
            uncached=[p for p in self.project.glob('*.try0.response.json') if not resolved(p)]
            if (not calls or calls[-1].get('status')!=-1 or calls[-1].get('usage_reported')!='UNKNOWN'
                    or calls[-1].get('request_id') is not None
                    or [p.name for p in uncached]!=[ident+'.try0.response.json']):return None
            call=calls[-1];phase=Path(os.environ['R2G_PHASE_LEDGER']).resolve()
            def booked(led,hold):
                s=[e for e in rows(led) if e.get('type')=='budget_settle' and e.get('hold_id')==hold]
                if len(s)!=1:raise RuntimeError('Standing rule: no single settlement for '+hold)
                return {r:s[0]['booked'][r] for r in ('tokens_in','tokens_out')}
            targets=[{'ledger_path':str(ledger.resolve()),'hold_id':call['hold_id'],'upper_bound':booked(ledger,call['hold_id'])},
                     {'ledger_path':str(phase),'hold_id':call['phase_hold_id'],'upper_bound':booked(phase,call['phase_hold_id'])}]
            out=Path(path).parent/'auto-occupancy'/(self.project.parents[2].name+'-'+self.project.name+'-'+ident+'.json')
            if not out.exists():
                out.parent.mkdir(exist_ok=True)
                save(out,{'schema':'r2g-unknown-occupancy-authorization-v1','user_approved':True,
                    'standing_rule':STANDING_TRANSPORT_RULE,'user_choice':'Standing rule (Recommended)',
                    'grant':str(Path(path).resolve()),'grant_sha256':sha(path),'request':ident,'cause':'status -1, no response',
                    'targets':targets,'meaning':'booked bound occupied for admission safety only; actual usage stays UNKNOWN'})
            budgets={str(ledger.resolve()):os.environ['R2G_BUDGET_FILE'],str(phase):os.environ['R2G_PHASE_BUDGET_FILE']}
            for t in targets:
                if any(e.get('type')=='budget_unknown_occupancy' and e.get('hold_id')==t['hold_id'] for e in rows(t['ledger_path'])):continue
                r=subprocess.run(['python3',str(self.H.SKILL/'scripts/flow/resource_ledger.py'),'occupy-unknown',str(self.project),
                    '--hold-id',t['hold_id'],'--authorization',str(out),'--authorization-sha256',sha(out)],
                    env={**os.environ,'R2G_LEDGER_PATH':t['ledger_path'],'R2G_BUDGET_FILE':budgets[t['ledger_path']]},
                    capture_output=True,text=True)
                if r.returncode:raise RuntimeError('Standing-rule occupancy failed: '+r.stderr.strip()[:200])
            return {'hold_id':call['hold_id'],'response_sha256':sha(self.project/(ident+'.try0.response.json')),
                    'request_sha256':sha(self.project/(ident+'.try0.request.json'))}

        def wait_for_phase_admission(self,limit=900):
            """A phase refusal sent nothing. Wait (no request) until no unoccupied UNKNOWN remains."""
            phase=Path(os.environ['R2G_PHASE_LEDGER']);deadline=time.time()+limit
            while time.time()<deadline:
                ev=rows(phase);occ={e['hold_id'] for e in ev if e.get('type')=='budget_unknown_occupancy'}
                if not any(e.get('type')=='budget_settle' and e['hold_id'] not in occ
                           and 'UNKNOWN' in json.dumps(e.get('actual')) for e in ev):return True
                time.sleep(10)
            return False

        def malformed_grant(self):
            path=os.environ.get('R2G_MALFORMED_RETRY_GRANT')
            if not path:return None
            if sha(path)!=os.environ.get('R2G_MALFORMED_RETRY_GRANT_SHA256'):
                raise RuntimeError('Malformed-output retry grant changed')
            if load(path).get('malformed_output_retry')!=MALFORMED_RETRY_POLICY:return None
            return {'path':str(Path(path).resolve()),'sha256':sha(path)}

        def malformed_retry(self,ident,prompt,temperature,max_tokens,reason):
            """At most one explicit re-send of the SAME prompt after a malformed output.

            Strict parsers are unchanged; the malformed response and every cost stay recorded.
            A cached retry answer is replayed without a request; a sent retry is never re-sent."""
            grant=self.malformed_grant()
            if not grant or not can_retry(ident,'malformed'):return None
            retry=ident+MALFORMED_RETRY_SUFFIX
            if (self.project/(retry+'.answer.json')).exists():return self.ask(retry,prompt,temperature,max_tokens)
            if (self.project/(retry+'.claim.json')).exists() and list(self.project.glob(retry+'.try*')):
                return self.ask(retry,prompt,temperature,max_tokens) # sent once: never re-sent
            if list(self.project.glob(retry+'.try*')):raise RuntimeError('Malformed-output retry already sent: '+ident)
            ev=rows(self.project/'reports/resource_ledger.jsonl')
            if ({e['hold_id'] for e in ev if e.get('type')=='budget_hold'}
                    -{e['hold_id'] for e in ev if e.get('type')=='budget_settle'}):
                raise RuntimeError('Unsettled prior request before malformed-output retry')
            lineage={'policy':MALFORMED_RETRY_POLICY,'original_ident':ident,'retry_ident':retry,'reason':reason,
                'arm':self.project.name,'grant':grant['path'],'grant_sha256':grant['sha256']}
            with (self.project/(retry+'.claim.json')).open('x') as f:
                json.dump(lineage,f,sort_keys=True);f.flush();os.fsync(f.fileno())
            rc,_=self.H._ledger(self.project,'event','--type','explicit_malformed_output_retry','--json',json.dumps(lineage))
            if rc:raise RuntimeError('Cannot record malformed-output retry')
            return self.ask(retry,prompt,temperature,max_tokens)

        def invalid_output_retry(self,ident,prompt,temperature,max_tokens):
            previous=self.project/(ident+'.try0.response.json')
            if ((self.project/(ident+'.answer.json')).exists() or not can_retry(ident,'malformed')
                    or not previous.exists()):return None
            # The A-MEM evolution context policy parses strictly even when the Calls object is
            # tolerant (2026-10-23, C00584 evolution-0013); judge the reply by the parse that applies.
            strict=(grant.get('amem_context_policy')==AMEM_CONTEXT_POLICY and self.project.name=='AMEM'
                    and ident.startswith('evolution-'))
            reason=invalid_output_reason(previous,False if strict else getattr(self,'tolerate',False))
            return self.malformed_retry(ident,prompt,temperature,max_tokens,reason) if reason else None

        def standing_active(self):
            path=os.environ.get('R2G_MALFORMED_RETRY_GRANT')
            return bool(path) and load(path).get('standing_transport_rule')==STANDING_TRANSPORT_RULE

        def native_evolution_skip(self,ident):
            """User rule 2026-10-23 ('Native: skip evolution'): an A-MEM evolution reply that is still
            unusable after its one malformed-output retry is handled as upstream A-MEM does (return
            False, note): the existing construction fallback records the note in parse_failures and
            does not evolve it. No request is sent. Replay-safe: judged from the saved reply."""
            if not (grant.get('amem_context_policy')==AMEM_CONTEXT_POLICY and self.project.name=='AMEM'
                    and ident.startswith('evolution-') and 'malformed' in retry_kinds(ident)):return None
            response=self.project/(ident+'.try0.response.json')
            if (self.project/(ident+'.answer.json')).exists() or not response.exists():return None
            reason=amem_malformed_reason(response) or invalid_output_reason(response,False)
            if not reason:return None
            content=load(response)['choices'][0]['message']['content']
            record=self.project/(ident+'.native-evolution-skip.json')
            if not record.exists():
                lineage={'policy':'amem-native-evolution-skip-after-retry-v1','ident':ident,'reason':reason,
                    'upstream':'WujiangXu_A-mem memory_layer.process_memory: unusable reply -> return False, note'}
                with record.open('x') as f:json.dump(lineage,f,sort_keys=True)
                rc,_=self.H._ledger(self.project,'event','--type','amem_native_evolution_skip','--json',json.dumps(lineage))
                if rc:raise RuntimeError('Cannot record native evolution skip')
            return {'_malformed_content':content}

        def ask(self,ident,prompt,temperature,max_tokens=800):
            skipped=self.native_evolution_skip(ident)
            if skipped is not None:return skipped
            if (not (self.project/(ident+'.answer.json')).exists()
                    and (self.project/(ident+'.try0.response.json')).exists()):
                retried=self.transport_retry(ident,prompt,temperature,max_tokens)
                if retried is not None:return retried
                retried=self.invalid_output_retry(ident,prompt,temperature,max_tokens)
                if retried is not None:return retried
            try:return self._dispatch(ident,prompt,temperature,max_tokens)
            except RuntimeError as exc:
                text=str(exc);standing=self.standing_active()
                if standing and "'budget_exhausted': True, 'scope': 'phase'" in text and not getattr(self,'_waited',False):
                    self._waited=True
                    try:
                        if self.wait_for_phase_admission():return self.ask(ident,prompt,temperature,max_tokens)
                    finally:self._waited=False
                if standing and text.startswith('Construction request stopped') and can_retry(ident,'transport'):
                    retried=self.transport_retry(ident,prompt,temperature,max_tokens)
                    if retried is not None:return retried
                skipped=self.native_evolution_skip(ident)
                if skipped is not None:return skipped
                raise
            except ValueError:
                retried=self.invalid_output_retry(ident,prompt,temperature,max_tokens)
                if retried is not None:return retried
                skipped=self.native_evolution_skip(ident)
                if skipped is not None:return skipped
                raise

        def _dispatch(self,ident,prompt,temperature,max_tokens):
            guarded=(grant.get('amem_context_policy')==AMEM_CONTEXT_POLICY
                and self.project.name=='AMEM' and ident.startswith('evolution-')
                and not (self.project/(ident+'.answer.json')).exists())
            if not guarded:return self._ask(ident,prompt,temperature,max_tokens)
            previous=self.project/(ident+'.try0.response.json')
            if previous.exists() and can_retry(ident,'malformed'):
                reason=amem_malformed_reason(previous)
                if reason:
                    retried=self.malformed_retry(ident,prompt,temperature,max_tokens,reason)
                    if retried is not None:return retried
            try:return self._guarded_ask(ident,prompt,temperature,max_tokens)
            except RuntimeError as exc:
                if str(exc) in AMEM_MALFORMED and can_retry(ident,'malformed'):
                    retried=self.malformed_retry(ident,prompt,temperature,max_tokens,str(exc))
                    if retried is not None:return retried
                raise

        def _guarded_ask(self,ident,prompt,temperature,max_tokens):
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
