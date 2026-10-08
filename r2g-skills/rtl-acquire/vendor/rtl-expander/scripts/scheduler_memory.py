#!/usr/bin/env python3
"""Export/import scheduler-only memory for leakage-free Cold/Warm campaigns.

Unlike export_frontier_snapshot.py, this artifact deliberately excludes
repositories, immutable revisions, candidates, queries, cursors, provider
state, and attempts.  It transfers only aggregate yield and scheduling priors
into a fresh corpus root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from frontier import FRONTIER_SCHEMA, FrontierDB, default_frontier_path, utc_now
from scheduler import DEFAULT_GAPS


MEMORY_SCHEMA = "rtl_scheduler_memory_v1"
STATIC_KEYS = {"diversity_gaps", "scheduler_config", "discovery_precision_policy"}
SAFE_PREFIXES = ("phase2_source_observation:", "phase2_acquisition_feedback:")
FORBIDDEN_TABLES = (
    "repositories", "repository_revisions", "discovery_events", "queries",
    "repo_edges", "acquisition_attempts", "provider_state", "graph_expansions",
    "round_acquisition_claims", "round_acquisition_budget", "acquisition_executor_budget",
)


class MemoryError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def payload_digest(payload: dict[str, Any]) -> str:
    material = dict(payload)
    material.pop("payload_sha256", None)
    return hashlib.sha256(canonical_bytes(material)).hexdigest()


def safe_family_source_key(key: str) -> bool:
    marker = ":family:"
    if marker not in key:
        return False
    family = key.rsplit(marker, 1)[1]
    return bool(family) and all(part in DEFAULT_GAPS for part in family.split("+"))


def safe_scheduler_key(key: str) -> bool:
    if key in STATIC_KEYS:
        return True
    return any(key.startswith(prefix) and safe_family_source_key(key[len(prefix):])
               for prefix in SAFE_PREFIXES)


def export_memory(corpus_root: Path, output: Path) -> dict[str, Any]:
    frontier = default_frontier_path(corpus_root)
    if not frontier.is_file():
        raise MemoryError(f"frontier database does not exist: {frontier}")
    connection = sqlite3.connect(f"file:{frontier}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # Keep family-level aggregates only. Exact source keys contain the old
        # expanded query text and would quietly turn Warm into query replay.
        source_yield = [dict(row) for row in connection.execute(
            "SELECT * FROM source_yield WHERE source_key LIKE '%:family:%' ORDER BY source_key"
        ) if safe_family_source_key(str(row["source_key"]))]
        scheduler_state = [dict(row) for row in connection.execute(
            "SELECT * FROM scheduler_state ORDER BY key"
        ) if safe_scheduler_key(str(row["key"]))]
    finally:
        connection.close()
    payload = {
        "schema": MEMORY_SCHEMA,
        "frontier_schema": FRONTIER_SCHEMA,
        "exported_at": utc_now(),
        "source_corpus_label": corpus_root.resolve().name,
        "tables": {
            "source_yield": source_yield,
            "scheduler_state": scheduler_state,
        },
        "excluded_state_classes": list(FORBIDDEN_TABLES),
    }
    payload["payload_sha256"] = payload_digest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    temporary.write_bytes(canonical_bytes(payload))
    os.replace(temporary, output)
    return payload


def _require_fresh(db: FrontierDB) -> None:
    dirty = [table for table in FORBIDDEN_TABLES
             if db.connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()]
    memory_dirty = [table for table in ("source_yield", "scheduler_state")
                    if db.connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()]
    if dirty or memory_dirty:
        raise MemoryError(
            "warm memory may only be imported into a fresh frontier; populated tables: "
            + ",".join(dirty + memory_dirty)
        )


def import_memory(corpus_root: Path, input_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryError(f"invalid scheduler memory: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != MEMORY_SCHEMA:
        raise MemoryError("unsupported scheduler memory schema")
    if payload.get("frontier_schema") != FRONTIER_SCHEMA:
        raise MemoryError("scheduler memory frontier schema mismatch")
    if payload_digest(payload) != payload.get("payload_sha256"):
        raise MemoryError("scheduler memory digest mismatch")
    tables = payload.get("tables") or {}
    if set(tables) != {"source_yield", "scheduler_state"}:
        raise MemoryError("scheduler memory contains unexpected tables")
    if any(not safe_scheduler_key(str(row.get("key") or ""))
           for row in tables["scheduler_state"]):
        raise MemoryError("scheduler memory contains unsafe state keys")
    if any(not safe_family_source_key(str(row.get("source_key") or ""))
           for row in tables["source_yield"]):
        raise MemoryError("scheduler memory contains exact or unsafe source keys")

    with FrontierDB(default_frontier_path(corpus_root)) as db:
        _require_fresh(db)
        with db.immediate() as connection:
            for row in tables["source_yield"]:
                connection.execute(
                    """INSERT INTO source_yield(
                         source_key,provider,strategy,candidates,acquired,
                         new_design_instances,new_families,synthesis_valid_families,
                         cpu_hours,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    tuple(row[key] for key in (
                        "source_key", "provider", "strategy", "candidates", "acquired",
                        "new_design_instances", "new_families", "synthesis_valid_families",
                        "cpu_hours", "updated_at",
                    )),
                )
            for row in tables["scheduler_state"]:
                connection.execute(
                    "INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)",
                    (row["key"], row["value_json"], row["updated_at"]),
                )
    return {
        "schema": "rtl_scheduler_memory_import_v1",
        "source_yield_rows": len(tables["source_yield"]),
        "scheduler_state_rows": len(tables["scheduler_state"]),
        "payload_sha256": payload["payload_sha256"],
        "target_corpus": str(corpus_root.resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--corpus-root", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--corpus-root", type=Path, required=True)
    import_parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = (export_memory(args.corpus_root, args.output)
                  if args.command == "export"
                  else import_memory(args.corpus_root, args.input))
    except (MemoryError, OSError, KeyError, TypeError) as exc:
        print(f"scheduler memory rejected: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
