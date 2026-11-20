"""Run the uploaded unmodified ASAP7 Calibre decks in an isolated 216 workspace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

parser=argparse.ArgumentParser()
parser.add_argument('--check',choices=['drc','lvs'],required=True)
parser.add_argument('--name',required=True)
parser.add_argument('--gds',required=True)
parser.add_argument('--top',default='INVx1_ASAP7_75t_R')
parser.add_argument('--cdl',default='/proj/workarea/yangao/asap7_qualification_20261116/third_party/asap7sc7p5t_28_R.cdl')
args=parser.parse_args()
root=Path('/proj/workarea/yangao/asap7_qualification_20261116')
run=root/'calibre_runs'/args.name/args.check
run.mkdir(parents=True,exist_ok=True)
deck=root/'private_rules/calibredecks_r1p7/calibre/ruledirs'/args.check/(args.check+'Rules_calibre_asap7.rul')
gds=Path(args.gds).resolve()
assert deck.is_file() and gds.is_file(),(str(deck),str(gds))
lines=[f'LAYOUT PATH "{gds}"',f'LAYOUT PRIMARY "{args.top}"','LAYOUT SYSTEM GDSII']
if args.check=='drc':
    lines.extend([f'DRC RESULTS DATABASE "{run}/results.db" ASCII',f'DRC SUMMARY REPORT "{run}/summary.rpt" HIER','DRC MAXIMUM RESULTS ALL'])
else:
    lines.extend([f'SOURCE PATH "{args.cdl}"',f'SOURCE PRIMARY "{args.top}"','SOURCE SYSTEM SPICE',
                  f'LVS REPORT "{run}/lvs.report"','LVS REPORT MAXIMUM ALL',f'MASK SVDB DIRECTORY "{run}/svdb" QUERY'])
lines.append(f'INCLUDE "{deck}"')
runset=run/'run.svrf';runset.write_text('\n'.join(lines)+'\n')
env=os.environ.copy();env['pdk_path']='/proj/workarea/LIB/asap7/asap7_pdk_r1p7';env['PDK_DIR']=env['pdk_path']
start=time.time()
with (run/'run.log').open('w')as log:
    result=subprocess.run(['calibre','-'+args.check,'-hier','-turbo','4',str(runset)],cwd=run,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
receipt={'check':args.check,'name':args.name,'returncode':result.returncode,'elapsed_seconds':round(time.time()-start,3),
 'gds':str(gds),'top':args.top,'source_cdl':args.cdl if args.check=='lvs'else None,'rule_file':str(deck),'log':str(run/'run.log'),
 'rule_selection':'all; no DRC SELECT or EXCLUDE statements','verdict':'not_assessed_by_returncode'}
(run/'invocation.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt),flush=True)
tail=(run/'run.log').read_text(errors='replace').splitlines()
print('\n'.join(tail[-28:]),flush=True)
