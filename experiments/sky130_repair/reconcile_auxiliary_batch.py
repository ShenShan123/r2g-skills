#!/usr/bin/env python3
"""Transfer explicit, completed auxiliary wins through the checked importer."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from strict_signoff import inspect_run


def main():
    p=argparse.ArgumentParser();p.add_argument('cases',nargs='+');p.add_argument('--arm',choices=['repair','recheck'],default='repair');a=p.parse_args()
    root=Path('/tmp/sky130_aux208');remote='/proj/workarea/yangao/sky130_repair_20261026'
    cases=json.loads((root/'cases.json').read_text())
    for cid in a.cases:
        if not re.fullmatch(r'C\d{5}',cid):raise ValueError('invalid case ID')
        design=cases[cid]['run_name'].removesuffix('__sky130hd__p5')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',design):raise ValueError('unsafe design name')
        receipt=root/('results' if a.arm=='repair' else 'results_recheck')/'cases'/design/'repair.json'
        exported=root/'export'/design
        if json.loads(receipt.read_text())['status']!='dual_clean' and not exported.exists():
            raise ValueError('no completed strict win or explicitly prepared corrected-signoff export')
        reconciled=receipt.parent/'reconciled.json'
        if reconciled.exists():raise ValueError('already reconciled; do not count or import again')
        if not exported.exists():
            subprocess.run([sys.executable,str(Path(__file__).with_name('prepare_auxiliary_import.py')),'--case',cid,'--arm',a.arm],check=True)
        if json.loads((exported/'repair.json').read_text())['status']!='dual_clean' or not inspect_run(exported/'accepted_run')['dual_clean']:
            raise ValueError('prepared export does not prove strict acceptance')
        for rel,digest in json.loads((exported/'transfer_manifest.json').read_text()).items():
            path=exported/rel
            if not path.resolve().is_relative_to(exported) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('prepared evidence changed')
        archive=root/(design+'.tar.gz');digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        dest=remote+'/'+cid+'_aux208.tar.gz'
        with archive.open('rb') as f:
            subprocess.run(['ssh','memlab216','cat > '+shlex.quote(dest)],stdin=f,check=True)
        args=['env','-u','PYTHONHOME','-u','PYTHONPATH','-u','PYTHONSAFEPATH','python3','-P',
              remote+'/tools/import_auxiliary_result.py','--archive',dest,'--sha256',digest,'--design',design]
        result=subprocess.run(['ssh','memlab216',shlex.join(args)],text=True,capture_output=True,check=True)
        record=json.loads(result.stdout)
        if record.get('imported')!=design or record.get('dual_clean') is not True:raise ValueError('unexpected import result')
        record['archive_sha256']=digest;reconciled.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)


if __name__=='__main__':main()
