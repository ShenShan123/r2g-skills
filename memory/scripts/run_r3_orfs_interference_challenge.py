#!/usr/bin/env python3
"""Run a real-ORFS Revision3 memory-interference challenge cohort.

The producer accepts explicit ORFS project directories and derives the RTL
inputs from each project's ``constraints/config.mk``.  It never reads an ORFS
campaign manifest or a gold fix.  A pre-registered, intentionally unsafe
``flow.CONFIG_DELTA`` candidate is executed against two or more source-
disjoint external designs.  The real four-arm receipts then drive the typed
``MEMORY_INTERFERENCE`` detector, P13 reason envelope, trigger, and admission
reports.  All output is evaluation-only and external to the repository.

This is a challenge/diagnostic lane, not production authority and not a
canonical-memory importer.  The candidate is deliberately harmful so that a
real oracle can demonstrate the detector; it must not be described as a
production memory rule or as a capability gain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from tehm.evaluation.candidate_executor import P12_ARMS  # noqa: E402
from tehm.evaluation.orfs_candidate_oracle import (  # noqa: E402
    OrfsCandidateOracle, _file_sha256, _source_binding, _source_inputs,
)
from tehm.evaluation.orfs_cohort import execute_orfs_paired_cohort  # noqa: E402
from tehm.evolution.admission import admit_evolution_reason  # noqa: E402
from tehm.evolution.p12_shadow_trigger import (  # noqa: E402
    build_p12_shadow_update_triggers_from_reason_receipt,
)
from tehm.evolution.reason_derivation import (  # noqa: E402
    derive_memory_interference_reason, p13_reason_receipt_from_derivations,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


CAMPAIGN_VERSION = "tehm-r3-orfs-interference-challenge-v0.1"
DEFAULT_CAMPAIGN_ID = "tehm-r3-orfs-interference-challenge-20260903"
_CONFIG_VALUE = re.compile(
    r"^\s*export\s+(DESIGN_NAME|PLATFORM)\s*=\s*([^#\s]+)", re.MULTILINE)
_VERILOG_FILES = re.compile(
    r"^\s*export\s+VERILOG_FILES\s*=\s*(.+?)\s*$", re.MULTILINE)


class OrfsInterferenceChallengeError(ValueError):
    """The explicit external ORFS challenge inputs are malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _required_path(value: Path | str | None, name: str, *, directory: bool = False) -> Path:
    if value is None:
        raise OrfsInterferenceChallengeError(f"{name} is required")
    path = Path(value).expanduser().resolve()
    if (path.is_dir() if directory else path.is_file()) is not True:
        kind = "directory" if directory else "file"
        raise OrfsInterferenceChallengeError(f"{name} is not a {kind}: {path}")
    return path


def _config_values(project: Path) -> tuple[str, str, str]:
    config = project / "constraints" / "config.mk"
    if not config.is_file():
        raise OrfsInterferenceChallengeError(f"config.mk is missing: {config}")
    text = config.read_text()
    values = dict(_CONFIG_VALUE.findall(text))
    design = values.get("DESIGN_NAME")
    platform = values.get("PLATFORM")
    if not design or not platform:
        raise OrfsInterferenceChallengeError(
            f"config.mk must export DESIGN_NAME and PLATFORM: {config}")
    match = _VERILOG_FILES.search(text)
    if match is None:
        raise OrfsInterferenceChallengeError(
            f"config.mk must export explicit VERILOG_FILES: {config}")
    # The source list is an immutable input, not a shell command.  Reject
    # substitutions and command-like tokens instead of evaluating config.mk.
    try:
        raw_paths = shlex.split(match.group(1))
    except ValueError as exc:
        raise OrfsInterferenceChallengeError(
            f"VERILOG_FILES cannot be parsed: {config}") from exc
    if not raw_paths or any("$" in item or "`" in item for item in raw_paths):
        raise OrfsInterferenceChallengeError(
            f"VERILOG_FILES must contain explicit paths: {config}")
    source_paths = tuple(Path(item).expanduser().resolve() for item in raw_paths)
    if any(not item.is_file() for item in source_paths):
        missing = next(item for item in source_paths if not item.is_file())
        raise OrfsInterferenceChallengeError(
            f"VERILOG_FILES source is missing: {missing}")
    return design, platform, " ".join(str(item) for item in source_paths)


