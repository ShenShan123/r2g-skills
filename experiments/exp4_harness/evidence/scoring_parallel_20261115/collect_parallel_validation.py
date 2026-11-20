from pathlib import Path
import json,time
v=Path("/tmp/exp4_scoring_speed_validation_20261115");c=Path("/tmp/exp4_scoring_parallel_candidate_20261115")
while True:
 rows=[];complete=True
 for size in ("small","medium","failed","large"):
  p=v/size;mode="policy" if size=="small" else "parallel";row={"size":size,"mode":mode,"metadata":json.loads((p/"metadata.json").read_text())}
  for field,prefix in (("baseline","baseline"),("candidate",mode)):
   f=p/(prefix+"_timing.json")
   if f.exists():row[field]=json.loads(f.read_text())
   else:complete=False
  if mode=="parallel":
   f=p/"parallel_resources.json"
   if f.exists():row["resources"]=json.loads(f.read_text())
   else:complete=False
  if (p/"baseline_score.json").exists() and (p/(mode+"_score.json")).exists():
   a=json.loads((p/"baseline_score.json").read_text());b=json.loads((p/(mode+"_score.json")).read_text())
   row["entire_json_equal"]=a==b;row["different_top_keys"]=[k for k in a if a[k]!=b.get(k)]
   if "baseline" in row and "candidate" in row:row["speedup"]=row["baseline"]["wall_seconds"]/row["candidate"]["wall_seconds"]
  else:complete=False
  rows.append(row)
 controls=json.loads((v/"semantic_parallel_controls_summary.json").read_text())
 tests=json.loads((v/"test_parallel_result.json").read_text())
 smoke=json.loads((v/"evaluate_entry_smoke.json").read_text())
 audit=json.loads((c/"source_audit.json").read_text())
 passed=complete and all(r.get("entire_json_equal") for r in rows) and all(r["entire_json_equal"] for r in controls) and tests["returncode"]==0 and smoke["entire_json_equal"]
 summary={"complete":complete,"validated_for_deployment":passed,"policy":"Ten graph PT files <64 MiB: 1 worker; otherwise 4 worker processes, within the same original arm CPU affinity. Each worker numerical thread count=1.","benchmark_rows":rows,"semantic_controls":controls,"parallel_unit_tests_returncode":tests["returncode"],"tests":12,"entry_smoke":smoke,"source_audit":audit,"large_baseline_note":"Old score mtime minus evaluate.log birth time includes container startup and truth load; candidate score time measured directly.","updated_unix":time.time()}
 tmp=v/"parallel_validation_summary.tmp.json";tmp.write_text(json.dumps(summary,indent=2));tmp.replace(v/"parallel_validation_summary.json")
 if complete:break
 time.sleep(5)
