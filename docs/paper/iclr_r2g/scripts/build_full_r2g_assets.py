#!/usr/bin/env python3
"""Verify Full R2G evidence and emit the completed supplementary result table."""
from pathlib import Path
import hashlib,json,math
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evidence/e3_full_r2g'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
man=json.loads((E/'material_manifest.json').read_text())
for item in man['files']+man['copied_evidence']:
 assert digest(E/item['file'])==item['sha256'],item['file']
for name,sha in man['preserved_original_result_sha256'].items():assert digest(Path(name))==sha,name
S=json.loads((E/'results/summary.json').read_text())
C=json.loads((E/'results/complete.json').read_text())
protocol=json.loads((E/'design_and_execution/manifest.json').read_text())
rows=C['results'];by_id={r['task_id']:r for r in rows}
assert len(rows)==len(by_id)==13
assert set(by_id)=={r['task_id'] for r in protocol['tasks']}=={r['task_id'] for r in S['tasks']}
assert S['evaluation_role']=='post_development_supplement_not_heldout'
assert protocol['llm_calls']==S['llm_calls_during_evaluation']==0 and not protocol['online_learning']
assert not protocol['original_scores_modified'] and not C['original_scores_modified']
old=json.loads(Path('/home/yangao/r2g_paper_experiment_materials_20260909/experiment3/results/experiment3_final_results.json').read_text())
# Compare the actual per-task records with both machine summaries and trial evidence.
attempts=[];strategy_counts={}
for r in rows:
 task=r['task_id'];q=json.loads((E/f'evidence/{task}/result.json').read_text())
 assert q==r,task
 assert r['attempt_count']==len(r['attempts'])
 expected=next(t for t in S['tasks'] if t['task_id']==task)
 assert expected['strict_clean']==r['strict_clean'] and expected['repair_orfs_calls']==len(r['attempts'])
 wins=0
 for i,a in enumerate(r['attempts'],1):
  direct=json.loads((E/f'evidence/{task}/attempt_{i:02d}/trial_evidence.json').read_text())
  assert direct==a,(task,i)
  assert a['infrastructure_complete'] and a['provenance_complete'] and a['protected_task_digest_match']
  m=a['action_metrics']
  clean=all(m[k]==0 for k in ['drc_violations','lvs_mismatches','route_violations','antenna_violations']) and m['setup_wns_ns']>=0 and m['hold_wns_ns']>=0
  assert clean==a['strict_clean_after_repair']
  wins+=clean;attempts.append(a)
  strategy_counts[a['candidate_id']]=strategy_counts.get(a['candidate_id'],0)+1
 assert r['strict_clean']==bool(wins)
 if wins:assert len(r['attempts'])==1
counts={'completed':len(rows),'strict_clean':sum(r['strict_clean'] for r in rows),'drc_clean':sum(r['strict_clean'] and r['check']=='drc' for r in rows),'drc_total':sum(r['check']=='drc' for r in rows),'setup_clean':sum(r['strict_clean'] and r['check']=='timing' for r in rows),'setup_total':sum(r['check']=='timing' for r in rows),'repair_orfs_calls':len(attempts),'tasks_with_attempts':sum(bool(r['attempts']) for r in rows)}
for k,v in counts.items():assert v==S[k],k
elapsed=sum(a['elapsed_seconds'] for a in attempts)
assert math.isclose(elapsed,S['sum_recorded_attempt_elapsed_seconds'],abs_tol=1e-8)
iir=by_id['exp1_61c173a60608_iir_biquad_axis']['attempts'][0]
derived={'evaluation_role':S['evaluation_role'],'source_exposure':S['source_exposure'],'counts':counts,'success_rate':counts['strict_clean']/len(rows),'no_action_tasks':sum(not r['attempts'] for r in rows),'failed_attempts':sum(not a['strict_clean_after_repair'] for a in attempts),'all_successes_first_attempt':True,'strategy_attempts':strategy_counts,'sum_recorded_attempt_elapsed_seconds':elapsed,'llm_calls_during_evaluation':0,'online_learning':False,'new_clean_sentinel_evaluation':False,'iir_baseline_metrics':iir['baseline_metrics'],'iir_action_metrics':iir['action_metrics'],'iir_setup_wns_gain_ns':iir['wns_delta_ns'],'sources':{str(f.relative_to(ROOT)):digest(f) for f in sorted(E.rglob('*')) if f.is_file()}}
(ROOT/'evidence/e3_full_r2g_derived.json').write_text(json.dumps(derived,indent=2)+'\n')
lines=[r'\begin{tabular}{llrl}\toprule',r'Design & Type & Trials & Outcome\\\midrule']
for r in sorted(rows,key=lambda r:(r['check']!='drc',r['task_id'])):
 name=r['task_id'].split('_',2)[2].replace('_',r'\_')
 outcome='Strict clean' if r['strict_clean'] else ('No eligible action' if not r['attempts'] else 'Setup violations remain')
 lines.append(r'\texttt{'+name+'} & '+('DRC' if r['check']=='drc' else 'Setup')+' & '+str(r['attempt_count'])+' & '+outcome+r'\\')
lines.extend([r'\midrule',r'\textbf{Total} & 13 designs & \textbf{11} & \textbf{8 strict clean}\\',r'\bottomrule\end{tabular}'])
(ROOT/'tables/e3_full_r2g_cases.tex').write_text('\n'.join(lines)+'\n')
print(json.dumps({'verified_manifest_files':len(man['files']),'case_records':len(rows),'trial_records':len(attempts),'recomputed':counts,'recorded_attempt_seconds':elapsed},indent=2))
