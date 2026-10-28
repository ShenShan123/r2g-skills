#!/usr/bin/env python3
"""Use the free worker while the last canary drains, at most 16 cores total."""
import argparse
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path


def start(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except FileNotFoundError:return None


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--canary-pid',type=int,required=True)
    a=p.parse_args();root=a.root
    lock=(root/'campaign.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def state(phase,**extra):
        data=dict(phase=phase,at=time.strftime('%FT%T%z'),**extra)
        target=root/'campaign_status.json';tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(target)
        print(json.dumps(data),flush=True)
    identity=start(a.canary_pid)
    canary=root/'canary';summary=json.loads((canary/'summary.json').read_text())
    script=root/'tools/run_campaign_goal.py'
    if not script.is_file():script=root/'tools/run_campaign.py'
    cmd=[sys.executable,str(script),'--inventory',str(root/'tools/inventory_audited.json'),
         '--out',str(root/'campaign'),'--seed',str(canary)]
    if identity and summary['completed']==3 and summary['selected']==4 and summary['new_dual_clean']==3:
        active=[]
        for progress in (canary/'cases').glob('*/progress.json'):
            if not (progress.parent/'repair.json').exists():active.append(progress.parent.name)
        if len(active)!=1:
            state('blocked',reason='cannot identify exactly one remaining canary');return 1
        state('classified_repairs_alongside_canary',workers=1,canary_workers=1,cores_per_flow=8,excluded=active)
        # This child is awaited before any two-worker phase. A canary exit cannot
        # start a competing batch while this single-worker queue is still live.
        rc=subprocess.call(cmd+['--workers','1','--exclude-design',active[0]])
        if rc:state('error',failed_phase='classified_repairs_alongside_canary',returncode=rc);return rc
    state('waiting_for_canary',pid=a.canary_pid)
    while identity and start(a.canary_pid)==identity:time.sleep(15)
    summary=json.loads((canary/'summary.json').read_text())
    if summary['completed']!=4 or summary['new_dual_clean']<3:
        state('blocked',reason='initial canary incomplete or insufficient verified repairs');return 1
    cmd+=['--workers','2']
    for phase,extra in [('classified_repairs',[]),('timeout_and_memory_repairs',['--include-expensive'])]:
        state(phase,workers=2,cores_per_flow=8)
        rc=subprocess.call(cmd+extra)
        if rc:state('error',failed_phase=phase,returncode=rc);return rc
    state('complete',summary=json.loads((root/'campaign/summary.json').read_text()))
    return 0


if __name__=='__main__':sys.exit(main())
