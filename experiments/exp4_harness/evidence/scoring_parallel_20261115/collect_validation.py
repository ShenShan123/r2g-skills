from pathlib import Path
import json,time,hashlib
v=Path("/tmp/exp4_scoring_speed_validation_20261115")
c=Path("/tmp/exp4_scoring_speed_candidate_20261115")
while True:
 rows=[];complete=True
 for size in ("small","medium","failed","large"):
  p=v/size;row={"size":size,"metadata":json.loads((p/"metadata.json").read_text())}
  for mode in ("baseline","candidate"):
   f=p/(mode+"_timing.json")
   if f.exists():row[mode]=json.loads(f.read_text())
   else:complete=False
  if (p/"baseline_score.json").exists() and (p/"candidate_score.json").exists():
   a=json.loads((p/"baseline_score.json").read_text());b=json.loads((p/"candidate_score.json").read_text())
   row["entire_json_equal"]=a==b
   row["different_keys"]=[k for k in a if a[k]!=b.get(k)]
   if "baseline" in row and "candidate" in row:row["speedup"]=row["baseline"]["wall_seconds"]/row["candidate"]["wall_seconds"]
  else:complete=False
  rows.append(row)
 controls=json.loads((v/"semantic_controls_summary.json").read_text()) if (v/"semantic_controls_summary.json").exists() else []
 tests=json.loads((v/"test_final.json").read_text()) if (v/"test_final.json").exists() else {}
 audit=json.loads((c/"source_audit.json").read_text())
 approved=complete and all(r.get("entire_json_equal") for r in rows) and len(controls)==9 and all(r["entire_json_equal"] for r in controls) and tests.get("returncode")==0
 summary={"complete":complete,"validated_for_deployment":approved,"benchmark_rows":rows,"semantic_controls":controls,"test_returncode":tests.get("returncode"),"tests":tests.get("tests"),"source_audit":audit,"large_baseline_note":"Formal old evaluator elapsed approximated by final score mtime minus evaluate.log birth time, includes startup and truth load; new elapsed measured directly.","updated_unix":time.time()}
 pending=v/"validation_summary.tmp.json";pending.write_text(json.dumps(summary,indent=2));pending.replace(v/"validation_summary.json")
 if complete:break
 time.sleep(5)
