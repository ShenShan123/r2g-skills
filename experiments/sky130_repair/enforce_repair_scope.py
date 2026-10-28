"""Drain abandoned cases from the already-loaded old policy without a restart.

Only case-owned containers are cancelled. Exit once the existing supervisor
finishes, or a new campaign manifest proves the scope is enforced at selection.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time
from repair_scope import load, cancelled_container


def identity(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return None if fields[0]=='Z' else fields[19]
    except FileNotFoundError:return None


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--supervisor-pid',type=int,required=True);a=p.parse_args()
    root=a.root.resolve();lock=(root/'repair_scope_guard.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    token=identity(a.supervisor_pid)
    cmd=Path(f'/proc/{a.supervisor_pid}/cmdline').read_bytes().split(b'\0')
    if not token or str(root/'tools/continue_campaign_goal.py').encode() not in cmd:raise ValueError('expected supervisor absent')
    policy=root/'repair_scope.json';digest=hashlib.sha256(policy.read_bytes()).hexdigest();decisions=load(policy)
    external=json.loads(policy.read_text()).get('externally_managed_designs',[])
    if any(decisions.get(d)!='candidate' for d in external):raise ValueError('invalid external assignment')
    decisions.update({d:'external' for d in external})
    def record(event,**data):
        value=dict(at=time.strftime('%FT%T%z'),event=event,scope_sha256=digest,**data)
        with (root/'repair_scope_guard_events.jsonl').open('a') as f:f.write(json.dumps(value)+'\n')
        print(json.dumps(value),flush=True)
    record('guard_started',supervisor_pid=a.supervisor_pid)
    while identity(a.supervisor_pid)==token:
        if hashlib.sha256(policy.read_bytes()).hexdigest()!=digest:raise ValueError('scope changed during guard')
        manifest=json.loads((root/'campaign/manifest.json').read_text())
        if manifest.get('repair_scope_sha256')==digest:
            record('new_policy_enforces_scope');return
        ids=subprocess.check_output(['podman','ps','-q','--filter','name=s130repair_'],text=True).split()
        for cid in ids:
            result=subprocess.run(['podman','inspect',cid],capture_output=True,text=True)
            if result.returncode:continue  # A natural exit is not a reason to restart.
            info=json.loads(result.stdout)[0];design=cancelled_container(info,decisions,root)
            if not design:continue
            record('cancel_abandoned_case' if decisions[design]=='abandon' else 'cancel_duplicate_external_case',
                   design=design,container_id=info['Id'],container_name=info['Name'])
            result=subprocess.run(['podman','rm','-f',info['Id']],capture_output=True,text=True,timeout=120)
            record('cancel_result',design=design,returncode=result.returncode,stderr=result.stderr[-1000:])
        time.sleep(5)
    record('supervisor_finished')


if __name__=='__main__':main()
