from pathlib import Path
import sys,time,json,pickle,resource
sys.path.insert(0,"/private/exp4_harness")
from scoring import score_design
size=sys.argv[1];case=Path("/score")/size
workers=int(sys.argv[2]) if len(sys.argv)>2 else 4
prefix=sys.argv[3] if len(sys.argv)>3 else "parallel"
meta=json.loads((case/"metadata.json").read_text())
start=time.monotonic()
truth=pickle.load(open("/reference/"+meta["design"]+"/truth.pkl","rb"))
load=time.monotonic()-start
truth["design"],truth["platform"]=meta["design"],meta["design"].split("__")[-2]
run=json.loads((case/"run.json").read_text())
start=time.monotonic();cpu=time.process_time();before=resource.getrusage(resource.RUSAGE_CHILDREN)
result=score_design(truth,case/"out",run,workers=workers)
after=resource.getrusage(resource.RUSAGE_CHILDREN)
report={"mode":"parallel","size":size,"workers":workers,"wall_seconds":time.monotonic()-start,"cpu_seconds":time.process_time()-cpu+(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime),"load_seconds":load,"strict_full_pass":result["strict_full_pass"],"label_accuracy":result["label_accuracy"]}
(case/(prefix+"_score.json")).write_text(json.dumps(result,indent=2,allow_nan=False,default=str))
(case/(prefix+"_timing.json")).write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
