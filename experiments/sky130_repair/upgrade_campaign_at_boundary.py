#!/usr/bin/env python3
"""Switch the queued policy only between complete designs, preserving compute.

The old runner has no drain API. Freeze it briefly to inspect all threads'
children and every unfinished receipt. Terminate only when no flow/signoff is
in flight; incomplete source preparation is retained and retried in a new
attempt directory. The existing supervisor then exits on its child's nonzero
return, releasing its campaign lock before its replacement starts.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


def identity(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return None if fields[0]=='Z' else fields[19]
    except FileNotFoundError:return None


def children(pid):
    result=set()
    for p in Path(f'/proc/{pid}/task').glob('*/children'):
        try:result.update(int(x) for x in p.read_text().split())
        except FileNotFoundError:pass
    return result


def in_flight(root):
    for p in (root/'campaign/cases').glob('*/progress.json'):
        if (p.parent/'repair.json').exists():continue
        data=json.loads(p.read_text())
        if any(a.get('status') in ('flow_running','signoff_running') for a in data.get('attempts',[])):
            return True
    return False


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--worker-pid',type=int,required=True);p.add_argument('--supervisor-pid',type=int,required=True)
    p.add_argument('--wait-for-receipt',required=True)
    p.add_argument('--poll-interval',type=float,default=.1)
    a=p.parse_args();root=a.root
    if a.poll_interval<.05:p.error('poll interval must be at least 0.05 seconds')
    lock=(root/'policy_upgrade.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    worker_id=identity(a.worker_pid);supervisor_id=identity(a.supervisor_pid)
    if not worker_id or not supervisor_id:raise RuntimeError('expected live runner/supervisor not found')
    expected={a.worker_pid:str(root/'tools/run_campaign_goal.py'),
              a.supervisor_pid:str(root/'tools/continue_campaign_goal.py')}
    for pid,script in expected.items():
        if script.encode() not in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
            raise RuntimeError('process identity does not match expected campaign script')
    def state(phase,**extra):
        data=dict(phase=phase,at=time.strftime('%FT%T%z'),worker_pid=a.worker_pid,**extra)
        tmp=root/'policy_upgrade.tmp';tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(root/'policy_upgrade.json')
        print(json.dumps(data),flush=True)
    state('waiting_for_completed_design',receipt=a.wait_for_receipt)
    receipt=root/'campaign/cases'/a.wait_for_receipt/'repair.json'
    while identity(a.worker_pid)==worker_id and identity(a.supervisor_pid)==supervisor_id:
        if not receipt.is_file() or in_flight(root):time.sleep(a.poll_interval);continue
        os.kill(a.worker_pid,signal.SIGSTOP)
        stopped=True
        try:
            # Allow delivery of the stop to the entire thread group before
            # inspecting a stable state. Recheck PID identity after the signal.
            for _ in range(50):
                status=Path(f'/proc/{a.worker_pid}/stat').read_text().rsplit(')',1)[1].split()[0]
                if status=='T':break
                time.sleep(.01)
            else:raise RuntimeError('runner did not reach stopped state')
            if identity(a.worker_pid)!=worker_id or children(a.worker_pid) or in_flight(root):
                os.kill(a.worker_pid,signal.SIGCONT);stopped=False
                time.sleep(a.poll_interval)
                continue
            state('safe_boundary_verified',children=[],in_flight=False)
            os.kill(a.worker_pid,signal.SIGTERM)
            os.kill(a.worker_pid,signal.SIGCONT);stopped=False
            # The supervisor returns an error and releases the existing lock;
            # do not replace it or start another runner while it remains live.
            for _ in range(100):
                if identity(a.supervisor_pid)!=supervisor_id and identity(a.worker_pid)!=worker_id:break
                time.sleep(.1)
            else:raise RuntimeError('old campaign did not exit; no replacement launched')
            canary_pid=int((root/'canary.pid').read_text())
            with (root/'campaign_goal.log').open('ab') as log:
                proc=subprocess.Popen(['python3',str(root/'tools/continue_campaign_goal.py'),
                    '--root',str(root),'--canary-pid',str(canary_pid)],stdin=subprocess.DEVNULL,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            (root/'campaign.pid').write_text(str(proc.pid)+'\n')
            version=re.search(r'^POLICY_REVISION=(\d+)',(root/'tools/run_campaign_goal.py').read_text(),re.M)
            state('replacement_launched',supervisor_pid=proc.pid,
                  policy_revision=int(version.group(1)) if version else None)
            return
        finally:
            if stopped and identity(a.worker_pid)==worker_id:os.kill(a.worker_pid,signal.SIGCONT)
        time.sleep(.2)
    state('original_runner_or_supervisor_exited',note='No running process replaced; staged policy remains available for normal next phase')


if __name__=='__main__':main()
