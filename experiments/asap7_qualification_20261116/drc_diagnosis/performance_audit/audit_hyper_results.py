"""Compare completed rule names, never scheduled operation counts."""
from pathlib import Path
import hashlib,json,re
q=Path(__file__).resolve().parent.parent.parent
out=Path(__file__).resolve().parent
names=['asap7_accel_hyper_aes_20261116','asap7_accel_hyper_gcd_20261116']
control=q/'calibre_evidence'/names[1]/'drc'
pattern=r'DRC RuleCheck (\S+) COMPLETED\. Number of Results = (\d+)(?: \((\d+)\))?'
all_rules={m[0] for m in re.findall(pattern,(control/'run.log').read_text())}
assert len(all_rules)==334
records={}
for name in names:
 p=q/'calibre_evidence'/name/'drc'
 text=(p/'run.log').read_text();matches=re.findall(pattern,text)
 completed={m[0] for m in matches};missing=sorted(all_rules-completed)
 original=p/'invocation.original.json'
 if not original.exists():original.write_bytes((p/'invocation.json').read_bytes())
 receipt=json.loads(original.read_text())
 receipt.pop('passed_operation_113',None)
 receipt.update(operation_index_not_comparable=True,
   operation_counter_note='-hyper reorders independent operations; numeric progress cannot prove the ACTIVE width batch completed.',
   completed_rulechecks=len(completed),missing_rulechecks=missing,
   missing_rulechecks_sha256=hashlib.sha256(('\n'.join(missing)+'\n').encode()).hexdigest(),
   rule_list_hash_encoding='UTF-8 sorted rule names, newline separated, including trailing newline',
   active_w1_completed='ACTIVE.W.1' in completed,active_w2_completed='ACTIVE.W.2' in completed,
   observed_violations_in_completed_rules={m[0]:int(m[2] or m[1]) for m in matches if int(m[1])},
   resource_scope='GNU time wrapper/launcher and reaped children only; separate HDB workers may be excluded, so CPU and RSS are not whole-job aggregates.',
   original_receipt=original.name,original_receipt_sha256=hashlib.sha256(original.read_bytes()).hexdigest())
 (p/'invocation.json').write_text(json.dumps(receipt,indent=2)+'\n')
 records[name]=receipt
missing=records[names[0]]['missing_rulechecks']
assert missing==['ACTIVE.AUX.3','ACTIVE.S.1','ACTIVE.W.1','ACTIVE.W.2','M4.AUX.4','M5.AUX.4']
result={'interpretation':'Hyperscaling completes independent rules earlier, but the ACTIVE.W.1/W.2 shared batch remains unfinished. No full-AES signoff speedup is established.',
 'runs':records,
 'active_width_shared_batch':{'dispatch_operation_id':'I99','gcd_start_log_T_s':3,
  'gcd_complete_log_elapsed_s':4,'gcd_batch_reported_real_s':1,
  'aes_start_log_T_s':6,'aes_last_logged_elapsed_s':49,
  'aes_minimum_observed_unfinished_span_s':43,
  'aes_probe_wall_budget_s':60,'individual_rule_duration_available':False,
  'reason':'ACTIVE.W.1 and ACTIVE.W.2 share the encrypted 27-operation batch; no separate per-rule timer is exposed.'},
 'no_additional_600s_run_started':True,
 'no_calibre_or_hdb_worker_processes_remaining':True,
 'installed_calibre_modules':['2025.1_16.10']}
(out/'rule_level_audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'completed_aes':len(all_rules)-len(missing),'missing':missing,
 'missing_sha256':records[names[0]]['missing_rulechecks_sha256'],
 'gcd':records[names[1]]['result'],
 'aes_observed_violations':records[names[0]]['observed_violations_in_completed_rules']},indent=2))
