#!/usr/bin/env python3
"""Recover versioned repository/file license evidence and safely promote resolved records."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from frontier import canonical_repository_identity, default_frontier_path
from corpus_state import CorpusState


SCHEMA = "rtl_license_evidence_v1"
LICENSE_NAMES = re.compile(r"(?i)^(license|licence|copying|copyright|notice)(\..*)?$")
README_NAMES = re.compile(r"(?i)^readme(?:\..*)?$")
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([^\s*]+)", re.I)
IDS = {
    "MIT": "PERMISSIVE_CONFIRMED", "Apache-2.0": "PERMISSIVE_CONFIRMED",
    "BSD-2-Clause": "PERMISSIVE_CONFIRMED", "BSD-3-Clause": "PERMISSIVE_CONFIRMED",
    "ISC": "PERMISSIVE_CONFIRMED", "Unlicense": "PERMISSIVE_CONFIRMED",
    "GPL-2.0-only": "COPYLEFT_CONFIRMED", "GPL-2.0-or-later": "COPYLEFT_CONFIRMED",
    "GPL-3.0-only": "COPYLEFT_CONFIRMED", "GPL-3.0-or-later": "COPYLEFT_CONFIRMED",
    "LGPL-2.1-only": "COPYLEFT_CONFIRMED", "LGPL-2.1-or-later": "COPYLEFT_CONFIRMED",
    "LGPL-3.0-only": "COPYLEFT_CONFIRMED", "AGPL-3.0-only": "COPYLEFT_CONFIRMED",
    "MPL-2.0": "COPYLEFT_CONFIRMED",
}


def normalize_identifier(value: str) -> str | None:
    text = value.strip().strip("()[]").replace("LicenseRef-", "")
    aliases = {
        "mit": "MIT", "apache-2.0": "Apache-2.0", "apache 2.0": "Apache-2.0",
        "bsd-2-clause": "BSD-2-Clause", "bsd-3-clause": "BSD-3-Clause", "isc": "ISC",
        "gpl-2.0": "GPL-2.0-only", "gpl-3.0": "GPL-3.0-only", "lgpl-2.1": "LGPL-2.1-only",
        "lgpl-3.0": "LGPL-3.0-only", "agpl-3.0": "AGPL-3.0-only", "mpl-2.0": "MPL-2.0",
        "unlicense": "Unlicense",
    }
    return aliases.get(text.lower(), text if text in IDS else None)


def detect_full_text(text: str) -> set[str]:
    lower = text.lower()
    found: set[str] = set()
    if "permission is hereby granted, free of charge" in lower or ("mit license" in lower and "permission is hereby granted" in lower):
        found.add("MIT")
    if "apache license" in lower and "version 2.0" in lower:
        found.add("Apache-2.0")
    if "redistribution and use in source and binary forms" in lower:
        found.add("BSD-3-Clause")
    if "isc license" in lower or ("permission to use, copy, modify" in lower and "provided as is" in lower):
        found.add("ISC")
    if "gnu affero general public license" in lower:
        found.add("AGPL-3.0-only")
    elif "gnu lesser general public license" in lower:
        found.add("LGPL-3.0-only" if "version 3" in lower else "LGPL-2.1-only")
    elif "gnu general public license" in lower:
        found.add("GPL-3.0-only" if "version 3" in lower else "GPL-2.0-only")
    if "mozilla public license version 2.0" in lower:
        found.add("MPL-2.0")
    return found


def bounded_files(root: Path) -> list[Path]:
    values: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
            if len(rel.parts) > 4 or path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        if LICENSE_NAMES.match(path.name) or README_NAMES.match(path.name) or path.suffix.lower() in {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}:
            values.append(path)
        if len(values) >= 4000:
            break
    return values


def recover(root: Path, provider_hint: str | None = None) -> dict[str, Any]:
    files = bounded_files(root)
    license_files = [path for path in files if LICENSE_NAMES.match(path.name)]
    readmes = [path for path in files if README_NAMES.match(path.name)]
    rtl_files = [path for path in files if path.suffix.lower() in {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}]
    full_ids: set[str] = set()
    readme_ids: set[str] = set()
    spdx_by_file: dict[str, list[str]] = {}
    for path in license_files:
        text = path.read_text(encoding="utf-8", errors="replace")[:300_000]
        full_ids.update(detect_full_text(text))
        for raw in SPDX_RE.findall(text):
            if value := normalize_identifier(raw):
                full_ids.add(value)
    for path in readmes:
        text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
        match = re.search(r"(?ims)^#{1,4}\s*licen[cs]e\b(.{0,4000})", text)
        if match:
            readme_ids.update(detect_full_text(match.group(1)))
            for raw in SPDX_RE.findall(match.group(1)):
                if value := normalize_identifier(raw):
                    readme_ids.add(value)
    for path in rtl_files:
        text = path.read_text(encoding="utf-8", errors="replace")[:12_000]
        ids = sorted({value for raw in SPDX_RE.findall(text) if (value := normalize_identifier(raw))})
        if ids:
            spdx_by_file[str(path.relative_to(root))] = ids
    provider_id = normalize_identifier(provider_hint or "")
    per_file_ids = {value for values in spdx_by_file.values() for value in values}
    all_ids = full_ids | readme_ids | per_file_ids | ({provider_id} if provider_id else set())
    statuses = {IDS[value] for value in all_ids if value in IDS}
    issues: list[str] = []
    if not license_files and not readme_ids and not per_file_ids and not provider_id:
        issues.append("LICENSE_FILE_ABSENT")
    if provider_id and not license_files and not readme_ids and not per_file_ids:
        issues.append("LICENSE_METADATA_ONLY")
    if len(statuses) > 1:
        issues.append("LICENSE_CONFLICT")
    if len(per_file_ids) > 1:
        issues.append("PER_FILE_LICENSE_MIXED")
    if not all_ids or any(value not in IDS for value in all_ids):
        issues.append("LICENSE_UNRESOLVED")
    strong_ids = full_ids | per_file_ids
    strong_statuses = {IDS[value] for value in strong_ids if value in IDS}
    if len(strong_statuses) == 1 and "LICENSE_CONFLICT" not in issues and "PER_FILE_LICENSE_MIXED" not in issues:
        status = next(iter(strong_statuses))
        confidence = "HIGH"
    elif not strong_ids and provider_id and provider_id in IDS and len(statuses) == 1:
        status = IDS[provider_id]
        confidence = "MEDIUM"
    else:
        status = "UNKNOWN"
        confidence = "LOW"
    release_policy = {
        "PERMISSIVE_CONFIRMED": "PUBLIC_EXPORT_ALLOWED",
        "COPYLEFT_CONFIRMED": "PUBLIC_EXPORT_ALLOWED",
        "RESEARCH_ONLY": "INTERNAL_TRAINING_ONLY",
        "UNKNOWN": "QUARANTINE",
    }.get(status, "QUARANTINE")
    # Metadata-only identification is useful evidence but not sufficient for public release.
    if issues == ["LICENSE_METADATA_ONLY"]:
        release_policy = "QUARANTINE"
    return {
        "schema": SCHEMA, "license_status": status, "release_policy": release_policy,
        "confidence": confidence, "resolution_states": sorted(set(issues)),
        "identifiers": sorted(all_ids),
        "license_files": sorted(str(path.relative_to(root)) for path in license_files),
        "readme_license_identifiers": sorted(readme_ids), "provider_license_identifier": provider_id,
        "per_file_spdx": spdx_by_file,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repos_path = args.corpus_root / "manifests/repositories.jsonl"
    designs_path = args.corpus_root / "manifests/all_designs.jsonl"
    repositories = read_jsonl(repos_path)
    designs = read_jsonl(designs_path)
    roots_by_repo: dict[str, Path] = {}
    for row in designs:
        root = row.get("storage", {}).get("repository_source_path")
        if root:
            roots_by_repo[row.get("provenance", {}).get("repo_id")] = Path(root)
    hints: dict[str, str] = {}
    frontier = default_frontier_path(args.corpus_root)
    if frontier.exists():
        db = sqlite3.connect(frontier)
        for row in repositories:
            try:
                key = canonical_repository_identity(row.get("repository_url", ""))["repository_key"]
            except ValueError:
                continue
            value = db.execute("SELECT license_hint,metadata_json FROM repositories WHERE repository_key=?", (key,)).fetchone()
            if value:
                hint = value[0]
                if not hint and value[1]:
                    try:
                        hint = json.loads(value[1]).get("license_hint")
                    except json.JSONDecodeError:
                        pass
                if hint:
                    hints[row["repo_id"]] = str(hint)
        db.close()
    evidence: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(repositories, 1):
        root = roots_by_repo.get(row["repo_id"])
        if root and root.is_dir():
            evidence[row["repo_id"]] = recover(root, hints.get(row["repo_id"]))
    previous_summary_path = args.corpus_root / "quality/phase1_5/license_evidence_summary.json"
    previous_transitions: Counter[str] = Counter()
    if previous_summary_path.exists():
        try:
            previous_summary = json.loads(previous_summary_path.read_text(encoding="utf-8"))
            previous_transitions.update(previous_summary.get("repository_transitions_cumulative", previous_summary.get("repository_transitions", {})))
        except json.JSONDecodeError:
            pass
    transitions = Counter()
    if args.apply:
        for row in repositories:
            item = evidence.get(row["repo_id"])
            if not item:
                continue
            old = row.get("license_status", "UNKNOWN")
            row["license_evidence"] = item
            if old == "UNKNOWN" and item["confidence"] == "HIGH" and item["license_status"] != "UNKNOWN":
                row["license_status"] = item["license_status"]
                row["release_policy"] = item["release_policy"]
                transitions[(old, item["license_status"])] += 1
        for row in designs:
            item = evidence.get(row.get("provenance", {}).get("repo_id"))
            if not item:
                continue
            release = row.setdefault("release", {})
            old = release.get("license_status", "UNKNOWN")
            release["license_evidence"] = item
            if old == "UNKNOWN" and item["confidence"] == "HIGH" and item["license_status"] != "UNKNOWN":
                release["license_status"] = item["license_status"]
                release["release_policy"] = item["release_policy"]
        atomic_jsonl(repos_path, sorted(repositories, key=lambda row: row["repo_id"]))
        from run_expansion_round import validate_publish_invariants, write_manifests
        design_map = {row["design_id"]: row for row in designs}
        validate_publish_invariants(args.corpus_root, design_map)
        write_manifests(args.corpus_root, design_map)
        with CorpusState(args.corpus_root) as state:
            if not state.populated():
                state.sync_materialized_views()
            else:
                state.apply_incremental(repositories=repositories, designs=design_map.values())
    summary = {
        "schema": SCHEMA, "repositories_audited": len(evidence), "apply": args.apply,
        "unknown_repository_revisions": sum(row.get("license_status", "UNKNOWN") == "UNKNOWN" for row in repositories),
        "unknown_design_instances": sum(row.get("release", {}).get("license_status", "UNKNOWN") == "UNKNOWN" for row in designs),
        "unknown_design_families": len({
            row.get("family_id") for row in designs
            if row.get("release", {}).get("license_status", "UNKNOWN") == "UNKNOWN"
        }),
        "unknown_design_families_definition": "family has at least one DesignInstance with UNKNOWN license status",
        "resolved_status": dict(Counter(item["license_status"] for item in evidence.values())),
        "confidence": dict(Counter(item["confidence"] for item in evidence.values())),
        "resolution_states": dict(Counter(state for item in evidence.values() for state in item["resolution_states"])),
        "repository_transitions": {f"{old}->{new}": count for (old, new), count in transitions.items()},
        "repository_transitions_cumulative": dict(previous_transitions + Counter({f"{old}->{new}": count for (old, new), count in transitions.items()})),
    }
    out = args.corpus_root / "quality/phase1_5"
    out.mkdir(parents=True, exist_ok=True)
    atomic_jsonl(out / "license_evidence.jsonl", [{"repo_id": key, **value} for key, value in sorted(evidence.items())])
    temporary = out / ".license_evidence_summary.json.tmp"
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, out / "license_evidence_summary.json")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
