"""Opt-in Sol provider/usage adapter over the unchanged R2G executor."""
import json
import os
from pathlib import Path
import time

import sol_provider as P


def policy():
    return {'revision':'sol-high-response-count-three-v1','scope':'Table2_R2G_model_baseline_extension',
        'model':P.MODEL,'reasoning_effort':P.EFFORT,'endpoint':'https://api.openai.com/v1/responses',
        'input_bytes_ceiling':None,'output_tokens':P.MAX_OUTPUT_TOKENS,'outer_output_ceiling':None,
        'output_limit_source':'provider hard maximum, not experiment quota',
        'provider_context_tokens':1050000,'provider_max_input_tokens':922000,
        'repair_generations':3,'total_online_requests':3,'scheduler':'agent_selected',
        'transport_retries':0,'cumulative_token_cap':None,'full_insights_and_workflows_required':True,
        'online_learning':False,'cross_task_memory':False,
        'provider_documentation':'https://developers.openai.com/api/docs/models/gpt-6.1-sol'}


class AdmissionStopped(Exception):
    def __init__(self,usage):self.usage=usage


def governed_call(H,key,messages,out_prefix,backoff=(),ledger=None,role='repair',sampling_override=None):
    if backoff or sampling_override:
        return None,{'accounting_error':'Unregistered retry or sampling override'}
    if ledger is None or os.environ.get('R2G_REQUIRE_LLM_BUDGET')!='1' or not H._phase():
        return None,{'accounting_error':'Missing episode/phase budget binding'}
    if role not in ('repair','auxiliary'):return None,{'accounting_error':'Invalid request role'}
    prefix=Path(str(out_prefix)+'.try0')
    if any(Path(str(prefix)+suffix).exists() for suffix in ('.request.json','.response.json','.transport.json')):
        return None,{'accounting_error':'Existing request prefix; no implicit retry'}
    _,endpoint=P.credentials()
    body=P.payload(messages);encoded=json.dumps(body,ensure_ascii=False).encode()
    phase_project,phase_env=H._phase();turn=phase_hold=hold=None
    sent=False;response={};transport={};tokens=None;content=None;failure=None;errors=[]
    def admit(project,needs,*,scope,env=None):
        args=['admit','--hold','--component','llm_repair']
        for need in needs:args.extend(['--need',need])
        rc,raw=H._ledger(project,*args,env=env)
        if rc:
            flag={'budget_exhausted':True,'scope':scope} if rc==75 else {'accounting_error':'admission rc='+str(rc)}
            raise AdmissionStopped(flag)
        if not raw:raise AdmissionStopped({'accounting_error':'Admission returned no hold identity'})
        return raw
    def settle(project,identity,actual,env=None):
        if identity:
            args=['settle','--hold-id',identity]
            for resource,value in actual.items():args.extend(['--actual',resource+'='+str(value)])
            rc,_=H._ledger(project,*args,env=env)
            if rc:errors.append('settle rc='+str(rc))
    try:
        if role=='repair':turn=admit(ledger,['repair_attempts=1'],scope='episode')
        needs=['llm_requests=1','tokens_in='+str(len(encoded)),'tokens_out='+str(P.MAX_OUTPUT_TOKENS)]
        phase_hold=admit(phase_project,needs,scope='phase',env=phase_env)
        hold=admit(ledger,needs,scope='episode')
        rc,raw=H._ledger(ledger,'remaining')
        left=json.loads(raw).get('wall_s') if rc==0 else 'UNKNOWN'
        if left=='UNKNOWN':raise AdmissionStopped({'accounting_error':'Remaining wall time UNKNOWN'})
        timeout=H.LLM_TIMEOUT_S if left is None else min(H.LLM_TIMEOUT_S,left-H.LLM_KILL_GRACE_S)
        if timeout<=0:raise AdmissionStopped({'budget_exhausted':True,'scope':'episode'})
        sent=True
        response,transport=P.transport(body,key,endpoint,prefix,timeout=timeout)
        tokens=P.usage(response)
        if response.get('status')=='completed':content=P.output_text(response) or None
    except AdmissionStopped as exc:failure=exc.usage
    except Exception as exc:failure={'accounting_error':type(exc).__name__+': '+str(exc).replace(key,'<redacted>')}
    finally:
        actual={'llm_requests':int(sent),'tokens_in':tokens['prompt_tokens'] if tokens else 'UNKNOWN',
                'tokens_out':tokens['completion_tokens'] if tokens else 'UNKNOWN'}
        if not sent:actual.update(tokens_in=0,tokens_out=0)
        settle(phase_project,phase_hold,actual,phase_env);settle(ledger,hold,actual)
        settle(ledger,turn,{'repair_attempts':int(sent)})
        if sent:
            mismatch=transport.get('http_status')==200 and response.get('model')!=P.MODEL
            event={'attempt':0,'status':transport.get('http_status'),'hold_id':hold,'phase_hold_id':phase_hold,
                'role':role,'repair_turn_id':turn,'model_requested':P.MODEL,'model_reported':response.get('model'),
                'model_mismatch':mismatch,'reasoning_effort_requested':P.EFFORT,
                'reasoning_effort_reported':(response.get('reasoning') or {}).get('effort'),
                'request_id':response.get('id') or transport.get('request_id'),'endpoint':endpoint,
                'request_bytes':len(encoded),'latency_s':transport.get('wall_s'),
                'usage_reported':tokens or 'UNKNOWN','provider_usage':response.get('usage') or 'UNKNOWN',
                'reasoning_tokens':tokens.get('reasoning_tokens') if tokens else 'UNKNOWN',
                'provider_response_status':response.get('status'),'retries':0,
                'service_tier_reported':response.get('service_tier'),
                'reasoning_is_included_in_completion_tokens':True}
            rc,_=H._ledger(ledger,'event','--type','llm_request','--json',json.dumps(event))
            if rc:errors.append('request event rc='+str(rc))
            effort_mismatch=(transport.get('http_status')==200 and
                             (response.get('reasoning') or {}).get('effort')!=P.EFFORT)
            if mismatch or effort_mismatch or transport.get('http_status') in (401,402,403):
                stop=P.ROOT/'GLOBAL-STOP.json'
                if not stop.exists():P.save_new(stop,{'reason':'provider identity/payment/permission failure',
                    'http_status':transport.get('http_status'),'model_reported':response.get('model'),
                    'request_prefix':str(prefix),'usage_preserved':True})
                failure={'stopped':{'provider_identity_or_permission':True}}
    if errors:failure={**(failure or {}),'accounting_error':errors}
    if failure:return None,{**(tokens or {}),**failure,'role':role}
    if (response.get('model')!=P.MODEL or (response.get('reasoning') or {}).get('effort')!=P.EFFORT or
        response.get('status')!='completed' or tokens is None or not content):
        return None,{**(tokens or {}),'stopped':{'invalid_provider_result':True},'role':role}
    result={**tokens,'role':role}
    if turn:result['repair_turn_id']=turn
    return content,result


def install_online():
    import t2a_runtime as T
    original=T.configure_online
    def configure(H):
        original(H)
        from t2_signoff_integrity import install
        install(H)
        H.LLM_MODEL_LABEL=H.LLM_API_MODEL=P.MODEL
        H.LLM_ENDPOINT='https://api.openai.com/v1/responses'
        H.LLM_ALLOWED_REPORTED_MODELS=(P.MODEL,)
        H.LLM_MAX_TOKENS=H.LLM_OUTPUT_CEILING=P.MAX_OUTPUT_TOKENS
        H.LLM_MAX_INPUT_BYTES=float('inf')
        H.llm_key=lambda path:P.credentials(Path(path))[0]
        H.llm_call=lambda key,messages,prefix,**kw:governed_call(H,key,messages,prefix,**kw)
        return policy()
    T.configure_online=configure;T.online_policy=policy;T.ONLINE_INPUT_BYTES=float('inf')
