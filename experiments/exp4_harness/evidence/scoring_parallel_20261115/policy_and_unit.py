from pathlib import Path
import subprocess,json
v=Path("/tmp/exp4_scoring_speed_validation_20261115")
ns={};s=(v/"run_benchmarks.py").read_text();exec(s[:s.index("def run(mode,cpu):")],ns)
base=[x.replace("/tmp/exp4_scoring_speed_candidate_20261115","/tmp/exp4_scoring_parallel_candidate_20261115") for x in ns["command_base"]("candidate")]
for name,args in (("small_policy",["/score/benchmark_parallel.py","small","1","policy"]),("test_parallel",["/score/test_parallel.py"])):
 with (v/(name+".log")).open("w") as f:r=subprocess.run(["taskset","-c","33",*base,"--","/opt/py/bin/python3",*args],stdout=f,stderr=subprocess.STDOUT)
 (v/(name+"_result.json")).write_text(json.dumps({"returncode":r.returncode},indent=2))