def _candidate(design: str, *, core_utilization: str) -> StructuredRepairCandidate:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", design).strip("_") or "design"
    candidate_id = f"r3-orfs-harmful-core{core_utilization}-{slug}"
    return StructuredRepairCandidate(
        candidate_id=candidate_id,
        resolved_state_id=f"r3-orfs-state-{slug}",
        knowledge_object_id="r3-orfs-external-challenge@1",
        causal_path_ids=(f"r3-orfs-path-{slug}",),
        asset_id="r3-orfs-harmful-core-utilization",
        action_family="DENSITY_RELIEF",
        concrete_action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": "DENSITY_RELIEF",
            "payload": {
                "config_edits": {"CORE_UTILIZATION": core_utilization},
                "rerun_from": "synth", "recheck": "route",
            },
        },
        applicability_receipt_id=f"r3-orfs-app-{slug}",
        binding_receipt_id=f"r3-orfs-binding-{slug}",
        obligations=("ORFS_FLOW_PASS", "ORFS_ROUTE_PASS", "ORFS_SIGNOFF_PASS"),
        evidence_level="L3_REPLICATED_EFFECT",
        authority={"eligible": True}, risk={},
        provenance={
            "evaluation_only": True,
            "source": "r3-pre-registered-external-challenge",
            "canonical_memory_mutation": "none",
        },
    )


def _case(project: Path, *, index: int, lineage_id: str | None,
          orfs_root: Path, openroad_exe: Path, yosys_exe: Path,
          pdk_root: Path, toolchain_root: Path | None,
          toolchain_manifest: Path | None, toolchain_digest: str,
          oracle_digest: str, platform_digest: str, pdk_digest: str,
          core_utilization: str, campaign_id: str) -> tuple[dict, StructuredRepairCandidate,
                                                               MemoryRoutingDecision]:
    design, platform, source_string = _config_values(project)
    source_paths = tuple(Path(item) for item in source_string.split(" "))
    source_inputs = _source_inputs([
        {"path": str(path), "sha256": _file_sha256(path)}
        for path in source_paths
    ])
    candidate = _candidate(design, core_utilization=core_utilization)
    case_id = f"{campaign_id}:{index}:{design}"
    lineage = lineage_id or f"{campaign_id}:lineage:{platform}:{design}:{index}"
    route = MemoryRoutingDecision(
        decision="CONSIDER", resolved_state_id=candidate.resolved_state_id,
        selected_rule_ids=(f"r3-orfs-rule-{design}",),
        selected_path_ids=candidate.causal_path_ids,
        selected_asset_ids=(candidate.asset_id,),
        applicability={"status": "APPLICABLE", "challenge": True},
        causal_support={"causal_path_ids": list(candidate.causal_path_ids)},
        risk={"level": "challenge"}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=1,
    )
    case = {
        "case_id": case_id, "lineage_id": lineage,
        "project_dir": str(project), "platform": platform,
        "target_check": "route",
        "run_flow_script": str(ROOT / "../r2g-skills/signoff-loop/scripts/flow/run_orfs.sh"),
        "fix_signoff_script": str(ROOT / "../r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh"),
        "orfs_root": str(orfs_root), "openroad_exe": str(openroad_exe),
        "yosys_exe": str(yosys_exe), "pdk_root": str(pdk_root),
        "toolchain_root": (str(toolchain_root) if toolchain_root is not None else None),
        "toolchain_manifest": (str(toolchain_manifest) if toolchain_manifest is not None else None),
        "toolchain_digest": toolchain_digest, "oracle_digest": oracle_digest,
        "platform_digest": platform_digest, "pdk_digest": pdk_digest,
        "source_inputs": [dict(item) for item in source_inputs],
        "source_digest": _source_binding(project, source_inputs),
        "routing_receipt_id": route.routing_receipt_id,
        "routing_decision": route.decision,
    }
    return case, candidate, route


