import pathlib,json,sys,re,collections,hashlib,datetime,copy,shutil,subprocess
wt=pathlib.Path('/data/yangao/exp2_rerun/r2g-exp2-submitfix-20261115');sys.path.insert(0,str(wt))
from tools.run_experiment1_r2g_method import select_diverse
from tools.build_experiment1_r2g_submission import candidate_from_expander_record
from tools.run_experiment1_rtl_acquisition import validate_submission,source_cache_key,verify_bound_campaign
campaign=pathlib.Path('/data/yangao/exp2_rerun/runs/exp2-v5fix2-20261115/campaign');method='r2g-expander-deepseek';r=campaign/'method_runs'/(method+'.batch1.formal');manifest=json.loads((campaign/'execution_manifest.json').read_text());verify_bound_campaign(manifest);batch=next(b for b in manifest['batches'] if b['method_id']==method);assert batch['status']=='pending' and not batch.get('submission_path')
old=json.loads((r/'submission.json').read_text());selected={c['candidate_id']:c for c in old['candidates']};bridge={}
for p in sorted((r/'replenishment').glob('*/expander_bridge.json')):
 for record in json.loads(p.read_text()).get('candidates',[]):bridge[record['design']]=record
logs=[json.loads(l) for l in (r/'precheck_log.jsonl').read_text().splitlines()];pool=[]
for row in logs:
 if not row.get('publishable_qualified'):continue
 candidate=selected.get(row['candidate_id']) or candidate_from_expander_record(bridge[row['candidate_id']],method)
 pool.append({'candidate':candidate,'family_id':row['family_id'],'mapped_cells':row['mapped_cells']})
invalid=[c for c in old['candidates'] if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*',c['top_module'])];assert len(invalid)==2
locked=[(c['repo_url'].rstrip('/'),c['commit'].lower(),c['top_module']) for c in old['candidates'] if c not in invalid]
new,payload=select_diverse(pool,target=200,prior_keys=set(),prior_families=set(),locked_keys=locked,quota={'small':70,'medium':90,'large':40})
assert len(new)==200 and payload['size_counts']=={'small':70,'medium':90,'large':40}
assert set(selected)-{c['candidate_id'] for c in new}=={c['candidate_id'] for c in invalid}
added=[c for c in new if c['candidate_id'] not in selected]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for candidate in added:
 records=list((r/'precheck').glob('*/'+candidate['candidate_id']+'/result.json'));assert len(records)==1
 evidence=json.loads(records[0].read_text());assert evidence['publishable_qualified']
 source=r/'precheck/_source_cache'/source_cache_key(candidate);assert source.is_dir()
 for item in evidence['source_evidence']['files']:assert sha(source/item['path'])==item['sha256']
 assert (source/candidate['license_evidence']['repository_path']).is_file()
print(json.dumps({'selected':len(new),'bins':payload['size_counts'],'removed':[(c['candidate_id'],c['top_module']) for c in invalid],'added':[(c['candidate_id'],c['top_module']) for c in added],'maximum_per_repo':max(payload['repository_counts'].values())},indent=2),flush=True)
if '--apply' not in sys.argv:sys.exit(0)
repair=r/'submission_format_repair_20261115';repair.mkdir()
for name in ['submission.json','prequalified_candidates.json','selection_manifest.json','method_end.json','resume_state.json']:
 shutil.copy2(r/name,repair/(name+'.before'))
# Preserve immutable checkpoint files; this explicit pre-lock format repair only replaces incompatible entries.
end=datetime.datetime.now(datetime.timezone.utc);start=datetime.datetime.fromisoformat(old['started_at']);updated=copy.deepcopy(old);updated['candidates']=new;updated['ended_at']=end.isoformat();updated['resource_usage']['method_runtime_seconds']=round((end-start).total_seconds(),3);updated['resource_usage']['format_repairs']=old['resource_usage'].get('format_repairs',0)+1;updated['resource_usage']['human_interventions']=old['resource_usage'].get('human_interventions',0)+1
staged=repair/'submission.repaired.json';staged.write_text(json.dumps(updated,indent=2)+'\n');_,errors=validate_submission(staged,campaign_root=campaign);assert not errors,errors
record={'at':end.isoformat(),'reason':'Exclude two schema-incompatible top names before submission locking; retain 198 valid checkpoint candidates and select replacements with unchanged diversity/size rules from existing prechecked pool','user_authorization':'2026-11-15 solve DeepSeek issue then score','repair_commit':subprocess.check_output(['git','-C',str(wt),'rev-parse','HEAD'],text=True).strip(),'script_sha256':sha(pathlib.Path(__file__)),'original_submission_sha256':sha(repair/'submission.json.before'),'repaired_submission_sha256':sha(staged),'removed':invalid,'added':added,'selection':payload,'original_acquisition_runtime_seconds':old['resource_usage']['method_runtime_seconds'],'original_acquisition_ended_at':old['ended_at'],'wall_runtime_including_repair_seconds':updated['resource_usage']['method_runtime_seconds'],'no_new_searches_synthesis_or_model_calls':True,'checkpoint_files_retained':True}
(repair/'repair_record.json').write_text(json.dumps(record,indent=2)+'\n')
(r/'prequalified_candidates.json').write_text(json.dumps(new,indent=2)+'\n')
selection=json.loads((r/'selection_manifest.json').read_text());selection.update(payload);selection['final_selection']=payload;selection['submission_format_repair']=str(repair/'repair_record.json');(r/'selection_manifest.json').write_text(json.dumps(selection,indent=2)+'\n')
method_end=json.loads((r/'method_end.json').read_text());method_end.update(ended_at=updated['ended_at'],method_runtime_seconds=updated['resource_usage']['method_runtime_seconds'],submission_format_repair=str(repair/'repair_record.json'));(r/'method_end.json').write_text(json.dumps(method_end,indent=2)+'\n')
resume=json.loads((r/'resume_state.json').read_text());resume['elapsed_seconds']=updated['resource_usage']['method_runtime_seconds'];resume['submission_format_repair']=str(repair/'repair_record.json');(r/'resume_state.json').write_text(json.dumps(resume,indent=2)+'\n')
shutil.copyfile(staged,r/'submission.json')
amendments=campaign/'implementation_amendments.json';entries=json.loads(amendments.read_text()) if amendments.exists() else [];entries.append({'at':end.isoformat(),'role':'pre-lock submission format repair','method_id':method,'repair_record':str(repair/'repair_record.json'),'repair_commit':record['repair_commit'],'protocol_effect':'No schema, scoring gate, budget or evaluator change; no independent evaluator feedback used. Source candidates selected from existing qualified pool, with repair included in wall time.'});amendments.write_text(json.dumps(entries,indent=2)+'\n')
print('APPLIED',str(repair/'repair_record.json'))
