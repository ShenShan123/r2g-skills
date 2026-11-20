from pathlib import Path
import json,time,hashlib
v=Path("/tmp/exp4_scoring_speed_validation_20261115");large=v/"large"
resource=large/"memory_recheck_resources.json"
while not resource.exists():time.sleep(2)
r=json.loads(resource.read_text());timing=json.loads((large/"memory_recheck_timing.json").read_text())
a=json.loads((large/"baseline_score.json").read_text());b=json.loads((large/"memory_recheck_score.json").read_text())
assert r["returncode"]==0 and a==b
sampler_test=json.loads((v/"process_tree_sampler_test.json").read_text());assert sampler_test["passed"]
candidate=Path("/tmp/exp4_scoring_parallel_candidate_20261115")
audit=json.loads((candidate/"source_audit.json").read_text())
assert all(hashlib.sha256((candidate/name).read_bytes()).hexdigest()==digest for name,digest in audit["changed_hashes"].items())
correction={"status":"corrected_large_remeasured","cause":"Initial sampler only traversed task/PID/children and missed Pool replacement workers created by worker_handler threads.","fix":"Recursively discover children from all /proc/PID/task/*/children, deduplicating PIDs.","large_recheck":r,"large_recheck_timing":timing,"large_recheck_entire_json_equal":True,"source_hashes_unchanged":True,"sampler_regression_test":sampler_test,"other_sample_memory":"Small/medium/failed original memory figures remain invalid lower bounds and were not repeated. Their timing and JSON equivalence stay valid.","original_speed_measurements_unchanged":True}
for p in [v/"parallel_validation_summary.json",Path("/home/yangao/exp4_harness_20261115_203/infrastructure/scoring_parallel_v2/validation_summary.json")]:
 s=json.loads(p.read_text())
 for row in s["benchmark_rows"]:
  if row["size"]=="large":
   row["prior_incomplete_resources"]=row["resources"]
   row["resources"]={**r,"measurement_run":"separate full-process-tree memory recheck; original speed timing unchanged"}
 s["memory_measurement_correction"]=correction
 tmp=p.with_suffix(".corrected.tmp.json");tmp.write_text(json.dumps(s,indent=2));tmp.replace(p)
(v/"memory_measurement_correction.json").write_text(json.dumps(correction,indent=2))
print(json.dumps(correction),flush=True)
