"""Registered Figure 2 wrapper; the existing checked executor remains native."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]


def provider_global_fault(reply):
    error=reply.get('error') or {}
    text=json.dumps(error).lower()
    if any(t in text for t in ('insufficient_balance','insufficient balance','authentication_error',
            'invalid_api_key','invalid api key','permission_denied','permission denied','unauthorized')):
        return 'provider payment/permission failure'
    if reply.get('model') and reply['model'] not in ('deepseek-flash','deepseek-v4.1-flash'):
        return 'unapproved provider model identity'
    if reply.get('http_status',reply.get('status')) in (401,402,403):
        return 'provider payment/permission failure'
    return None


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--task',required=True);p.add_argument('--arm',choices=['TM0','RECIPE'],required=True)
    args=p.parse_args()
    import experiment as C
    C.validate(args.root)
    reg=json.loads((args.root/'registration.json').read_text())
    variant=reg['figure2_variant']
    import t2_agent_request_resume as R
    R.install_online()
    import t2a_batch as B
    import t2_agent_memories as M
    import t2_agent_runtime as A
    import t2_agent_dispatch as D
    from figure2_policy import configure_memory
    make_memory=M.make_memory
    run=A.run
    original_call=D.OnlineGateway.call
    def guarded_call(gateway,*a,**k):
        if variant=='RECIPE':raise D.IntegrityStop('pure RECIPE provider boundary is disabled')
        if (ROOT/'GLOBAL-STOP.json').exists():raise D.IntegrityStop('Figure 2 global provider/integrity stop')
        try:return original_call(gateway,*a,**k)
        finally:
            project=Path(gateway.project)
            for reply in project.glob('agent-decision-*.try0.response.json'):
                try:reason=provider_global_fault(json.loads(reply.read_text()))
                except (OSError,ValueError):continue
                if reason:
                    C.record(ROOT/'GLOBAL-STOP.json',{'reason':reason,
                        'source':str(reply),'usage':'preserved in original response and ledger','created':time.time()})
                    raise D.IntegrityStop(reason)
    D.OnlineGateway.call=guarded_call
    def make(*a,**k):return configure_memory(make_memory(*a,**k),variant)
    M.make_memory=make
    def execute(*a,**k):
        result=run(*a,**k)
        memory=k['memory']
        result['figure2_variant']=variant
        result['local_memory_attempts']=memory.figure2_attempts
        if variant=='RECIPE':result['stop_reason']=memory.figure2_stop or result['stop_reason']
        return result
    A.run=execute
    B.episode(args.root.resolve(),args.task,args.arm,1,'TEST')


if __name__=='__main__':main()
