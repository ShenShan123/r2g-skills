from pathlib import Path
import subprocess,json,time
v=Path("/tmp/exp4_scoring_speed_validation_20261115")
ns={};s=(v/"run_benchmarks.py").read_text();exec(s[:s.index("def run(mode,cpu):")],ns)
base=[s.replace("/tmp/exp4_scoring_speed_candidate_20261115","/tmp/exp4_scoring_parallel_candidate_20261115") for s in ns["command_base"]("candidate")]
def sample(pid):
 todo=[pid];seen=set();rss=pss=0
 while todo:
  p=todo.pop()
  if p in seen:continue
  seen.add(p)
  try:
   for children in Path(f"/proc/{p}/task").glob("*/children"):
    try:todo.extend(map(int,children.read_text().split()))
    except (FileNotFoundError,PermissionError,ProcessLookupError):pass
   for line in Path(f"/proc/{p}/smaps_rollup").read_text().splitlines():
    if line.startswith("Rss:"):rss+=int(line.split()[1])*1024
    elif line.startswith("Pss:"):pss+=int(line.split()[1])*1024
  except (FileNotFoundError,PermissionError,ProcessLookupError):pass
 return rss,pss,len(seen)
for size in ("small","medium","failed","large"):
 print(json.dumps({"event":"start","size":size}),flush=True)
 cmd=["taskset","-c","4,5,9,11",*base,"--","/opt/py/bin/python3","/score/benchmark_parallel.py",size]
 peakrss=peakpss=peakprocs=samples=0;started=time.monotonic()
 with (v/size/"parallel.log").open("w") as f:
  p=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT)
  while p.poll() is None:
   rss,pss,procs=sample(p.pid);peakrss=max(peakrss,rss);peakpss=max(peakpss,pss);peakprocs=max(peakprocs,procs);samples+=1
   time.sleep(1)
 report={"returncode":p.returncode,"peak_process_tree_rss_bytes":peakrss,"peak_process_tree_pss_bytes":peakpss,"peak_processes":peakprocs,"samples":samples,"sample_interval_seconds":1,"elapsed_container_seconds":time.monotonic()-started,"cpuset":"4,5,9,11"}
 (v/size/"parallel_resources.json").write_text(json.dumps(report,indent=2))
 print(json.dumps({"event":"done","size":size,**report}),flush=True)
 if p.returncode:break
