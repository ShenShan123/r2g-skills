#!/usr/bin/env python3
"""Read-only split-closure audit over revision-local pipeline artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from run_expansion_round import (
    FAMILY_SCHEMA,
    SPLIT_GROUP_SCHEMA,
    family_signatures,
    load_jsonl,
    split_hierarchy_top_member,
    split_project_member,
    split_source_members,
    stable_id,
)


SCHEMA = "rtl_staged_closure_audit_v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def current_profile(profile_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = [row for row in profile_index.values() if row.get("status") == "CURRENT"]
    if len(current) > 1:
        raise RuntimeError("multiple CURRENT split profiles")
    return current[0] if current else {
        "profile_id": "rtl_split_profile_v1",
        "split_schema": "rtl_split_v1",
        "split_epoch": "initial_frozen_v1",
    }


def terminal_group(group_id: str, assignments: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    lineage: list[str] = []
    current = group_id
    while current in assignments and assignments[current].get("superseded_by"):
        if current in lineage:
            raise RuntimeError(f"split lineage cycle at {current}")
        lineage.append(current)
        current = str(assignments[current]["superseded_by"])
    return current, lineage


def staged_designs(corpus: Path, round_id: str) -> tuple[list[dict[str, Any]], int]:
    database = corpus / "state/corpus.sqlite"
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """SELECT artifact_path FROM processing_queue
               WHERE round_id=? AND state='TERMINAL' AND artifact_path IS NOT NULL""",
            (round_id,),
        ).fetchall()
    finally:
        connection.close()
    designs: dict[str, dict[str, Any]] = {}
    artifacts = 0
    for (path_value,) in rows:
        path = Path(str(path_value))
        if not path.is_file():
            continue
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifacts += 1
        for record in artifact.get("designs", []):
            design_id = str(record.get("design_id", ""))
            if design_id:
                designs[design_id] = record
    return list(designs.values()), artifacts


def audit(corpus: Path, round_id: str) -> dict[str, Any]:
    signature_index = load_jsonl(corpus / "manifests/family_signature_index.jsonl", "signature")
    assignments = load_jsonl(corpus / "manifests/split_assignments.jsonl", "split_group_id")
    membership = load_jsonl(corpus / "manifests/split_membership_index.jsonl", "member")
    profiles = load_jsonl(corpus / "manifests/split_profiles.jsonl", "profile_id")
    profile = current_profile(profiles)
    designs, artifacts = staged_designs(corpus, round_id)

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    design_family: dict[str, str] = {}
    current_hierarchy_tops: set[str] = set()
    for record in designs:
        signatures = family_signatures(record)
        existing = sorted({
            str(signature_index[value]["family_id"])
            for _, value in signatures if value in signature_index
        })
        family_id = existing[0] if existing else stable_id(
            "f", FAMILY_SCHEMA, signatures[0][1]
        )
        design_family[str(record["design_id"])] = family_id
        family_member = f"family:{family_id}"
        find(family_member)
        union(family_member, split_project_member(record))
        for source_member in split_source_members(record):
            union(family_member, source_member)
        hierarchy_top = split_hierarchy_top_member(record, record["build"]["top_module"])
        current_hierarchy_tops.add(hierarchy_top)
        union(family_member, hierarchy_top)

    known_hierarchy_tops = current_hierarchy_tops | {
        member for member in membership if member.startswith("hierarchy_top:")
    }
    for record in designs:
        family_member = f"family:{design_family[str(record['design_id'])]}"
        for dependency in record.get("build", {}).get("dependency_modules", []):
            hierarchy_member = split_hierarchy_top_member(record, str(dependency))
            if hierarchy_member in known_hierarchy_tops:
                union(family_member, hierarchy_member)

    components: dict[str, set[str]] = defaultdict(set)
    for member in list(parent):
        components[find(member)].add(member)
    conflicts: list[dict[str, Any]] = []
    unknown_groups: set[str] = set()
    for members in components.values():
        raw_groups = {
            str(membership[member]["split_group_id"])
            for member in members if member in membership
        }
        terminal_groups: set[str] = set()
        historical_groups: set[str] = set(raw_groups)
        for group_id in raw_groups:
            target, lineage = terminal_group(group_id, assignments)
            terminal_groups.add(target)
            historical_groups.update(lineage)
        splits: set[str] = set()
        for group_id in terminal_groups:
            split = assignments.get(group_id, {}).get("split")
            if split not in {"train", "val", "test"}:
                unknown_groups.add(group_id)
            else:
                splits.add(str(split))
        if len(splits) < 2:
            continue
        family_ids = sorted(
            member.removeprefix("family:") for member in members
            if member.startswith("family:")
        )
        affected_design_ids = sorted(
            design_id for design_id, family_id in design_family.items()
            if family_id in set(family_ids)
        )
        kind = "TEST_BOUNDARY_INVALIDATION" if "test" in splits else "TRAIN_VAL_RECONCILIATION"
        conflicts.append({
            "kind": kind,
            "old_split_groups": sorted(terminal_groups),
            "affected_historical_groups": sorted(historical_groups),
            "old_splits": sorted(splits),
            "target_split": "test" if "test" in splits else "val",
            "affected_family_ids": family_ids,
            "affected_design_ids": affected_design_ids,
            "closure_members": sorted(members),
            "proposed_canonical_split_group_id": stable_id(
                "sg", SCHEMA, str(profile["profile_id"]), round_id,
                *sorted(terminal_groups),
            ),
        })
    conflicts.sort(key=lambda row: (row["kind"], row["old_split_groups"]))
    return {
        "schema": SCHEMA,
        "round_id": round_id,
        "generated_at": utc_now(),
        "publication_performed": False,
        "active_split_profile_id": profile["profile_id"],
        "active_split_schema": profile["split_schema"],
        "terminal_artifacts_audited": artifacts,
        "staged_designs_audited": len(designs),
        "potential_train_val_components": sum(
            row["kind"] == "TRAIN_VAL_RECONCILIATION" for row in conflicts
        ),
        "potential_test_boundary_conflicts": sum(
            row["kind"] == "TEST_BOUNDARY_INVALIDATION" for row in conflicts
        ),
        "unknown_split_groups": sorted(unknown_groups),
        "conflicts": conflicts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.corpus_root, args.round_id)
    output = args.output or (
        args.corpus_root / "quality/phase2/rounds" / args.round_id / "staged_split_audit.json"
    )
    atomic_json(output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
