#!/usr/bin/env python3
"""Run the installed R2G converter and its production validation profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run_logged(command, log, environment):
    start = time.monotonic()
    with log.open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                env=environment)
    return {"exit_code": result.returncode, "wall_seconds": round(time.monotonic() - start, 3),
            "log": str(log)}


def generate_and_validate(kind, design, platform, output, *, runtime=None):
    runtime = Path(runtime or Path(__file__).resolve().parent)
    design, platform, output = (Path(p).resolve() for p in (design, platform, output))
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(R2G_IGNORE_ENV_LOCAL="1", R2G_DEF=str(design / "6_final.def"),
                       R2G_SPEF=str(design / "6_final.spef"),
                       ORFS_ROOT="/r2g_toolchain/OpenROAD-flow-scripts",
                       R2G_JOBS="1")
    report = {"schema": 1, "kind": kind, "design": str(design), "platform": str(platform),
              "status": "failed", "output": str(output),
              "declared_not_applicable": ["irdrop", "signoff"] if kind == "graph" else []}
    raw_report = output / ("graph_validation.json" if kind == "graph" else "_r2g/verify.json")
    raw_report.unlink(missing_ok=True)
    (output / "validation.json").unlink(missing_ok=True)
    try:
        report["generation"] = run_logged(
            ["bash", str(runtime / f"convert_{kind}.sh"), str(design), str(platform), str(output)],
            output / "generation.log", environment)
        if kind == "graph" and report["generation"]["exit_code"] == 0:
            report["validation"] = run_logged(
                [sys.executable, str(runtime / "tools/verify_graph_dataset.py"),
                 str(output / "project"), "--json", str(raw_report),
                 "--not-applicable", "irdrop,signoff"],
                output / "validation.log", environment)
        elif kind == "image":
            # convert_image.sh already invokes the production image verifier.
            report["validation"] = {"included_in_generation": True, "report": str(raw_report)}
        if raw_report.is_file():
            report["verifier_report"] = str(raw_report)
            report["verifier"] = json.loads(raw_report.read_text())
        verifier = report.get("verifier")
        if report["generation"]["exit_code"] == 0 and verifier is not None:
            if kind == "graph":
                valid = (report["validation"]["exit_code"] == 0 and
                         verifier.get("failed") == 0 and verifier.get("passed", 0) > 0)
            else:
                valid = (verifier.get("status") == "ok" and verifier.get("total", 0) > 0
                         and verifier.get("passed") == verifier.get("total"))
            report["status"] = "passed" if valid else "failed"
    except (OSError, ValueError) as error:
        report["error"] = str(error)
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def summary(report):
    verifier = report.get("verifier", {})
    checks = verifier.get("results", verifier.get("checks", []))
    return {"design": Path(report["design"]).name, "status": report["status"],
            "report": str(Path(report["output"]) / "validation.json"),
            "generation": report.get("generation"),
            "validation": report.get("validation"),
            "passed_checks": verifier.get("passed"),
            "failed_checks": verifier.get("failed", verifier.get("total", 0) - verifier.get("passed", 0)),
            "failures": [row for row in checks if not row.get("ok", False)][:20],
            **({"error": report["error"]} if report.get("error") else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("graph", "image"), required=True)
    parser.add_argument("design", type=Path)
    parser.add_argument("platform", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = generate_and_validate(args.kind, args.design, args.platform, args.output)
    print(json.dumps(summary(report)), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
