#!/usr/bin/env python3
"""Exercise bounded R2/R3 transforms on triaged failures without publication."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from exercise_repair_cohort import failure_index, rows, source_roots
from run_expansion_round import source_language, synthesize_design


SCHEMA = "rtl_repair_exercise_v1"


def r2_sv2v(paths: list[Path], out: Path, include_dirs: list[Path], sv2v: str) -> tuple[list[Path], dict[str, Any]]:
    if not paths or any(source_language(path) == "vhdl" for path in paths):
        return [], {"status": "UNSUPPORTED_LANGUAGE"}
    out.mkdir(parents=True, exist_ok=True)
    canonical = out / "portable.v"
    command = [sv2v, *(value for directory in include_dirs for value in ("-I", str(directory))), *(str(path) for path in paths)]
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60, check=False)
    if result.returncode == 0 and result.stdout.strip():
        canonical.write_text(result.stdout, encoding="utf-8")
        return [canonical], {"status": "CONVERTED", "command": command, "stderr_tail": result.stderr[-4000:]}
    return [], {"status": "CONVERSION_FAIL", "command": command, "stderr_tail": result.stderr[-4000:]}


def r3_isolate(paths: list[Path], out: Path) -> tuple[list[Path], dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []
    changed_files: list[dict[str, Any]] = []
    for index, path in enumerate(paths):
        if source_language(path) == "vhdl":
            return [], {"status": "UNSUPPORTED_LANGUAGE"}
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = re.sub(r"(?is)//\s*synthesis\s+translate_off.*?//\s*synthesis\s+translate_on", "\n", text)
        updated = re.sub(r"(?im)^\s*(?:assert|assume|cover)\s*(?:property\s*)?\([^;]*;\s*$", "", updated)
        updated = re.sub(r"(?im)^\s*\$(?:display|write|monitor|finish|stop|fatal|error|warning)\s*\([^;]*;\s*$", "", updated)
        target = out / f"{index:04d}_{path.name}"
        target.write_text(updated, encoding="utf-8")
        converted.append(target)
        if updated != text:
            changed_files.append({"source": str(path), "target": str(target), "text_delta": abs(len(updated) - len(text))})
    return converted, {"status": "TRANSFORMED" if changed_files else "NO_APPLICABLE_RULE", "rules": ["translate_off_isolation", "verification_statement_isolation"], "changed_files": changed_files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--per-level", type=int, default=10)
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    parser.add_argument("--sv2v", default="/opt/OpenROAD/2_convert_verilog_tool/sv2v-Linux/sv2v")
    args = parser.parse_args()
    audits = rows(args.corpus_root / "quality/phase1_5/failure_audit_cohort.jsonl")
    repositories = rows(args.corpus_root / "manifests/repositories.jsonl")
    roots = source_roots(args.corpus_root, repositories)
    failures = failure_index(args.corpus_root)
    results: list[dict[str, Any]] = []
    for level in ("R2", "R3"):
        selected = [row for row in audits if row.get("suggested_repair_level") == level][:args.per_level]
        for index, audit in enumerate(selected, 1):
            failure = failures.get((str(audit.get("repo_id")), str(audit.get("project_key")), str(audit.get("top_candidate"))))
            root = roots.get(str(audit.get("repo_id")))
            if not failure or not root:
                results.append({"schema": SCHEMA, "sample_key": audit["sample_key"], "repair_level": level, "status": "SOURCE_UNAVAILABLE"})
                continue
            paths = [root / unit["path"] for unit in failure.get("source_units", []) if (root / unit["path"]).is_file()]
            include_dirs = sorted({root, *(path.parent for path in paths)})
            output = args.corpus_root / "repairs/phase1_5" / audit["sample_key"] / level
            print(f"[{level} {index}/{len(selected)}] {audit['top_candidate']}", flush=True)
            try:
                transformed, transform = r2_sv2v(paths, output, include_dirs, args.sv2v) if level == "R2" else r3_isolate(paths, output)
            except (OSError, subprocess.TimeoutExpired) as exc:
                transformed, transform = [], {"status": "TOOL_FAIL", "detail": str(exc)}
            if not transformed or transform["status"] in {"NO_APPLICABLE_RULE", "CONVERSION_FAIL", "UNSUPPORTED_LANGUAGE", "TOOL_FAIL"}:
                results.append({
                    "schema": SCHEMA, "sample_key": audit["sample_key"], "repo_id": audit["repo_id"],
                    "top_candidate": audit["top_candidate"], "repair_level": level,
                    "status": "EVIDENCE_INSUFFICIENT", "transformation": transform,
                })
                continue
            synthesis = synthesize_design(audit["top_candidate"], "verilog", [], transformed, [output], output / "synthesis", args.yosys, 90)
            results.append({
                "schema": SCHEMA, "sample_key": audit["sample_key"], "repo_id": audit["repo_id"],
                "top_candidate": audit["top_candidate"], "repair_level": level,
                "status": "RECOVERED_CANDIDATE" if synthesis.get("generic_pass") else "RETRY_FAILED",
                "transformation": transform, "synthesis": synthesis,
                "equivalence": {"schema": "rtl_equiv_v1", "result": "UNAVAILABLE"},
                "publication_status": "NOT_PUBLISHED_REQUIRES_EQUIVALENCE_AND_FULL_VALIDATION",
            })
    target = args.corpus_root / "quality/phase1_5/repair_r2_r3_exercise.jsonl"
    target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    summary = {
        "schema": SCHEMA, "completed": len(results),
        "status": dict(Counter(row["status"] for row in results)),
        "by_level": {level: dict(Counter(row["status"] for row in results if row["repair_level"] == level)) for level in ("R2", "R3")},
        "recovered_candidates": sum(row["status"] == "RECOVERED_CANDIDATE" for row in results), "published": 0,
    }
    (args.corpus_root / "quality/phase1_5/repair_r2_r3_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
