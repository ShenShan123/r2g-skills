#!/usr/bin/env python3
"""Install a read-only export while the auxiliary execution slot is idle."""
import argparse
import base64
import fcntl
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('bundle',type=Path);a=p.parse_args()
    root=Path('/tmp/sky130_aux208')
    locks=[]
    for name in ('worker.lock','review_worker.lock','worker_2.lock','worker_3.lock','worker_4.lock'):
        lock=(root/name).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
    bundle=json.loads(a.bundle.read_text());hashes=json.loads((root/'original_files.json').read_text())
    cases=json.loads((root/'cases.json').read_text());pending=[]
    for rel,item in bundle['files'].items():
        path=root/rel;raw=base64.b64decode(item['data'],validate=True)
        if not path.resolve().is_relative_to(root) or Path(rel).parts[0] not in ('baseline','package'):
            raise ValueError('input outside frozen input scope')
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('export hash mismatch')
        if path.exists() and path.read_bytes()!=raw:raise ValueError('existing frozen input differs')
        if rel in hashes and hashes[rel]!=item['sha256']:raise ValueError('existing input manifest differs')
        pending.append((path,raw));hashes[rel]=item['sha256']
    for cid,value in bundle['cases'].items():
        if cid in cases and cases[cid]!=value:raise ValueError('existing case identity differs')
        cases[cid]=value
    for path,raw in pending:
        if not path.exists():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    for name,data in [('original_files.json',hashes),('cases.json',cases)]:
        path=root/name;temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)
    print(json.dumps(dict(prepared=list(bundle['cases']),files=len(pending))))


if __name__=='__main__':main()
