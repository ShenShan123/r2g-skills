"""Verify constructed controls locally. Missing builds are NOT_READY, not passes.

No EDA/model calls. Native embedding/retrieval compute is measured. Online
auxiliary provider verification and seven real DEV episodes remain separate gates.
"""
import argparse
import json
from pathlib import Path
import resource
import time

import t2a_adapters as A
import t2a_construct as C
import t2a_runtime as T


def serialized_probe_bytes(H,query,context):
    """Exact client JSON encoding on a local probe, NOT a real DEV observation."""
    messages=[{'role':'system','content':H.LLM_SYSTEM.replace('{platform}','sky130hd')},
              {'role':'user','content':'Current evidence:\n'+query+'\nReturn one repair candidate.'+
                 '\nFrozen historical memory (not current-task evidence):\n'+context}]
    body=json.dumps({'model':H.LLM_API_MODEL,'temperature':0.3,'max_tokens':H.LLM_MAX_TOKENS,
                     'stream':False,'thinking':{'type':'disabled'},
                     'response_format':{'type':'json_object'},'messages':messages}).encode()
    if len(body)>H.LLM_MAX_INPUT_BYTES:
        raise ValueError('Serialized local probe exceeds common online ceiling')
    return len(body)


def verify(root,output):
    output.mkdir(parents=True,exist_ok=False)
    H=C.Q.module('t2a_local_input_probe',Path(__file__).with_name('r2g_memory_eval.py'))
    T.configure_online(H)  # no credentials read, no network request
    units=A.load_source(C.SOURCE)
    situation=A.situation(next(u for u in units if A.positive(u)))
    query='sky130hd detailed routing congestion timeout; retain clock and signoff requirements'
    rows=[]
    for arm in C.ARMS:
        p=root/(arm+'.json')
        if not p.exists():
            rows.append({'arm':arm,'status':'NOT_READY','reason':'construction incomplete'});continue
        start,cpu=time.time(),time.process_time()
        try:
            data=json.loads(p.read_text())
            if data.get('source_sha256')!=A.SOURCE_SHA or data.get('status')!='FROZEN':
                raise ValueError('Wrong source or unsealed representation')
            if arm=='RECIPE':
                recipes=A.build_recipes(units)
                if recipes!=data['recipes']:raise ValueError('Recipe evidence content differs')
                count=sum(len(r['unit_ids']) for r in recipes)
                if count!=501:raise ValueError('SOURCE successes omitted/duplicated')
                detail={'recipes':len(recipes),'verified_positive_units':count}
            else:
                memory=A.PromptMemory(arm,root)
                # Local query stub ONLY for retrieval smoke. Not a pass for the
                # real-provider A-MEM auxiliary path or a zero-online-LLM result.
                context,ids=memory.context(query,situation,auxiliary=lambda q:['route','congestion'],
                                           max_bytes=T.MEMORY_CONTEXT_BYTES)
                probe_bytes=serialized_probe_bytes(H,query,context)
                if arm=='AWM':
                    for key in data['workflows']:
                        values=json.loads(key)
                        other,_=memory.context(query,dict(zip(A.GROUP_KEYS,values)),max_bytes=T.MEMORY_CONTEXT_BYTES)
                        probe_bytes=max(probe_bytes,serialized_probe_bytes(H,query,other))
                detail={'context_bytes':len(context.encode()),'retrieved_ids':ids,
                        'max_serialized_local_probe_bytes':probe_bytes,
                        'probe_kind':'SOURCE_QUERY_STUB_NOT_REAL_DEV_OBSERVATION',
                        'auxiliary_provider_exercised':False,'native_embedding':memory.index is not None}
            rows.append({'arm':arm,'status':'PASS_LOCAL','representation_sha256':C.Q.digest(p),
                   'wall_s':time.time()-start,'cpu_s':time.process_time()-cpu,**detail})
        except Exception as exc:
            rows.append({'arm':arm,'status':'FAIL_LOCAL','reason':str(exc),'wall_s':time.time()-start})
    receipt={'source_sha256':A.SOURCE_SHA,'records':rows,'llm_requests':0,'eda_flows':0,
             'online_policy':T.online_policy(),
             'all_controls_local_ready':all(r['status']=='PASS_LOCAL' for r in rows),
             'real_seven_arm_dev_validated':False,'formal_launch_allowed':False,
             'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    C.write(output/'receipt.json',receipt)
    print(json.dumps(receipt,sort_keys=True),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();verify(args.root.resolve(),args.output.resolve())
