#!/usr/bin/env python3
"""Export reproducible JSONL views from the mutable SQLite frontier."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from frontier import FRONTIER_SCHEMA, FrontierDB, default_frontier_path, utc_now


EXPORT_TABLES = ["repositories", "discovery_events", "queries", "repo_edges", "acquisition_attempts", "provider_state", "source_yield", "scheduler_state", "repository_revisions", "graph_expansions"]


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(value)
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.corpus_root / "manifests" / "frontier"
    counts = {}
    with FrontierDB(default_frontier_path(args.corpus_root)) as db:
        for table in EXPORT_TABLES:
            rows = [dict(row) for row in db.connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            atomic_text(target / f"{table}.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
            counts[table] = len(rows)
    metadata = {"schema": "rtl_frontier_export_v1", "frontier_schema": FRONTIER_SCHEMA, "exported_at": utc_now(), "counts": counts}
    atomic_text(target / "export.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
