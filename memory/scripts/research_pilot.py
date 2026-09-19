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
from tehm.evaluation.research_inventory import (  # noqa: E402
    build_research_inventory,
    verify_research_inventory,
)
from tehm.evaluation.research_campaign import (  # noqa: E402
    prepare_research_campaign,
    verify_prepared_campaign,
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


def _inventory(args: argparse.Namespace) -> int:
    result = build_research_inventory(
        corpus_root=args.corpus_root,
        output=args.output,
        acquisition_script=args.acquisition_script,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["corpus_unchanged"] else 2


def _verify_inventory(args: argparse.Namespace) -> int:
    result = verify_research_inventory(args.inventory)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _prepare(args: argparse.Namespace) -> int:
    result = prepare_research_campaign(
        campaign_spec=args.campaign,
        epoch=args.epoch,
        inventory=args.inventory,
        task_contracts=args.task_contracts,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["silently_filtered"] == 0 else 2


def _verify_prepared(args: argparse.Namespace) -> int:
    result = verify_prepared_campaign(args.prepared)
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

    inventory = sub.add_parser(
        "inventory",
        help="build a read-only real-design inventory outside the corpus",
    )
    inventory.add_argument("--corpus-root", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument(
        "--acquisition-script",
        type=Path,
        default=None,
        help="optional pinned rtl-acquire discovery parser",
    )
    inventory.set_defaults(handler=_inventory)

    verify_inventory = sub.add_parser(
        "verify-inventory", help="verify inventory artifacts and source immutability"
    )
    verify_inventory.add_argument("--inventory", type=Path, required=True)
    verify_inventory.set_defaults(handler=_verify_inventory)

    prepare = sub.add_parser(
        "prepare", help="freeze campaign tasks against an epoch and design inventory"
    )
    prepare.add_argument("--campaign", type=Path, required=True)
    prepare.add_argument("--epoch", type=Path, required=True)
    prepare.add_argument("--inventory", type=Path, required=True)
    prepare.add_argument("--task-contracts", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)

    verify_prepared = sub.add_parser(
        "verify-prepared", help="verify frozen campaign inputs and full task denominator"
    )
    verify_prepared.add_argument("--prepared", type=Path, required=True)
    verify_prepared.set_defaults(handler=_verify_prepared)

    args = parser.parse_args(argv)
    if args.command == "freeze-epoch" and args.source_root is None:
        args.source_root = ["memory", "r2g-skills"]
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
