#!/usr/bin/env python3
"""Re-exercise historical VHDL-top mixed-language failures with the live frontend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

from exercise_repair_cohort import rows, source_roots
from run_expansion_round import synthesize_design


SCHEMA = "rtl_mixed_frontend_exercise_v1"
ENTITY = re.compile(r"(?im)^\s*entity\s+([A-Za-z][\w]*)\s+is\b")


def key(row: dict) -> str:
    material = "\0".join(str(row.get(name, "")) for name in ("repo_id", "project_key", "top_candidate"))
    return hashlib.sha256(material.encode()).hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--yosys", default="/opt/OpenROAD/oss-cad-suite/bin/yosys")
    args = parser.parse_args()
    corpus = args.corpus_root
    repository_rows = rows(corpus / "manifests/repositories.jsonl")
    roots = source_roots(corpus, repository_rows)
    candidates: dict[str, dict] = {}
    for path in sorted((corpus / "failures/top_candidates").glob("*.jsonl")):
        for row in rows(path):
            languages = {unit.get("language") for unit in row.get("source_units", [])}
            if row.get("failure_type") == "MIXED_LANGUAGE_VHDL_TOP_UNSUPPORTED" and "vhdl" in languages and len(languages) > 1 and row.get("repo_id") in roots:
                candidates[key(row)] = row
    selected = [candidates[value] for value in sorted(candidates)[: max(0, args.sample_size)]]
    results: list[dict] = []
    for row in selected:
        sample_key = key(row)
        root = roots[row["repo_id"]]
        paths = [root / unit["path"] for unit in row.get("source_units", [])]
        paths = [path for path in paths if path.is_file()]
        entities: set[str] = set()
        for path in paths:
            if path.suffix.lower() in {".vhd", ".vhdl"}:
                entities.update(ENTITY.findall(path.read_text(encoding="utf-8", errors="replace")))
        synthesis = synthesize_design(
            str(row["top_candidate"]), "vhdl", sorted(entities - {str(row["top_candidate"])}),
            paths, sorted({root, *(path.parent for path in paths)}),
            corpus / "quality/phase2/mixed_frontend_exercise" / sample_key,
            args.yosys, args.timeout,
        )
        results.append({
            "schema": SCHEMA, "sample_key": sample_key, "repo_id": row["repo_id"],
            "project_key": row.get("project_key"), "top_candidate": row["top_candidate"],
            "source_languages": sorted({unit.get("language") for unit in row.get("source_units", [])}),
            "source_unit_count": len(paths), "outcome": synthesis.get("reason"),
            "generic_pass": bool(synthesis.get("generic_pass")), "runtime_seconds": synthesis.get("runtime_seconds"),
            "log_path": synthesis.get("log_path"), "diagnostic_tail": synthesis.get("log_tail", "")[-2000:],
        })
    out = corpus / "quality/phase2"
    atomic(out / "mixed_frontend_exercise.json", {"schema": SCHEMA, "population": len(candidates), "sampled": len(results), "generic_pass": sum(row["generic_pass"] for row in results), "outcomes": dict(Counter(row["outcome"] for row in results)), "results": results})
    print(json.dumps({"schema": SCHEMA, "population": len(candidates), "sampled": len(results), "generic_pass": sum(row["generic_pass"] for row in results), "outcomes": dict(Counter(row["outcome"] for row in results))}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
