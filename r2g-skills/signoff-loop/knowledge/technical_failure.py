#!/usr/bin/env python3
"""Append-only record of TECHNICAL failures in a repair session (2026-10-17).

Distinguishes a technical failure (an invalid candidate, a crashed diagnose or memory
consultation) from a normal "no match" and from a normal repair failure. A recorded failure
stops the session and the episode before any further repair turn, and is surfaced to the
integrity monitor; it is never suppressed into an ordinary residual.

  technical_failure.py record <project> --kind K --source S --detail D [--check C]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

KINDS = ("invalid_candidate", "diagnose_crash", "consultation_crash")
FILE = "reports/technical_failures.jsonl"


def record(project, kind, *, source, detail, check=None, iteration=None) -> dict:
    if kind not in KINDS:
        raise ValueError("unknown technical failure kind: %r" % kind)
    row = {"ts": time.time(), "kind": kind, "source": source, "detail": str(detail)[:2000],
           "check": check, "iter": iteration}
    path = Path(project) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def load(project) -> list[dict]:
    path = Path(project) / FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("project")
    r.add_argument("--kind", required=True, choices=KINDS)
    r.add_argument("--source", required=True)
    r.add_argument("--detail", required=True)
    r.add_argument("--check")
    r.add_argument("--iter")
    a = p.parse_args(argv)
    record(a.project, a.kind, source=a.source, detail=a.detail, check=a.check, iteration=a.iter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
