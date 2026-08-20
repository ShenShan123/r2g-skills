#!/usr/bin/env python3
"""Sync incremental corpus state and materialize an immutable release snapshot."""

import argparse
import json
from pathlib import Path

from corpus_state import materialize_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    identity = materialize_snapshot(args.corpus_root, args.snapshot_id)
    completion_path = args.corpus_root / "snapshots" / identity["corpus_snapshot_id"] / "completion.json"
    completion = json.loads(completion_path.read_text())
    print(json.dumps({"release_identity": identity, "completion": completion}, indent=2, sort_keys=True))
    return 0 if completion.get("status") == "CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
