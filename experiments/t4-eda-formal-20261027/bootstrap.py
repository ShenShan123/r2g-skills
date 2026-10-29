"""Create isolated formal EDA host snapshots; never copy integration memories."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'t4-cross-host-online-20261026'


def main():
    receipt=ROOT/'inputs/bootstrap.json'
    if receipt.exists():raise ValueError('snapshot already exists')
    for folder in ('adapters','tests','inputs','reports','bin','hosts','qualification'):
        (ROOT/folder).mkdir(exist_ok=True)
    shutil.copytree(OLD/'code',ROOT/'code',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    names=['bridge.py','meter.py','provider_clients.py','hooks.py','native_evidence.py',
           'native_serialization.py','local_reuse.py','fixer_runtime.py','tool_sandbox.py']
    for name in names:shutil.copy2(OLD/'adapters'/name,ROOT/'adapters'/name)
    commits={}
    for name in ('rtlfixer','rtlscout','verilog-eval'):
        subprocess.run(['git','clone','--no-hardlinks',str(OLD/'sources'/name),str(ROOT/'sources'/name)],check=True)
        commits[name]=subprocess.check_output(['git','-C',str(ROOT/'sources'/name),'rev-parse','HEAD'],text=True).strip()
    for name in ('iverilog','vvp','yosys','verilator'):
        (ROOT/'bin'/name).symlink_to('../adapters/tool_sandbox.py')
    (ROOT/'adapters/tool_sandbox.py').chmod(0o755)
    (ROOT/'inputs/empty.env').touch()
    copied={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for tree in ('adapters','code') for p in (ROOT/tree).rglob('*') if p.is_file()}
    data={'created':time.time(),'native_commits':commits,'initial_stores':'EMPTY, isolated per host and arm',
          'integration_results_or_memory_copied':False,'shared_TEHM_core_changed':False,'copied_files':copied,
          'rtlrepair_commit':subprocess.check_output(['git','-C',str(ROOT/'sources/rtl-repair'),'rev-parse','HEAD'],text=True).strip()}
    receipt.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'status':'SNAPSHOTTED','native_commits':commits,'copied_files':len(copied)}))


if __name__=='__main__':main()
