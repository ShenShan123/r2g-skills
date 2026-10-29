#!/usr/bin/env python3
"""Meter a strict-signoff child invocation; no checker retries or policy edits."""
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    project,name,*command=sys.argv[1:]
    if not command or name not in ('drc','lvs','rcx'):raise ValueError('Invalid checker boundary')
    start=time.time();before=resource.getrusage(resource.RUSAGE_CHILDREN)
    result=subprocess.run(command)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    data={'source':'strict_signoff','checker':name,'tool':Path(command[1] if command[0]=='bash' else command[0]).name,
          'exit_status':result.returncode,'started':start,'ended':time.time(),'elapsed_s':time.time()-start,
          'cpu_s':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
          'cost_nested_in_parent_tool_call':True,'implicit_retries':0}
    ledger=Path(__file__).with_name('resource_ledger.py')
    emitted=subprocess.run([sys.executable,str(ledger),'event',project,'--type','checker_run','--json',json.dumps(data)])
    if emitted.returncode:return 76
    return result.returncode


if __name__=='__main__':sys.exit(main())
