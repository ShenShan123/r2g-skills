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
    bind_research_inventory_adapters,
    build_research_inventory,
    verify_research_inventory,
)
from tehm.evaluation.research_campaign import (  # noqa: E402
    prepare_research_campaign,
    verify_prepared_campaign,
)
from tehm.evaluation.research_ledger import (  # noqa: E402
    audit_attempt_ledger,
    verify_attempt_ledger,
    verify_research_audit,
)
from tehm.evaluation.research_runtime import run_research_campaign  # noqa: E402


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


def _adapt_inventory(args: argparse.Namespace) -> int:
    result = bind_research_inventory_adapters(
        inventory=args.inventory,
        adapter_spec=args.adapter_spec,
        authority_root=args.authority_root,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["corpus_unchanged"] else 2


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


def _verify_ledger(args: argparse.Namespace) -> int:
    result = verify_attempt_ledger(prepared=args.prepared, ledger=args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _audit(args: argparse.Namespace) -> int:
    result = audit_attempt_ledger(
        prepared=args.prepared, ledger=args.ledger, output=args.output
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _run(args: argparse.Namespace) -> int:
    result = run_research_campaign(prepared=args.prepared, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _summarize(args: argparse.Namespace) -> int:
    result = verify_research_audit(args.audited_campaign)
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

    adapt_inventory = sub.add_parser(
        "adapt-inventory",
        help="bind explicit non-mutating adapters to selected frozen designs",
    )
    adapt_inventory.add_argument("--inventory", type=Path, required=True)
    adapt_inventory.add_argument("--adapter-spec", type=Path, required=True)
    adapt_inventory.add_argument("--authority-root", type=Path, required=True)
    adapt_inventory.add_argument("--output", type=Path, required=True)
    adapt_inventory.set_defaults(handler=_adapt_inventory)

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

    verify_ledger = sub.add_parser(
        "verify-ledger", help="verify the append-only attempt hash chain and budgets"
    )
    verify_ledger.add_argument("--prepared", type=Path, required=True)
    verify_ledger.add_argument("--ledger", type=Path, required=True)
    verify_ledger.set_defaults(handler=_verify_ledger)

    run = sub.add_parser(
        "run", help="execute the complete frozen campaign through the RC1 runtime"
    )
    run.add_argument("--prepared", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.set_defaults(handler=_run)

    audit = sub.add_parser(
        "audit", help="independently recompute scoped verdicts from raw attempt evidence"
    )
    audit.add_argument("--prepared", type=Path, required=True)
    audit.add_argument("--ledger", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.set_defaults(handler=_audit)

    summarize = sub.add_parser(
        "summarize", help="verify and project an independently audited campaign"
    )
    summarize.add_argument("--audited-campaign", type=Path, required=True)
    summarize.set_defaults(handler=_summarize)

    args = parser.parse_args(argv)
    if args.command == "freeze-epoch" and args.source_root is None:
        args.source_root = ["memory", "r2g-skills"]
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
