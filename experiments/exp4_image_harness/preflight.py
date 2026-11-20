#!/usr/bin/env python3
"""Exercise the image converter and independent scorer on development data only."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exp4_harness"))
from runner_native import freeze_converter
from run_batch import produce, evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cpu-ids", required=True)
    parser.add_argument("--all-dev", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "smoke" / args.name
    converter = output / "work/converter"
    converter.mkdir(parents=True)
    (converter / "run.sh").write_text('#!/bin/bash\nexec bash /r2g/convert_image.sh "$@"\n')
    frozen = freeze_converter(converter, output)
    (output / "result.json").write_text(json.dumps({"preflight_only": True, **frozen}))
    config = {"harness": "r2g", "task_kind": "image", "out": str(output),
              "r2g_root": str(root / "runtime"), "image": "localhost/exp4c-conv:1",
              "image_root": str(root / "image/rootfs"), "cpuset": args.cpu_ids}
    rows = json.loads((root / "cohort.json").read_text())["splits"]["dev"]
    if not args.all_dev:
        rows = [min((r for r in rows if r["platform"] == p), key=lambda r: r["cells"])
                for p in ("nangate45", "sky130hd", "gf180")]
    summaries = []
    for row in rows:
        destination = output / "preflight" / row["design"]
        produce(config, root, "dev", row, destination)
        evaluate(config, root, "dev", row, destination)
        report = json.loads((destination / "score/score.json").read_text())
        summary = {k: report[k] for k in ("design", "platform", "strict_full_pass", "raster_accuracy", "failures")}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
        (output / "summary.json").write_text(json.dumps(summaries, indent=2))
    if not all(r["strict_full_pass"] for r in summaries):
        raise SystemExit("Image infrastructure preflight failed; inspect the development reports")


if __name__ == "__main__":
    main()
