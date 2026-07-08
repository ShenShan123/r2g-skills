#!/usr/bin/env python3
"""Atomic reports/*.json writer shared by the extract scripts.

Every extractor used to `write_text` its report in place — truncate-then-write —
so a `kill -9 -<pgid>` (the documented orphan-cleanup for stuck drivers), an OOM
kill, or disk-full mid-write left a TRUNCATED reports/*.json. ingest_run then
read it as a blank/failed report and the run silently vanished from the
knowledge store, breaking the "ingest after EVERY flow" honesty invariant
(2026-07-04 robustness audit, M1). tmp + os.replace() is atomic on POSIX: a
reader sees either the old complete report or the new complete report, never a
torn one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path: Path | str, obj, *, indent: int = 2,
                      ensure_ascii: bool = False, sort_keys: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii,
                                  sort_keys=sort_keys) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)     # no stray tmp on a failed dump/replace
