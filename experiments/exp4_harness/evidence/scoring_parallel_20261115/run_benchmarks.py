from pathlib import Path
import subprocess,concurrent.futures,json
root=Path("/home/yangao/exp4_harness_20261115_203")
val=Path("/tmp/exp4_scoring_speed_validation_20261115")
def command_base(mode):
 selected=Path("/tmp/exp4_scoring_speed_candidate_20261115") if mode=="candidate" else root/"snapshot/experiments"
 return ["bwrap","--unshare-all","--die-with-parent","--new-session","--ro-bind",str(root/"image/rootfs"),"/","--proc","/proc","--dev","/dev","--ro-bind",str(selected/"exp4_harness"),"/private/exp4_harness","--ro-bind",str(selected/"exp4_validation"),"/private/exp4_validation","--bind",str(val),"/score","--ro-bind",str(root/"reference"),"/reference","--tmpfs","/tmp","--setenv","PYTHONDONTWRITEBYTECODE","1","--setenv","OMP_NUM_THREADS","2","--setenv","OPENBLAS_NUM_THREADS","1","--setenv","MKL_NUM_THREADS","1"]
def run(mode,cpu):
 for size in ("small","medium","failed","large"):
  cmd=["taskset","-c",str(cpu),*command_base(mode),"--","/opt/py/bin/python3","/score/benchmark.py",mode,size]
  print(json.dumps({"event":"start","mode":mode,"size":size}),flush=True)
  with (val/size/(mode+".log")).open("w") as f: ret=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
  print(json.dumps({"event":"done","mode":mode,"size":size,"code":ret.returncode}),flush=True)
  if ret.returncode: break
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 list(pool.map(lambda args:run(*args),[("baseline",0),("candidate",1)]))
