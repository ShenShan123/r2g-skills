#!/usr/bin/env python3
"""Unified command facade for TEHM Research Runtime RC1."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm.evaluation.research_epoch import (  # noqa: E402
    freeze_research_epoch,
    verify_research_epoch,
)


def _freeze_epoch(args: argparse.Namespace) -> int:
    result = freeze_research_epoch(
        repo_root=args.repo_root,
        output=args.output,
        epoch_id=args.epoch_id,
        toolchain_manifest=args.toolchain_manifest,
        memory_snapshot=args.memory_snapshot,
        schema_path=args.schema,
        oracle_paths=args.oracle,
        controller_config=args.controller,
        budget_config=args.budget,
        evidence_status=args.evidence_status,
        source_roots=args.source_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["research_evaluation_ready"] else 2


def _verify_epoch(args: argparse.Namespace) -> int:
    result = verify_research_epoch(args.epoch)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser(
        "freeze-epoch",
        help="freeze Git/source/schema/toolchain/oracle/controller/budget/M0",
    )
    freeze.add_argument("--repo-root", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--epoch-id", required=True)
    freeze.add_argument("--toolchain-manifest", type=Path, required=True)
    freeze.add_argument("--memory-snapshot", type=Path, required=True)
    freeze.add_argument("--schema", type=Path, required=True)
    freeze.add_argument("--oracle", type=Path, action="append", required=True)
    freeze.add_argument("--controller", type=Path, required=True)
    freeze.add_argument("--budget", type=Path, required=True)
    freeze.add_argument("--evidence-status", type=Path, required=True)
    freeze.add_argument(
        "--source-root",
        action="append",
        default=None,
        help="repository-relative source root (repeatable; defaults to memory,r2g-skills)",
    )
    freeze.set_defaults(handler=_freeze_epoch)

    verify = sub.add_parser("verify-epoch", help="verify a frozen research epoch")
    verify.add_argument("--epoch", type=Path, required=True)
    verify.set_defaults(handler=_verify_epoch)

    args = parser.parse_args(argv)
    if args.command == "freeze-epoch" and args.source_root is None:
        args.source_root = ["memory", "r2g-skills"]
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
