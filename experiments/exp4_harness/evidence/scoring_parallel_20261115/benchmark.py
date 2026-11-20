from pathlib import Path
import sys,time,json,pickle,resource
mode,size=sys.argv[1:3]
base=Path("/score")
sys.path.insert(0,"/private/exp4_harness")
from scoring import score_design
case=base/size
meta=json.loads((case/"metadata.json").read_text())
if mode == "candidate" and size == "large":
    import fcntl
    candidate_lock=open(case/"candidate.lock","a+")
    fcntl.flock(candidate_lock,fcntl.LOCK_EX)
    if (case/"candidate_timing.json").exists():
        print((case/"candidate_timing.json").read_text(),flush=True)
        raise SystemExit(0)

if mode == "baseline" and size == "large":
    # Reuse the already running formal baseline evaluation, with identical run.json.
    source=case/"formal_original"
    while not (source/"score.json").exists():
        time.sleep(5)
    original=json.loads((source/"score.json").read_text())
    assert original["run_report"] == json.loads((case/"run.json").read_text())
    (case/"baseline_score.json").write_text(json.dumps(original,indent=2))
    report={"mode":mode,"size":size,"wall_seconds":(source/"score.json").stat().st_mtime-json.loads((case/"formal_start.json").read_text())["unix"],"measurement":"formal evaluator score.json mtime minus evaluate.log birth time; includes container startup and truth load","strict_full_pass":original["strict_full_pass"],"label_accuracy":original["label_accuracy"]}
    (case/"baseline_timing.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
    raise SystemExit(0)
t0=time.monotonic()
with open("/reference/"+meta["design"]+"/truth.pkl","rb") as f: truth=pickle.load(f)
load=time.monotonic()-t0
truth["design"],truth["platform"]=meta["design"],meta["design"].split("__")[-2]
run=json.loads((case/"run.json").read_text())
t0=time.monotonic(); cpu0=time.process_time()
result=score_design(truth,case/"out",run)
report={"mode":mode,"size":size,"wall_seconds":time.monotonic()-t0,"cpu_seconds":time.process_time()-cpu0,"load_seconds":load,"peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"strict_full_pass":result["strict_full_pass"],"label_accuracy":result["label_accuracy"]}
(case/(mode+"_score.json")).write_text(json.dumps(result,indent=2,allow_nan=False,default=str))
(case/(mode+"_timing.json")).write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
