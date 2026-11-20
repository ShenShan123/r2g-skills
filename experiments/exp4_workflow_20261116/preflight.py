#!/usr/bin/env python3
"""Validate the installed workflow on development designs, without model calls."""
import argparse
import json
from pathlib import Path
from runner_native import freeze_converter
from run_batch import produce, evaluate

p = argparse.ArgumentParser()
p.add_argument("--root", type=Path, required=True)
p.add_argument("--kind", choices=("graph", "image"), required=True)
p.add_argument("--name", default="workflow")
p.add_argument("--all", action="store_true")
p.add_argument("--platform", choices=("nangate45", "sky130hd", "gf180"))
p.add_argument("--cpuset", default="0,1,2,3,4,5,6,7")
a = p.parse_args()
root = a.root.resolve()
out = root / "smoke" / a.name
converter = out / "work/converter"
if not (out / "result.json").exists():
    converter.mkdir(parents=True, exist_ok=True)
    (converter / "run.sh").write_text(f'#!/usr/bin/env bash\nexec bash /r2g/generate_{a.kind}.sh "$@"\n')
    (out / "result.json").write_text(json.dumps({"preflight_only": True, **freeze_converter(converter, out)}))
config = {"harness": "r2g", "task_kind": a.kind, "out": str(out),
          "r2g_root": str(root / "runtime"), "image": "localhost/exp4c-conv:1",
          "image_root": str(root / "image/rootfs"), "cpuset": a.cpuset}
rows = json.loads((root / "cohort.json").read_text())["splits"]["dev"]
if not a.all:
    rows = [min((r for r in rows if r["platform"] == platform), key=lambda r: r["cells"])
            for platform in ("nangate45", "sky130hd", "gf180")]
if a.platform:
    rows = [r for r in rows if r["platform"] == a.platform]
results = []
for row in rows:
    destination = out / "preflight" / row["design"]
    produce(config, root, "dev", row, destination)
    evaluate(config, root, "dev", row, destination)
    score = json.loads((destination / "score/score.json").read_text())
    run = json.loads((destination / "run.json").read_text())
    validation_path = destination / "out/validation.json"
    validation = json.loads(validation_path.read_text()) if validation_path.exists() else {}
    result = {"design": row["design"], "platform": row["platform"],
              "strict_full_pass": score["strict_full_pass"], "failures": score.get("failures"),
              "workflow_status": validation.get("status"), "run": run}
    results.append(result)
    (out / "summary.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(result), flush=True)
raise SystemExit(0 if all(r["strict_full_pass"] and r["workflow_status"] == "passed" for r in results) else 1)
