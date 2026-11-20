#!/usr/bin/env python3
"""Exercise the graph runtime and evaluator on one small development input/PDK.

This is infrastructure validation, with no model and no test-set access. Its
artifacts are separate from every formal attempt.
"""
import argparse
import json
from pathlib import Path

from runner_native import freeze_converter
from run_batch import produce, evaluate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--image-root", type=Path)
    args = ap.parse_args()
    root = args.root
    output = root / "smoke" / args.name
    converter = output / "work/converter"
    converter.mkdir(parents=True)
    (converter / "run.sh").write_text('#!/usr/bin/env bash\nexec bash /r2g/convert_graph.sh "$@"\n')
    frozen = freeze_converter(converter, output)
    (output / "result.json").write_text(json.dumps({"preflight_only": True, **frozen}))
    config = {"harness": "r2g", "out": str(output), "r2g_root": str(root / "runtime"),
              "image": "localhost/exp4c-conv:1", "image_root": str(args.image_root) if args.image_root else None}
    rows = json.loads((root / "cohort.json").read_text())["splits"]["dev"]
    summaries = []
    for platform in ("nangate45", "sky130hd", "gf180"):
        row = min((r for r in rows if r["platform"] == platform), key=lambda r: r["cells"])
        destination = output / "preflight" / row["design"]
        produce(config, root, "dev", row, destination)
        evaluate(config, root, "dev", row, destination)
        report = json.loads((destination / "score/score.json").read_text())
        summary = {k: report[k] for k in ("design", "platform", "strict_full_pass", "label_accuracy", "failures")}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
        (output / "summary.json").write_text(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
