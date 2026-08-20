#!/usr/bin/env python3
"""Atomically rebuild the disposable corpus index from immutable ledger truth."""

import argparse
import json
import os
from pathlib import Path

from corpus_state import CorpusState, read_jsonl


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", required=True, type=Path)
    args = parser.parse_args()
    corpus = args.corpus_root.resolve()
    target = corpus / "state" / "corpus.sqlite"
    temporary = target.with_name(f".{target.name}.rebuild.{os.getpid()}")
    state = CorpusState.__new__(CorpusState)
    state.corpus, state.database, state.ledger = corpus, temporary, corpus / "ledger"
    state._pending_events = None
    import sqlite3
    state.connection = sqlite3.connect(temporary, timeout=60)
    state.connection.row_factory = sqlite3.Row
    state.connection.execute("PRAGMA journal_mode=WAL")
    state.connection.execute("PRAGMA synchronous=FULL")
    state._schema()
    latest = {}
    events = []
    for path in sorted(state.ledger.glob("*_events.jsonl")):
        for event in read_jsonl(path):
            events.append(event)
            if event["stream"] == "repository" and event["event_type"] in {
                "REPOSITORY_REVISION_CREATED", "REPOSITORY_STATE_UPDATED"
            }:
                latest[(event["stream"], event["object_id"])] = event["payload"]
            elif event["stream"] == "design" and event["event_type"] in {
                "DESIGN_CREATED", "DESIGN_UPDATED"
            }:
                latest[(event["stream"], event["object_id"])] = event["payload"]
            elif event["stream"] == "design" and event["event_type"] == "DESIGN_RETIRED":
                latest.pop((event["stream"], event["object_id"]), None)
    with state.connection:
        # Seed event identities first, so materialization cannot append replayed facts.
        state.connection.executemany(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
            [(e["event_id"], e["stream"], e["event_type"], e["object_id"],
              e["payload_sha256"], e["occurred_at"]) for e in events],
        )
        state.upsert_repositories(sorted(
            (v for (s, _), v in latest.items() if s == "repository"),
            key=lambda row: row["repo_id"],
        ))
        state.upsert_designs(sorted(
            (v for (s, _), v in latest.items() if s == "design"),
            key=lambda row: row["design_id"],
        ))
    result = {"events_replayed": len(events), **state.metrics()}
    state.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(temporary) + suffix)
        if sidecar.exists():
            raise RuntimeError(f"SQLite sidecar remains after close: {sidecar}")
    os.replace(temporary, target)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
