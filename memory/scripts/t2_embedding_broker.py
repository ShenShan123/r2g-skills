#!/usr/bin/env python3
"""Local CPU embedding process, using the existing pinned ML Python environment.

The R2G/controller Python and EDA environment do not change. No provider calls,
network or retries. Every embedding operation records worker CPU/wall/RSS.
"""
import atexit
import contextlib
import ctypes
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time


class RemoteIndex:
    def __init__(self,model,texts=None):
        env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
             'HF_HUB_DISABLE_IMPLICIT_TOKEN':'1','CUDA_VISIBLE_DEVICES':'','PYTHONDONTWRITEBYTECODE':'1',
             'LD_LIBRARY_PATH':os.environ['R2G_EMBEDDING_LIBRARY_PATH']}
        python=env.pop('R2G_EMBEDDING_PYTHON')
        self.metrics=os.environ.get('R2G_EMBEDDING_METRICS_FILE')
        self.process=subprocess.Popen([python,str(Path(__file__).resolve()),str(os.getpid())],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env,bufsize=1)
        atexit.register(self.close)
        self._request('init',model=model,texts=texts)

    def _request(self,op,**data):
        self.process.stdin.write(json.dumps({'op':op,**data})+'\n');self.process.stdin.flush()
        raw=self.process.stdout.readline()
        if not raw:raise RuntimeError('local embedding process ended unexpectedly')
        reply=json.loads(raw)
        if self.metrics:
            path=Path(self.metrics);path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a') as f:f.write(json.dumps({'type':'local_embedding','op':op,'ts':time.time(),
                **reply.get('metrics',{}),'ok':reply.get('ok')})+'\n')
        if not reply.get('ok'):raise RuntimeError('local embedding failure: '+reply.get('error','UNKNOWN'))
        return reply.get('result')

    def search(self,query,k):return self._request('search',query=query,k=k)
    def add(self,texts):return self._request('add',texts=texts)
    def rebuild(self,texts):return self._request('rebuild',texts=texts)
    def encode(self,texts):return self._request('encode',texts=texts)

    def close(self):
        p=self.process
        if p.poll() is not None:return
        p.stdin.close()
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()


def worker(parent):
    # Only this freshly created child is signalled if its owner dies.
    libc=ctypes.CDLL(None);libc.prctl(1,signal.SIGTERM,0,0,0)
    if os.getppid()!=parent:return
    index=None
    for line in sys.stdin:
        start,cpu=time.monotonic(),time.process_time()
        try:
            q=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if q['op']=='init':
                    from t2a_adapters import DenseIndex
                    index=DenseIndex(q['model'],q.get('texts'));result=None
                elif q['op']=='search':result=index.search(q['query'],q['k'])
                elif q['op']=='add':result=index.add(q['texts'])
                elif q['op']=='rebuild':result=index.rebuild(q['texts'])
                elif q['op']=='encode':result=index.model.encode(q['texts'],normalize_embeddings=True,
                    show_progress_bar=False).tolist()
                else:raise ValueError('unknown embedding operation')
            reply={'ok':True,'result':result}
        except Exception as exc:reply={'ok':False,'error':str(exc)}
        reply['metrics']={'wall_s':time.monotonic()-start,'cpu_s':time.process_time()-cpu,
                          'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'pid':os.getpid(),
                          'model_requests':0,'tokens':0,'EDA_flows':0}
        print(json.dumps(reply),flush=True)

if __name__=='__main__':worker(int(sys.argv[1]))
