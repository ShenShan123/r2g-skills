"""Summarize preserved checker reports; successful process exit never implies clean."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,json,re
r=Path(__file__).resolve().parent;e=r/'calibre_evidence/gcd_full'
drc=(e/'drc/drc_report').read_text();lvs=(e/'lvs_device_complete/mask.lvs.rep').read_text()
rules=[{'rule':m[0],'results':int(m[1]),'parenthesized_results':int(m[2])} for m in re.findall(r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)\s*\((\d+)\)',drc,re.M)]
assert len(rules)==334
violations=[x for x in rules if x['results']]
assert sum(x['results'] for x in rules)==1339
assert sum(x['parenthesized_results'] for x in rules)==1815
assert 'INCORRECT' in lvs and 'Ports:             56        56' in lvs
assert 'Nets:            2269      2269' in lvs
ppa=json.loads((r/'gcd_smoke/reports/ppa.json').read_text())
summary={'created_gmt8':datetime.now(timezone(timedelta(hours=8))).isoformat(),
'platform':'asap7','design':'gcd','default_platform_enabled':False,'qualified_dual_clean':False,
'archive_sha256':json.loads((r/'archive_manifest.json').read_text())['archive_sha256'],
'flow':{'status':'complete','clock_period_ns':1.0,'setup_wns_ns':ppa['summary']['timing']['setup_wns']/1000,'setup_tns_ns':0,'hold_wns_ns':ppa['summary']['timing']['hold_wns']/1000,'stdcell_area_um2':ppa['geometry']['stdcell_area_um2'],'stage_elapsed_sum_s':173,'orfs_report_peak_memory_mb':1096},
'drc':{'checker':'Calibre 2025.1_16.10','rulechecks_executed':len(rules),'result_count':1339,'report_parenthesized_result_count':1815,'violated_rules':violations,'status':'violations','wall_s':13.36,'max_rss_kib':113620,'result_limit':'ALL','rules_excluded':[]},
'lvs':{'checker':'Calibre 2025.1_16.10','status':'INCORRECT','layout_ports':56,'source_ports':56,'layout_nets_after_transformation':2269,'source_nets_after_transformation':2269,'layout_instances_after_transformation':2632,'source_instances_after_transformation':2632,'matched_nets':2255,'unmatched_nets_each_side':14,'wall_s':6.76,'max_rss_kib':144364,'remaining_errors':['Different numbers of component types','Connectivity errors','Property errors'],'logic_blackboxes':0,'warning':'LVS property resolution maximum exceeded; reported property examples are not exhaustive.'}}
(r/'qualification.json').write_text(json.dumps(summary,indent=2)+'\n')
files=[r/'archive_manifest.json',r/'cdl_source.json',r/'qualification.json']+list((r/'gcd_calibre_input').glob('*'))+[p for p in e.rglob('*') if p.is_file()]
(r/'evidence_sha256.json').write_text(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},indent=2)+'\n')
print(json.dumps({'checks':len(rules),'violated_rule_types':len(violations),'drc_results':1339,'lvs':summary['lvs']['status']}))
