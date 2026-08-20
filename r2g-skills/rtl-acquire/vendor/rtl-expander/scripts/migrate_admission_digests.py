#!/usr/bin/env python3
"""Explicit one-time migration of legacy objects to admission-digest records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from frontier import utc_now


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def receipt(path: Path, schema: str, producer: str) -> dict[str, Any]:
    digest, size = hash_file(path)
    return {
        "schema": schema, "object_id": path.name, "path": str(path),
        "sha256": digest, "size": size, "producer": producer,
        "recorded_at": utc_now(), "rehash_required": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--round-id")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pending: list[tuple[Path, Path, str]] = []
    for name in (
        "family_signature_index.jsonl", "split_membership_index.jsonl",
        "split_assignments.jsonl", "split_profiles.jsonl", "training_gold.jsonl",
    ):
        path = args.corpus_root / "manifests" / name
        target = path.with_name(f"{path.name}.admission.json")
        if path.is_file() and not target.exists():
            pending.append((target, path, "rtl_materialized_view_admission_v1"))
    queue_artifacts: list[tuple[Path, str, str]] = []
    if args.round_id:
        round_dir = args.corpus_root / "quality/phase2/rounds" / args.round_id
        cohort = round_dir / "cohort_lock.json"
        cohort_receipt = cohort.with_name(f"{cohort.name}.admission.json")
        if cohort.is_file() and not cohort_receipt.exists():
            pending.append((cohort_receipt, cohort, "rtl_immutable_artifact_admission_v1"))
        database = args.corpus_root / "state/corpus.sqlite"
        if database.is_file():
            connection = sqlite3.connect(database)
            try:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(processing_queue)")}
                if {"artifact_sha256", "artifact_size"}.issubset(columns):
                    for key, path_text in connection.execute(
                        """SELECT repository_revision_key,artifact_path FROM processing_queue
                           WHERE round_id=? AND state='TERMINAL' AND artifact_path IS NOT NULL
                           AND (artifact_sha256 IS NULL OR artifact_size IS NULL)""", (args.round_id,),
                    ):
                        artifact = Path(path_text)
                        if artifact.is_file():
                            queue_artifacts.append((artifact, args.round_id, str(key)))
            finally:
                connection.close()
    if args.apply:
        for target, source, schema in pending:
            atomic_json(target, receipt(source, schema, __file__))
        queue_updates = []
        for artifact, round_id, key in queue_artifacts:
            digest, size = hash_file(artifact)
            queue_updates.append((digest, size, round_id, key))
        if queue_updates:
            connection = sqlite3.connect(args.corpus_root / "state/corpus.sqlite")
            try:
                with connection:
                    connection.executemany(
                        """UPDATE processing_queue SET artifact_sha256=?,artifact_size=?
                           WHERE round_id=? AND repository_revision_key=?
                           AND artifact_sha256 IS NULL""", queue_updates,
                    )
            finally:
                connection.close()
    summary = {
        "schema": "rtl_admission_digest_migration_v1",
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "receipt_count": len(pending), "queue_update_count": len(queue_artifacts),
        "rehash_required": bool(pending or queue_artifacts) and not args.apply,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
