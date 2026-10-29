"""Official Responses transport, exact requested model, no implicit retries."""
import argparse
import datetime
import hashlib
import json
import math
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MODEL='gpt-6.1-sol'
EFFORT='high'
MAX_OUTPUT_TOKENS=128000  # official model maximum, not an experiment token quota
CREDENTIALS=Path('/data1/zhangdy/.llm_env')
ROOT=Path(__file__).resolve().parent


def credentials(path=CREDENTIALS):
    config={}
    for line in Path(path).read_text().splitlines():
        match=re.match(r'\s*(?:export\s+)?(GPT_API_KEY|GPT_MODEL|GPT_BASE_URL)\s*=\s*(.*)$',line)
        if match:
            key,value=match.groups();values=shlex.split(value,comments=True)
            if len(values)!=1 or '$' in values[0]:raise ValueError('Unresolved GPT configuration field: '+key)
            if key in config and config[key]!=values[0]:raise ValueError('Ambiguous GPT configuration: '+key)
            config[key]=values[0]
    if config.get('GPT_MODEL')!=MODEL:raise ValueError('GPT_MODEL must match '+MODEL)
    if not config.get('GPT_API_KEY'):raise ValueError('GPT_API_KEY is not configured')
    url=urllib.parse.urlsplit(config.get('GPT_BASE_URL','https://api.openai.com/v1'))
    if url.scheme!='https' or url.hostname!='api.openai.com' or url.username or url.password or url.query or url.fragment:
        raise ValueError('This authorization is scoped to the official OpenAI API')
    if url.path.rstrip('/') not in ('/v1','/v1/models','/v1/responses'):
        raise ValueError('Unrecognised GPT_BASE_URL path')
    return config['GPT_API_KEY'],'https://api.openai.com/v1/responses'


def payload(messages,*,smoke=False):
    return {'model':MODEL,'reasoning':{'effort':EFFORT},'input':messages,
        'text':{'format':{'type':'json_object'}},'store':False,
        'max_output_tokens':4096 if smoke else MAX_OUTPUT_TOKENS}


def usage(response):
    u=response.get('usage') or {}
    def number(value):
        return isinstance(value,int) and not isinstance(value,bool) and value>=0
    if not all(number(u.get(k)) for k in ('input_tokens','output_tokens','total_tokens')):
        return None
    if u['input_tokens']+u['output_tokens']!=u['total_tokens']:return None
    reasoning=(u.get('output_tokens_details') or {}).get('reasoning_tokens')
    cached=(u.get('input_tokens_details') or {}).get('cached_tokens')
    if reasoning is not None and (not number(reasoning) or reasoning>u['output_tokens']):return None
    if cached is not None and (not number(cached) or cached>u['input_tokens']):return None
    return {'prompt_tokens':u['input_tokens'],'completion_tokens':u['output_tokens'],
        'total_tokens':u['total_tokens'],'reasoning_tokens':reasoning,'cached_input_tokens':cached,
        'reasoning_is_subset_of_output_not_added_twice':True,'raw_usage':u}


def output_text(response):
    return ''.join(c.get('text','') for item in response.get('output',[]) if item.get('type')=='message'
        for c in item.get('content',[]) if c.get('type')=='output_text')


def save_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as out:json.dump(value,out,indent=2,sort_keys=True,allow_nan=False);out.write('\n')


def transport(body,key,endpoint,prefix,timeout=180):
    prefix=Path(prefix)
    request_path=Path(str(prefix)+'.request.json');response_path=Path(str(prefix)+'.response.json')
    receipt_path=Path(str(prefix)+'.transport.json')
    if any(p.exists() for p in (request_path,response_path,receipt_path)):
        raise ValueError('Request prefix already exists; no implicit retry')
    save_new(request_path,body)
    started=time.time();status=None;raw=b'';error=None;request_id=None
    data=json.dumps(body,ensure_ascii=False).encode()
    req=urllib.request.Request(endpoint,data=data,headers={'Authorization':'Bearer '+key,
        'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as reply:
            status=reply.status;request_id=reply.headers.get('x-request-id');raw=reply.read()
    except urllib.error.HTTPError as exc:
        status=exc.code;request_id=exc.headers.get('x-request-id');raw=exc.read();error='HTTP '+str(exc.code)
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc).replace(key,'<redacted>')
    text=raw.decode('utf-8',errors='replace').replace(key,'<redacted>')
    try:response=json.loads(text)
    except ValueError:response={'unparseable_body':text,'error':{'message':error or 'invalid JSON response'}}
    save_new(response_path,response)
    receipt={'started':started,'ended':time.time(),'wall_s':time.time()-started,'endpoint':endpoint,
        'http_status':status,'request_id':request_id,'request_bytes':len(data),
        'request_sha256':hashlib.sha256(data).hexdigest(),'error':error,'retries':0,
        'usage_status':'KNOWN' if usage(response) is not None else 'UNKNOWN'}
    save_new(receipt_path,receipt)
    return response,receipt


def smoke():
    key,endpoint=credentials()
    body=payload([{'role':'system','content':'This is a connectivity smoke test. Return one JSON object only.'},
        {'role':'user','content':'Return {"operation":"stop","arguments":{"reason":"smoke_ok"}} exactly. No tools.'}],smoke=True)
    response,receipt=transport(body,key,endpoint,ROOT/'smoke/request-001')
    tokens=usage(response);text=output_text(response)
    try:decision=json.loads(text)
    except ValueError:decision=None
    passed=(receipt['http_status']==200 and response.get('model')==MODEL and
        response.get('status')=='completed' and response.get('reasoning',{}).get('effort')==EFFORT and
        tokens is not None and decision=={'operation':'stop','arguments':{'reason':'smoke_ok'}})
    result={'passed':passed,'model_requested':MODEL,'model_reported':response.get('model'),
        'reasoning_requested':EFFORT,'reasoning_reported':response.get('reasoning',{}).get('effort'),
        'response_status':response.get('status'),'http_status':receipt['http_status'],
        'usage':tokens,'request_id':receipt['request_id'],'wall_s':receipt['wall_s'],
        'EDA_flows':0,'formal_task_episode':False,'requests':1,'retries':0}
    save_new(ROOT/'smoke/result.json',result)
    print(json.dumps(result));return passed


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['smoke']);args=ap.parse_args()
    raise SystemExit(0 if smoke() else 1)