def run(projects: Sequence[Path | str], *, artifacts: Path | str,
        orfs_root: Path | str, openroad_exe: Path | str, yosys_exe: Path | str,
        pdk_root: Path | str, toolchain_digest: str, oracle_digest: str,
        platform_digest: str, pdk_digest: str, toolchain_root: Path | str | None = None,
        toolchain_manifest: Path | str | None = None,
        lineages: Sequence[str] | None = None,
        campaign_id: str = DEFAULT_CAMPAIGN_ID, core_utilization: str = "99",
        force: bool = False, timeout: int | None = None) -> dict:
    if len(projects) < 2:
        raise OrfsInterferenceChallengeError(
            "at least two explicit external projects are required")
    if lineages is not None and len(lineages) != len(projects):
        raise OrfsInterferenceChallengeError("lineages must cover every project")
    if not str(core_utilization).isdigit() or not 1 <= int(core_utilization) <= 100:
        raise OrfsInterferenceChallengeError("core_utilization must be an integer from 1 to 100")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise OrfsInterferenceChallengeError("campaign_id is required")
    artifacts = Path(artifacts).expanduser().resolve()
    if artifacts.exists():
        if not force:
            raise OrfsInterferenceChallengeError(
                f"output exists; pass --force to replace it: {artifacts}")
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    receipts_dir = artifacts / "receipts"
    receipts_dir.mkdir()

    project_paths = tuple(_required_path(item, "project", directory=True)
                          for item in projects)
    orfs_root = _required_path(orfs_root, "orfs_root", directory=True)
    openroad_exe = _required_path(openroad_exe, "openroad_exe")
    yosys_exe = _required_path(yosys_exe, "yosys_exe")
    pdk_root = _required_path(pdk_root, "pdk_root", directory=True)
    toolchain_root = (None if toolchain_root is None else
                      _required_path(toolchain_root, "toolchain_root", directory=True))
    toolchain_manifest = (None if toolchain_manifest is None else
                          _required_path(toolchain_manifest, "toolchain_manifest"))
    for name, value in (("toolchain_digest", toolchain_digest),
                        ("oracle_digest", oracle_digest),
                        ("platform_digest", platform_digest),
                        ("pdk_digest", pdk_digest)):
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise OrfsInterferenceChallengeError(f"{name} must be a sha256 digest")

    cases: list[dict] = []
    candidates: dict[str, StructuredRepairCandidate] = {}
    routes: dict[str, MemoryRoutingDecision] = {}
    for index, project in enumerate(project_paths):
        case, candidate, route = _case(
            project, index=index,
            lineage_id=(None if lineages is None else lineages[index]),
            orfs_root=orfs_root, openroad_exe=openroad_exe, yosys_exe=yosys_exe,
            pdk_root=pdk_root, toolchain_root=toolchain_root,
            toolchain_manifest=toolchain_manifest,
            toolchain_digest=toolchain_digest, oracle_digest=oracle_digest,
            platform_digest=platform_digest, pdk_digest=pdk_digest,
            core_utilization=str(core_utilization), campaign_id=campaign_id)
        cases.append(case)
        candidates[case["case_id"]] = candidate
        routes[case["case_id"]] = route

    manifest_payload = {
        "version": CAMPAIGN_VERSION, "campaign_id": campaign_id,
        "lane": "EVOLUTION_CHALLENGE", "challenge_reason": "MEMORY_INTERFERENCE",
        "candidate_policy": "pre_registered_harmful_flow_config_delta",
        "core_utilization": str(core_utilization),
        "cases": cases,
        "candidate_payloads": {
            case_id: candidate.to_dict() for case_id, candidate in sorted(candidates.items())
        },
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }
    manifest_digest = _digest(manifest_payload)
    cases_payload = {
        "cases": cases,
        "candidate_payloads": {
            case_id: candidate.to_dict()
            for case_id, candidate in sorted(candidates.items())
        },
        "routing": {
            case_id: {**route.to_dict(),
                      "routing_receipt_id": route.routing_receipt_id,
                      "decision_digest": route.decision_digest}
            for case_id, route in sorted(routes.items())
        },
    }
    # Persist the immutable challenge inputs before starting any expensive flow.
    # If a process is interrupted, the artifact still proves exactly what was
    # attempted; memory/docs remains local-only and is never copied here.
    _write_json(receipts_dir / "campaign_manifest.json", manifest_payload)
    _write_json(receipts_dir / "cases.json", cases_payload)
    lock_dir = artifacts / "locks"
    lock_dir.mkdir()
    if timeout is not None:
        if type(timeout) is not int or timeout < 1:
            raise OrfsInterferenceChallengeError("timeout must be positive")
        os.environ["ORFS_TIMEOUT"] = str(timeout)
    oracle = OrfsCandidateOracle(environment={"R2G_LOCK_DIR": str(lock_dir)})
    arms = {
        case["case_id"]: {
            arm: (None if arm == "NO_MEMORY" else candidates[case["case_id"]])
            for arm in P12_ARMS
        }
        for case in cases
    }
    try:
        cohort = execute_orfs_paired_cohort(
            cases, arms, campaign_id=campaign_id,
            campaign_manifest_digest=manifest_digest,
            platform_digest=platform_digest, pdk_digest=pdk_digest,
            oracle=oracle, budget=3, toolchain_digest=toolchain_digest,
            oracle_digest=oracle_digest, min_lineages=2)
    except Exception as exc:
        # No partial cohort object can be honestly reconstructed from the
        # current executor.  Record the immutable inputs and the terminal
        # execution error rather than leaving an apparently empty campaign.
        failure = {
            "version": CAMPAIGN_VERSION, "campaign_id": campaign_id,
            "lane": "EVOLUTION_CHALLENGE",
            "challenge_reason": "MEMORY_INTERFERENCE",
            "status": "EXECUTION_FAILED",
            "error_type": type(exc).__name__, "error": str(exc),
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False, "memory_docs_submitted": False,
        }
        _write_json(artifacts / "failure.json", failure)
        raise

    # The paired execution itself is valuable evidence even when the selected
    # challenge candidate does not produce the requested evolution reason.
    # Write it before deriving/admitting any reason so fail-closed diagnostics
    # never discard a complete real cohort.
    _write_json(receipts_dir / "cohort.json",
                {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest})

    derivations = {}
    derivation_errors = {}
    for case_id, paired in sorted(cohort.case_receipts.items()):
        try:
            reason = derive_memory_interference_reason(
                paired, campaign_id=campaign_id, memory_arm="ALWAYS_MEMORY")
        except Exception as exc:
            derivation_errors[case_id] = (
                f"{type(exc).__name__}: {exc}")
            continue
        if reason is None:
            derivation_errors[case_id] = "case did not produce MEMORY_INTERFERENCE"
            continue
        derivations[case_id] = (reason,)
    _write_json(receipts_dir / "reason_derivation.json", {
        "derivations": {
            case_id: [{**item.to_dict(), "receipt_id": item.receipt_id,
                       "receipt_digest": item.receipt_digest}
                      for item in items]
            for case_id, items in sorted(derivations.items())
        },
        "errors": dict(sorted(derivation_errors.items())),
    })
    if derivation_errors:
        failure = {
            "version": CAMPAIGN_VERSION, "campaign_id": campaign_id,
            "lane": "EVOLUTION_CHALLENGE",
            "challenge_reason": "MEMORY_INTERFERENCE",
            "status": "REASON_DERIVATION_FAILED",
            "case_count": len(cohort.case_receipts),
            "lineage_count": cohort.lineage_count,
            "outcome_counts": cohort.outcome_counts,
            "cohort_receipt_digest": cohort.receipt_digest,
            "reason_derivation_errors": dict(sorted(derivation_errors.items())),
            "evaluation_only": True, "canonical_memory_mutation": "none",
            "production_runtime_imported": False, "memory_docs_submitted": False,
        }
        _write_json(artifacts / "failure.json", failure)
        _write_json(artifacts / "summary.json", failure)
        details = "; ".join(
            f"{case_id}: {reason}"
            for case_id, reason in sorted(derivation_errors.items()))
        raise OrfsInterferenceChallengeError(
            f"ORFS challenge reason derivation failed ({details})")

    reason_receipt = p13_reason_receipt_from_derivations(
        derivations, campaign_id=campaign_id,
        cohort_receipt_digest=cohort.receipt_digest)
    eligibility = {case["case_id"]: True for case in cases}
    triggers = build_p12_shadow_update_triggers_from_reason_receipt(
        cohort, memory_arm="ALWAYS_MEMORY", learner_eligible=True,
        reason_receipt=reason_receipt, min_lineages=2,
        routing_decisions=routes, case_learner_eligibility=eligibility,
        derivation_receipts=derivations)
    admissions = {
        case_id: admit_evolution_reason(
            derivations[case_id][0], campaign_id=campaign_id,
            learner_eligible=True, paired=cohort.case_receipts[case_id],
            memory_arm="ALWAYS_MEMORY")
        for case_id in sorted(cohort.case_receipts)
    }

    _write_json(receipts_dir / "p13_reason_receipt.json",
                {**reason_receipt.to_dict(), "receipt_digest": reason_receipt.receipt_digest})
    _write_json(receipts_dir / "p12_triggers.json", {
        "triggers": [{**item.to_dict(), "receipt_digest": item.receipt_digest}
                     for item in triggers]
    })
    _write_json(receipts_dir / "admissions.json", {
        "admissions": {
            case_id: {**item.to_dict(), "receipt_id": item.receipt_id,
                      "receipt_digest": item.receipt_digest}
            for case_id, item in sorted(admissions.items())
        }
    })
    summary = {
        "version": CAMPAIGN_VERSION, "campaign_id": campaign_id,
        "lane": "EVOLUTION_CHALLENGE", "challenge_reason": "MEMORY_INTERFERENCE",
        "oracle": "real ORFS run_orfs.sh + fix_signoff.sh",
        "case_count": len(cohort.case_receipts), "lineage_count": cohort.lineage_count,
        "lineages": cohort.lineage_ids, "outcome_counts": cohort.outcome_counts,
        "derived_reasons": reason_receipt.evolution_reasons,
        "triggered_count": sum(item.triggered for item in triggers),
        "admitted_count": sum(item.admitted for item in admissions.values()),
        "trigger_reasons": {item.case_id: item.reason for item in triggers},
        "admission_reasons": {case_id: item.blocked_reason
                              for case_id, item in sorted(admissions.items())},
        "cohort_receipt_digest": cohort.receipt_digest,
        "evaluation_only": True, "canonical_memory_mutation": "none",
        "production_runtime_imported": False, "memory_docs_submitted": False,
    }
    _write_json(artifacts / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", action="append", required=True,
                        help="external ORFS project; provide at least two")
    parser.add_argument("--lineage", action="append",
                        help="explicit lineage per project (repeat with --project)")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)
    parser.add_argument("--core-utilization", default="99")
    parser.add_argument("--orfs-root", type=Path, required=True)
    parser.add_argument("--openroad-exe", type=Path, required=True)
    parser.add_argument("--yosys-exe", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument("--toolchain-root", type=Path)
    parser.add_argument("--toolchain-manifest", type=Path)
    parser.add_argument("--toolchain-digest", required=True)
    parser.add_argument("--oracle-digest", required=True)
    parser.add_argument("--platform-digest", required=True)
    parser.add_argument("--pdk-digest", required=True)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(
            args.project, artifacts=args.artifacts, campaign_id=args.campaign_id,
            lineages=args.lineage, core_utilization=args.core_utilization,
            orfs_root=args.orfs_root, openroad_exe=args.openroad_exe,
            yosys_exe=args.yosys_exe, pdk_root=args.pdk_root,
            toolchain_root=args.toolchain_root, toolchain_manifest=args.toolchain_manifest,
            toolchain_digest=args.toolchain_digest, oracle_digest=args.oracle_digest,
            platform_digest=args.platform_digest, pdk_digest=args.pdk_digest,
            timeout=args.timeout, force=args.force)
    except (OSError, OrfsInterferenceChallengeError, TypeError, ValueError) as exc:
        print(f"challenge failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
