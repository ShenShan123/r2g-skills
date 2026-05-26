#!/usr/bin/env python3
"""Read-only API over knowledge/heuristics.json.

Usage (CLI):
  query_knowledge.py family <family> [--platform <p>]
  query_knowledge.py list

Other scripts (notably suggest_config.py) import this module directly
and call get_family_heuristics().
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import knowledge_db

DEFAULT_HEURISTICS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"


def _load(heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH) -> dict[str, Any]:
    p = Path(heuristics_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_family_heuristics(family: str,
                          platform: str,
                          heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                          ) -> dict[str, Any] | None:
    data = _load(heuristics_path)
    fam = (data.get("families") or {}).get(family)
    if not fam:
        return None
    return (fam.get("platforms") or {}).get(platform)


def list_families(heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                  ) -> list[tuple[str, str]]:
    data = _load(heuristics_path)
    out: list[tuple[str, str]] = []
    for fam_name, fam in (data.get("families") or {}).items():
        for plat in (fam.get("platforms") or {}):
            out.append((fam_name, plat))
    return sorted(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("family", help="Look up heuristics for one family/platform")
    pf.add_argument("family")
    pf.add_argument("--platform", default="nangate45")
    pf.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)

    pl = sub.add_parser("list", help="List known (family, platform) pairs")
    pl.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)

    args = p.parse_args()

    if args.cmd == "family":
        result = get_family_heuristics(args.family, args.platform,
                                       heuristics_path=args.heuristics)
        if result is None:
            print(f"No heuristics for ({args.family}, {args.platform}).",
                  file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "list":
        pairs = list_families(heuristics_path=args.heuristics)
        if not pairs:
            print("(empty)")
            return 0
        for fam, plat in pairs:
            print(f"{fam}\t{plat}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
