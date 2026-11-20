"""Bounded official-deck test: add only -hyper to the Calibre command."""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,time
p=argparse.ArgumentParser()
p.add_argument('name');p.add_argument('--gds',required=True);p.add_argument('--baseline',required=True)
a=p.parse_args()
root=Path('/proj/workarea/yangao/asap7_qualification_20261116')
base=root/'calibre_runs'/a.baseline/'drc'
gds=Path(a.gds).resolve()
deck=root/'private_rules/calibredecks_r1p7/calibre/ruledirs/drc/drcRules_calibre_asap7.rul'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
previous=json.loads((base/'invocation.json').read_text())
assert previous['gds_sha256']==sha(gds)
assert previous['deck_sha256']==sha(deck)
assert previous['flat'] is False
out=root/'calibre_runs'/a.name/'drc';out.mkdir(parents=True,exist_ok=False)
(out/'test.gds').symlink_to(gds)
wrapper=(base/'run.svrf').read_bytes();(out/'run.svrf').write_bytes(wrapper)
old=['calibre','-drc','-hier','-turbo','4','run.svrf']
new=['calibre','-drc','-hier','-turbo','4','-hyper','run.svrf']
command=['/usr/bin/time','-v','timeout','--kill-after=5','60',*new]
metadata={'baseline_name':a.baseline,'baseline_command':old,'probe_command':new,
          'wrapper_command':command,'change':'only add -hyper to Calibre invocation',
          'timeout_s':60,'threads':4,'gds':str(gds),'gds_sha256':sha(gds),
          'deck_sha256':sha(deck),'wrapper_sha256':sha(out/'run.svrf'),
          'same_gds_and_deck_as_baseline':True,'same_svrf_bytes_as_baseline':True,
          'rule_exclusions':[],'maximum_results':'ALL'}
(out/'probe_setup.json').write_text(json.dumps(metadata,indent=2)+'\n')
env=dict(os.environ,MGLS_LICENSE_FILE='27020@sv01',pdk_path='/proj/workarea/LIB/asap7/asap7_pdk_r1p7')
env.update(dict(zip(['VAR_M4_YOFFSET','VAR_M5_XOFFSET','VAR_M6_YOFFSET','VAR_M7_XOFFSET'],previous['offsets'])))
start=time.monotonic()
with (out/'run.log').open('w') as log:
 proc=subprocess.run(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT)
text=(out/'run.log').read_text();ops=re.findall(r'OPS COMPLETE = (\d+) OF (\d+)\s+ELAPSED TIME = (\d+)',text)
get=lambda pattern:re.search(pattern,text)
receipt={**metadata,'process_exit':proc.returncode,'wall_s':time.monotonic()-start,
 'last_operation_progress':list(map(int,ops[-1][:2])) if ops else None,
 'time_at_last_operation_s':int(ops[-1][2]) if ops else None,
 'passed_operation_113':any(int(x[0])>=113 for x in ops),
 'result':'INCOMPLETE' if proc.returncode else 'completed_unparsed',
 'cpu_user_s':float(get(r'User time \(seconds\): ([\d.]+)')[1]),
 'cpu_system_s':float(get(r'System time \(seconds\): ([\d.]+)')[1]),
 'peak_rss_kib':int(get(r'Maximum resident set size \(kbytes\): (\d+)')[1])}
report=out/'drc_report'
if report.exists():
 txt=report.read_text();checks=re.search(r'TOTAL DRC RuleChecks Executed:\s*(\d+)',txt)
 count=re.search(r'TOTAL DRC Results Generated:\s*(\d+)(?:\s*\((\d+)\))?',txt)
 if checks and count and proc.returncode==0:
  receipt.update(rulechecks=int(checks[1]),results=int(count[1]),expanded_results=int(count[2] or count[1]))
  receipt['result']='clean' if receipt['expanded_results']==0 else 'violations'
(out/'invocation.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
