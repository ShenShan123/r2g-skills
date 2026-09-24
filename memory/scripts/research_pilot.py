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
from tehm.evaluation.research_flow import (  # noqa: E402
    audit_flow_run,
    compare_flow_replays,
    stage_flow_project,
    verify_flow_audit,
    verify_flow_replay,
    verify_staged_flow_project,
)
from tehm.evaluation.research_coverage import (  # noqa: E402
    audit_memory_route_coverage,
    verify_memory_route_coverage,
)
from tehm.evaluation.research_seed_pair import (  # noqa: E402
    run_seed_pair,
    verify_seed_pair_run,
    verify_seed_pair_spec,
)
from tehm.evaluation.research_seed_m0 import build_research_seed_m0  # noqa: E402
from tehm.evaluation.research_s1_control import (  # noqa: E402
    run_s1_controls,
    verify_s1_control_binding,
    verify_s1_controls,
)
from tehm.evaluation.research_s1_preflight import (  # noqa: E402
    audit_s1_candidate_preflight,
    verify_s1_candidate_preflight,
)
from tehm.evaluation.research_s1_action import (  # noqa: E402
    prepare_s1_treatments,
    verify_s1_treatment_plan,
)
from tehm.evaluation.research_s1_treatment import (  # noqa: E402
    run_s1_treatments,
    verify_s1_treatments,
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


def _stage_flow(args: argparse.Namespace) -> int:
    result = stage_flow_project(
        inventory=args.inventory, design_id=args.design_id, output=args.output
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["source_unchanged"] else 2


def _verify_staged_flow(args: argparse.Namespace) -> int:
    result = verify_staged_flow_project(args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2



def _verify_s1_control_binding(args: argparse.Namespace) -> int:
    result = verify_s1_control_binding(args.binding)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _run_s1_controls(args: argparse.Namespace) -> int:
    result = run_s1_controls(binding=args.binding, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _verify_s1_controls(args: argparse.Namespace) -> int:
    result = verify_s1_controls(binding=args.binding, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _audit_s1_preflight(args: argparse.Namespace) -> int:
    result = audit_s1_candidate_preflight(
        binding=args.binding, controls=args.controls, epoch=args.epoch,
        output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _verify_s1_preflight(args: argparse.Namespace) -> int:
    result = verify_s1_candidate_preflight(args.preflight)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _prepare_s1_treatments(args: argparse.Namespace) -> int:
    result = prepare_s1_treatments(
        preflight=args.preflight, epoch=args.epoch, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _verify_s1_treatments(args: argparse.Namespace) -> int:
    result = verify_s1_treatment_plan(args.plan)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _run_s1_treatments(args: argparse.Namespace) -> int:
    result = run_s1_treatments(plan=args.plan, epoch=args.epoch, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _verify_s1_treatment_run(args: argparse.Namespace) -> int:
    result = verify_s1_treatments(plan=args.plan, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _audit_flow(args: argparse.Namespace) -> int:
    result = audit_flow_run(
        project=args.project,
        run_dir=args.run_dir,
        producer_epoch=args.producer_epoch,
        auditor_epoch=args.auditor_epoch,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _verify_flow_audit(args: argparse.Namespace) -> int:
    result = verify_flow_audit(args.audit)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _compare_flow_replays(args: argparse.Namespace) -> int:
    result = compare_flow_replays(
        baseline_audit=args.baseline_audit,
        replay_audit=args.replay_audit,
        auditor_epoch=args.auditor_epoch,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["reproduced"] else 2


def _verify_flow_replay(args: argparse.Namespace) -> int:
    result = verify_flow_replay(args.replay)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["reproduced"] else 2


def _audit_memory_coverage(args: argparse.Namespace) -> int:
    result = audit_memory_route_coverage(
        epoch=args.epoch, flow_audits=args.flow_audit, output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _verify_memory_coverage(args: argparse.Namespace) -> int:
    result = verify_memory_route_coverage(args.coverage)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _verify_seed_pair_spec(args: argparse.Namespace) -> int:
    result = verify_seed_pair_spec(args.spec)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _run_seed_pair(args: argparse.Namespace) -> int:
    result = run_seed_pair(spec=args.spec, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] and result["all_registered_terminal"] else 2


def _verify_seed_pair_run(args: argparse.Namespace) -> int:
    result = verify_seed_pair_run(args.output, args.spec)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def _build_seed_m0(args: argparse.Namespace) -> int:
    result = build_research_seed_m0(spec=args.spec, output=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["m0_status"] == "BUILT_READ_ONLY_RESEARCH" else 2


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

    stage_flow = sub.add_parser(
        "stage-flow", help="materialize immutable adapter-bound fixed-flow inputs"
    )
    stage_flow.add_argument("--inventory", type=Path, required=True)
    stage_flow.add_argument("--design-id", required=True)
    stage_flow.add_argument("--output", type=Path, required=True)
    stage_flow.set_defaults(handler=_stage_flow)

    verify_staged = sub.add_parser(
        "verify-staged-flow", help="verify immutable staged fixed-flow inputs"
    )
    verify_staged.add_argument("--project", type=Path, required=True)
    verify_staged.set_defaults(handler=_verify_staged_flow)

    s1_binding = sub.add_parser(
        "verify-s1-control-binding", help="verify preregistered S1 control inputs"
    )
    s1_binding.add_argument("--binding", type=Path, required=True)
    s1_binding.set_defaults(handler=_verify_s1_control_binding)

    s1_controls = sub.add_parser(
        "run-s1-controls", help="execute and audit both S1 development controls once"
    )
    s1_controls.add_argument("--binding", type=Path, required=True)
    s1_controls.add_argument("--output", type=Path, required=True)
    s1_controls.set_defaults(handler=_run_s1_controls)

    s1_verify = sub.add_parser(
        "verify-s1-controls", help="recompute the S1 control event chain and raw audits"
    )
    s1_verify.add_argument("--binding", type=Path, required=True)
    s1_verify.add_argument("--output", type=Path, required=True)
    s1_verify.set_defaults(handler=_verify_s1_controls)

    s1_preflight = sub.add_parser(
        "audit-s1-preflight", help="replay M0 in RAM and audit target candidate selection"
    )
    s1_preflight.add_argument("--binding", type=Path, required=True)
    s1_preflight.add_argument("--controls", type=Path, required=True)
    s1_preflight.add_argument("--epoch", type=Path, required=True)
    s1_preflight.add_argument("--output", type=Path, required=True)
    s1_preflight.set_defaults(handler=_audit_s1_preflight)

    s1_preflight_verify = sub.add_parser(
        "verify-s1-preflight", help="recompute scoped S1 candidate preflight"
    )
    s1_preflight_verify.add_argument("--preflight", type=Path, required=True)
    s1_preflight_verify.set_defaults(handler=_verify_s1_preflight)

    s1_prepare = sub.add_parser(
        "prepare-s1-treatments", help="stage only binder-selected isolated treatments"
    )
    s1_prepare.add_argument("--preflight", type=Path, required=True)
    s1_prepare.add_argument("--epoch", type=Path, required=True)
    s1_prepare.add_argument("--output", type=Path, required=True)
    s1_prepare.set_defaults(handler=_prepare_s1_treatments)

    s1_plan_verify = sub.add_parser(
        "verify-s1-treatments", help="recheck selected treatment staging and inputs"
    )
    s1_plan_verify.add_argument("--plan", type=Path, required=True)
    s1_plan_verify.set_defaults(handler=_verify_s1_treatments)

    s1_run = sub.add_parser(
        "run-s1-treatments", help="execute and audit each selected S1 treatment once"
    )
    s1_run.add_argument("--plan", type=Path, required=True)
    s1_run.add_argument("--epoch", type=Path, required=True)
    s1_run.add_argument("--output", type=Path, required=True)
    s1_run.set_defaults(handler=_run_s1_treatments)

    s1_run_verify = sub.add_parser(
        "verify-s1-treatment-run", help="recompute selected S1 treatment raw audits"
    )
    s1_run_verify.add_argument("--plan", type=Path, required=True)
    s1_run_verify.add_argument("--output", type=Path, required=True)
    s1_run_verify.set_defaults(handler=_verify_s1_treatment_run)

    audit_flow = sub.add_parser(
        "audit-flow", help="independently classify a terminal fixed-flow run"
    )
    audit_flow.add_argument("--project", type=Path, required=True)
    audit_flow.add_argument("--run-dir", type=Path, required=True)
    audit_flow.add_argument("--producer-epoch", type=Path, required=True)
    audit_flow.add_argument("--auditor-epoch", type=Path, required=True)
    audit_flow.add_argument("--output", type=Path, required=True)
    audit_flow.set_defaults(handler=_audit_flow)

    verify_flow = sub.add_parser(
        "verify-flow-audit", help="verify a frozen fixed-flow audit and raw references"
    )
    verify_flow.add_argument("--audit", type=Path, required=True)
    verify_flow.set_defaults(handler=_verify_flow_audit)

    compare_replays = sub.add_parser(
        "compare-flow-replays",
        help="compare two isolated independently audited fixed-flow attempts",
    )
    compare_replays.add_argument("--baseline-audit", type=Path, required=True)
    compare_replays.add_argument("--replay-audit", type=Path, required=True)
    compare_replays.add_argument("--auditor-epoch", type=Path, required=True)
    compare_replays.add_argument("--output", type=Path, required=True)
    compare_replays.set_defaults(handler=_compare_flow_replays)

    verify_replay = sub.add_parser(
        "verify-flow-replay", help="verify a frozen fixed-flow replay comparison"
    )
    verify_replay.add_argument("--replay", type=Path, required=True)
    verify_replay.set_defaults(handler=_verify_flow_replay)

    coverage = sub.add_parser(
        "audit-memory-coverage",
        help="derive read-only M0 route coverage from audited fixed-flow results",
    )
    coverage.add_argument("--epoch", type=Path, required=True)
    coverage.add_argument("--flow-audit", type=Path, action="append", required=True)
    coverage.add_argument("--output", type=Path, required=True)
    coverage.set_defaults(handler=_audit_memory_coverage)

    verify_coverage = sub.add_parser(
        "verify-memory-coverage",
        help="recompute a frozen read-only route coverage report",
    )
    verify_coverage.add_argument("--coverage", type=Path, required=True)
    verify_coverage.set_defaults(handler=_verify_memory_coverage)

    seed_spec = sub.add_parser(
        "verify-seed-pair-spec", help="verify preregistered paired-arm seed inputs"
    )
    seed_spec.add_argument("--spec", type=Path, required=True)
    seed_spec.set_defaults(handler=_verify_seed_pair_spec)

    seed_run = sub.add_parser(
        "run-seed-pair", help="execute and independently audit each frozen seed arm once"
    )
    seed_run.add_argument("--spec", type=Path, required=True)
    seed_run.add_argument("--output", type=Path, required=True)
    seed_run.set_defaults(handler=_run_seed_pair)

    seed_verify = sub.add_parser(
        "verify-seed-pair-run", help="recompute registered seed evidence and raw audits"
    )
    seed_verify.add_argument("--spec", type=Path, required=True)
    seed_verify.add_argument("--output", type=Path, required=True)
    seed_verify.set_defaults(handler=_verify_seed_pair_run)

    seed_m0 = sub.add_parser(
        "build-seed-m0",
        help="rebuild and export a read-only M0 from two audited seed pairs",
    )
    seed_m0.add_argument("--spec", type=Path, required=True)
    seed_m0.add_argument("--output", type=Path, required=True)
    seed_m0.set_defaults(handler=_build_seed_m0)

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
