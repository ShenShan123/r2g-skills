#!/usr/bin/env python3
"""Run a frozen, source-disjoint ORFS P12 cohort from a JSON manifest.

The manifest is the only campaign input.  Every memory arm is loaded from a
serialized ``StructuredRepairCandidate`` (or explicitly set to ``null``), and
the existing ORFS oracle/cohort harness performs the real four-arm execution.
This command does not open SQLite, infer NO_SKILL reasons, or import any result
into canonical memory, lifecycle authority, or production runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryRoutingDecision  # noqa: E402
from tehm.evaluation import P12_ARMS, OrfsCandidateOracle, execute_orfs_paired_cohort  # noqa: E402
from tehm.evaluation.orfs_cohort import OrfsCohortExecutionError  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID,
    known_utility_contracts, utility_contract_digest,
)
from tehm.retrieval.structured_candidate import StructuredRepairCandidate  # noqa: E402


MANIFEST_VERSION = "p12-orfs-cohort-manifest-v1"
REPORT_VERSION = "p12-orfs-cohort-run-report-v1"
TERMINAL_REPORT_VERSION = "p12-orfs-cohort-terminal-report-v1"
_MEMORY_ARMS = frozenset(P12_ARMS[1:])
_SOURCE_BOUND_AUTHORITY_VERSION = "r3-8-source-bound-orfs-input-authority-v1"
_PREREGISTRATION_V2 = "r3-8-source-bound-orfs-preregistration-v2"
_ORACLE_BINDING_VERSION = "r3-8-orfs-oracle-binding-v1"


class P12OrfsRunError(ValueError):
    """A P12 manifest or candidate binding is malformed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _source_bound_authority(manifest_path: Path, manifest: Mapping,
                            case_ids: set[str]) -> tuple[dict | None, dict | None]:
    ref = manifest.get("input_authority")
    required = (manifest.get("utility_contract_id") ==
                P12_DENSITY_RELIEF_INTERFERENCE_NONREGRESSION_V1_ID)
    if ref is None:
        if required:
            raise P12OrfsRunError(
                "R3-8 physical-harm contract requires input_authority")
        return None, None
    if not isinstance(ref, Mapping):
        raise P12OrfsRunError("input_authority reference must be an object")
    path = Path(_text(ref.get("path"), "input_authority.path")).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.is_file() or _sha256(path) != _digest_pin(
            ref.get("sha256"), "input_authority.sha256"):
        raise P12OrfsRunError("input_authority file digest mismatch")
    authority = _load_json(path, "input authority")
    supplied = authority.get("authority_digest")
    unsigned = dict(authority)
    unsigned.pop("authority_digest", None)
    if (supplied != _digest(unsigned) or supplied !=
            _digest_pin(ref.get("authority_digest"),
                        "input_authority.authority_digest")):
        raise P12OrfsRunError("input_authority content digest mismatch")
    if authority.get("version") != _SOURCE_BOUND_AUTHORITY_VERSION:
        raise P12OrfsRunError("input_authority version mismatch")
    authority_cases = authority.get("cases")
    if (authority.get("campaign_id") != manifest.get("campaign_id") or
            not isinstance(authority_cases, Mapping) or
            set(authority_cases) != case_ids):
        raise P12OrfsRunError("input_authority campaign/case coverage mismatch")
    if (authority.get("utility_contract_id") !=
            manifest.get("utility_contract_id") or
            authority.get("utility_contract_digest") !=
            manifest.get("utility_contract_digest")):
        raise P12OrfsRunError("input_authority utility contract mismatch")
    if any(authority.get(field) is not True for field in (
            "actual_router_used", "actual_selector_used",
            "actual_runtime_binding_used", "actual_candidate_builder_used")):
        raise P12OrfsRunError("input_authority actual generation chain is incomplete")
    if (authority.get("source_disjoint_scope") !=
            "verilog_content_sha256" or
            authority.get("eda_executed") is not False or
            authority.get("evaluation_only") is not True or
            authority.get("canonical_memory_mutation") != "none" or
            authority.get("production_runtime_imported") is not False or
            authority.get("memory_docs_submitted") is not False):
        raise P12OrfsRunError("input_authority boundary is invalid")
    preregistration = authority.get("preregistration")
    if (isinstance(preregistration, Mapping) and
            preregistration.get("version") == _PREREGISTRATION_V2):
        binding = authority.get("oracle_binding")
        if (not isinstance(binding, Mapping) or
                binding.get("version") != _ORACLE_BINDING_VERSION or
                binding.get("oracle_digest") != manifest.get("oracle_digest")):
            raise P12OrfsRunError("input_authority oracle binding is invalid")
        files = binding.get("files")
        if not isinstance(files, list) or not files:
            raise P12OrfsRunError("input_authority oracle files are missing")
        unsigned = dict(binding)
        unsigned.pop("oracle_digest", None)
        if _digest(unsigned) != binding["oracle_digest"]:
            raise P12OrfsRunError("input_authority oracle binding digest mismatch")
        for item in files:
            if not isinstance(item, Mapping):
                raise P12OrfsRunError("input_authority oracle file is malformed")
            oracle_path = Path(_text(item.get("path"), "oracle_binding.path")).resolve()
            if not oracle_path.is_file() or _sha256(oracle_path) != item.get("sha256"):
                raise P12OrfsRunError("input_authority oracle source drift")
    return authority, {"path": str(path), "sha256": _sha256(path),
                       "authority_digest": supplied}


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P12OrfsRunError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P12OrfsRunError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P12OrfsRunError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_pin(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise P12OrfsRunError(f"{name} must be a sha256 digest")
    return value


def _manifest(path: Path) -> tuple[dict, list[dict], int, int]:
    payload = _load_json(path, "P12 manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise P12OrfsRunError("P12 manifest version mismatch")
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)) or not raw_cases:
        raise P12OrfsRunError("P12 manifest cases must be a non-empty sequence")
    budget = payload.get("candidate_budget", 3)
    if type(budget) is not int or not 1 <= budget <= 3:
        raise P12OrfsRunError("P12 manifest candidate_budget must be between one and three")
    min_lineages = payload.get("min_lineages", 2)
    if type(min_lineages) is not int or min_lineages < 1:
        raise P12OrfsRunError("P12 manifest min_lineages must be positive")
    platform_digest = _digest_pin(payload.get("platform_digest"), "platform_digest")
    pdk_digest = _digest_pin(payload.get("pdk_digest"), "pdk_digest")
    toolchain_digest = payload.get("toolchain_digest")
    oracle_digest = payload.get("oracle_digest")
    if toolchain_digest is not None:
        toolchain_digest = _digest_pin(toolchain_digest, "toolchain_digest")
    if oracle_digest is not None:
        oracle_digest = _digest_pin(oracle_digest, "oracle_digest")
    utility_contract_id = payload.get("utility_contract_id")
    utility_contract_digest_pin = payload.get("utility_contract_digest")
    if ((utility_contract_id is None) !=
            (utility_contract_digest_pin is None)):
        raise P12OrfsRunError(
            "utility_contract_id and utility_contract_digest must be supplied together")
    if utility_contract_id is not None:
        utility_contract_id = _text(
            utility_contract_id, "utility_contract_id")
        catalog = known_utility_contracts()
        if utility_contract_id not in catalog:
            raise P12OrfsRunError("utility_contract_id is not registered")
        utility_contract_digest_pin = _digest_pin(
            utility_contract_digest_pin, "utility_contract_digest")
        actual = "sha256:" + utility_contract_digest(
            catalog[utility_contract_id]())
        if utility_contract_digest_pin != actual:
            raise P12OrfsRunError("utility contract digest mismatch")
        payload["utility_contract_id"] = utility_contract_id
        payload["utility_contract_digest"] = utility_contract_digest_pin
    cases: list[dict] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise P12OrfsRunError("each P12 case must be an object")
        case = dict(raw)
        case_id = _text(case.get("case_id"), "case_id")
        if case_id in seen:
            raise P12OrfsRunError("P12 case IDs must be unique")
        seen.add(case_id)
        for key in ("project_dir", "source_digest", "source_inputs", "platform_digest",
                    "pdk_digest", "toolchain_digest", "oracle_digest"):
            if key not in case:
                raise P12OrfsRunError(f"P12 case {case_id} is missing {key}")
        if _digest_pin(case["platform_digest"], f"{case_id}.platform_digest") != platform_digest:
            raise P12OrfsRunError(f"P12 case {case_id} platform digest drifts from manifest")
        if _digest_pin(case["pdk_digest"], f"{case_id}.pdk_digest") != pdk_digest:
            raise P12OrfsRunError(f"P12 case {case_id} PDK digest drifts from manifest")
        if toolchain_digest is not None and _digest_pin(
                case["toolchain_digest"], f"{case_id}.toolchain_digest") != toolchain_digest:
            raise P12OrfsRunError(f"P12 case {case_id} toolchain digest drifts from manifest")
        if oracle_digest is not None and _digest_pin(
                case["oracle_digest"], f"{case_id}.oracle_digest") != oracle_digest:
            raise P12OrfsRunError(f"P12 case {case_id} oracle digest drifts from manifest")
        case["case_id"] = case_id
        cases.append(case)
    # The digest is over the immutable manifest payload, not a self-referential
    # field.  It is recorded in the cohort receipt and report for replay.
    payload_digest = _digest(payload)
    payload["campaign_id"] = campaign_id
    payload["platform_digest"] = platform_digest
    payload["pdk_digest"] = pdk_digest
    return payload, cases, budget, min_lineages


def _candidate_map(case: Mapping, manifest_path: Path) -> tuple[dict, dict[str, dict]]:
    case_id = _text(case.get("case_id"), "case_id")
    raw = case.get("candidate_paths")
    if not isinstance(raw, Mapping) or set(raw) != set(P12_ARMS):
        raise P12OrfsRunError(
            f"P12 case {case_id} candidate_paths must cover exactly all four arms")
    candidates: dict[str, StructuredRepairCandidate | None] = {}
    refs: dict[str, dict] = {}
    for arm in P12_ARMS:
        value = raw[arm]
        if value is None:
            if arm == "ALWAYS_MEMORY":
                raise P12OrfsRunError(
                    f"P12 case {case_id} ALWAYS_MEMORY requires a candidate")
            candidates[arm] = None
            continue
        path = Path(_text(value, f"{case_id}.{arm}.candidate_path")).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise P12OrfsRunError(f"P12 candidate is not a file: {path}")
        try:
            candidate = StructuredRepairCandidate.from_dict(
                _load_json(path, f"{case_id}.{arm} candidate"))
        except (TypeError, ValueError) as exc:
            raise P12OrfsRunError(
                f"P12 candidate for {case_id}/{arm} is invalid: {exc}") from exc
        candidates[arm] = candidate
        refs[arm] = {"path": str(path), "sha256": _sha256(path),
                     "candidate_id": candidate.candidate_id,
                     "candidate_digest": candidate.candidate_digest}
    return candidates, refs


def _routing_map(path: Path, cases: Sequence[Mapping]) -> tuple[dict[str, MemoryRoutingDecision], dict]:
    """Load typed routing receipts and bind their identity into each case.

    Routing is deliberately an input evidence plane, not something inferred
    from an ORFS outcome.  Requiring exact case coverage and checking any
    manifest-declared metadata before execution prevents an expensive cohort
    from producing receipts that cannot be replayed at the P13 boundary.
    """
    payload = _load_json(path, "routing decisions")
    raw = payload.get("routes", payload.get("routing_decisions", payload))
    case_ids = {case["case_id"] for case in cases}
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P12OrfsRunError("routing decisions must cover exactly all P12 cases")
    result: dict[str, MemoryRoutingDecision] = {}
    by_id = {case["case_id"]: case for case in cases}
    for case_id in sorted(case_ids):
        try:
            decision = MemoryRoutingDecision.from_dict(raw[case_id])
        except (TypeError, ValueError) as exc:
            raise P12OrfsRunError(
                f"routing decision for {case_id} is invalid: {exc}") from exc
        case = by_id[case_id]
        for field in ("routing_decision", "routing_receipt_id", "no_skill_reason",
                      "state_shift_receipt_id", "risk_receipt_id",
                      "risk_receipt"):
            declared = case.get(field)
            actual = (decision.routing_receipt_id if field == "routing_receipt_id"
                      else decision.decision if field == "routing_decision"
                      else getattr(decision, field))
            if declared is not None and declared != actual:
                raise P12OrfsRunError(
                    f"P12 case {case_id} {field} disagrees with routing receipt")
            case[field] = actual
        result[case_id] = decision
    return result, {"path": str(path), "sha256": _sha256(path)}


def _verify_source_bound_bindings(authority: Mapping | None,
                                  authority_ref: Mapping | None,
                                  cases: Sequence[Mapping],
                                  candidate_refs: Mapping,
                                  routing: Mapping | None,
                                  routing_meta: Mapping | None) -> None:
    if authority is None:
        return
    case_ids = {case["case_id"] for case in cases}
    frozen_candidates = authority.get("candidate_freeze")
    authority_cases = authority.get("cases")
    if (not isinstance(frozen_candidates, Mapping) or
            set(frozen_candidates) != case_ids or
            not isinstance(authority_cases, Mapping) or
            set(authority_cases) != case_ids):
        raise P12OrfsRunError("input_authority candidate/case coverage mismatch")
    if routing is None or routing_meta is None:
        raise P12OrfsRunError(
            "source-bound input_authority requires routing_decisions")
    route_ref = authority.get("routing_decisions")
    if (not isinstance(route_ref, Mapping) or
            route_ref.get("path") != routing_meta.get("path") or
            route_ref.get("sha256") != routing_meta.get("sha256")):
        raise P12OrfsRunError("routing decisions drift from input_authority")
    by_id = {case["case_id"]: case for case in cases}
    for case_id in sorted(case_ids):
        frozen = frozen_candidates[case_id]
        audit = authority_cases[case_id]
        if not isinstance(frozen, Mapping) or not isinstance(audit, Mapping):
            raise P12OrfsRunError("input_authority case entry is malformed")
        refs = candidate_refs[case_id]
        if set(refs) != _MEMORY_ARMS:
            raise P12OrfsRunError(
                "source-bound campaign requires all three memory candidates")
        for arm in sorted(_MEMORY_ARMS):
            current = refs[arm]
            if any(current.get(field) != frozen.get(field) for field in (
                    "path", "sha256", "candidate_id", "candidate_digest")):
                raise P12OrfsRunError(
                    f"{case_id}/{arm} candidate drifts from input_authority")
        expected_route = {
            **routing[case_id].to_dict(),
            "decision_digest": routing[case_id].decision_digest,
        }
        if (audit.get("source_digest") != by_id[case_id].get("source_digest") or
                stable_dumps(audit.get("route")) !=
                stable_dumps(expected_route)):
            raise P12OrfsRunError(
                f"{case_id} source/route drifts from input_authority")
    if authority_ref is None:  # pragma: no cover - validated together above
        raise P12OrfsRunError("input_authority reference is unavailable")


def _routing_report(routing: Mapping | None) -> dict | None:
    if routing is None:
        return None
    return {
        case_id: {
            "decision": decision.decision,
            "decision_digest": decision.decision_digest,
            "routing_receipt_id": decision.routing_receipt_id,
            "no_skill_reason": decision.no_skill_reason,
            "state_shift_receipt_id": decision.state_shift_receipt_id,
            "risk_receipt_id": decision.risk_receipt_id,
            **({"risk_receipt": dict(decision.risk_receipt)}
               if decision.risk_receipt is not None else {}),
        }
        for case_id, decision in sorted(routing.items())
    }


def _terminal_report(*, output_path: Path, campaign_id: str,
                     manifest_path: Path, manifest_digest: str,
                     candidate_refs: Mapping, routing: Mapping | None,
                     routing_meta: Mapping | None,
                     input_authority_ref: Mapping | None,
                     utility_contract_id: str | None,
                     utility_contract_digest: str | None,
                     error: Exception) -> dict:
    failed = completed = None
    failed_case_id = None
    stage = "EXECUTION"
    status = "EXECUTION_FAILED"
    cause = error
    if isinstance(error, OrfsCohortExecutionError):
        status = "EXECUTION_POSTPROCESS_FAILED"
        failed_case_id, stage = error.case_id, error.stage
        failed_receipt = error.failed_case_receipt
        failed = {**failed_receipt.to_dict(),
                  "receipt_digest": failed_receipt.receipt_digest}
        completed = {
            case_id: {**receipt.to_dict(),
                      "receipt_digest": receipt.receipt_digest}
            for case_id, receipt in sorted(
                error.completed_case_receipts.items())
        }
        cause = error.__cause__ or error
    report = {
        "terminal_report_version": TERMINAL_REPORT_VERSION,
        "report_version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "status": status,
        "failed_stage": stage,
        "failed_case_id": failed_case_id,
        "error_type": type(cause).__name__,
        "error": str(cause),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": manifest_digest,
        "candidate_refs": dict(candidate_refs),
        "routing_decisions": _routing_report(routing),
        "routing_decisions_ref": (
            None if routing_meta is None else dict(routing_meta)),
        "input_authority_ref": (
            None if input_authority_ref is None else dict(input_authority_ref)),
        "utility_contract_id": utility_contract_id,
        "utility_contract_digest": utility_contract_digest,
        "failed_case_receipt": failed,
        "completed_case_receipts": completed,
        "cohort_receipt_available": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["terminal_report_digest"] = _digest(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def run_p12_orfs_cohort(manifest: Path | str, *, output: Path | str,
                        timeout: int | None = None,
                        routing_decisions: Path | str | None = None) -> dict:
    """Execute the exact four-arm P12 cohort described by ``manifest``."""
    manifest_path = Path(manifest).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path.exists():
        raise P12OrfsRunError(f"output already exists: {output_path}")
    payload, cases, budget, min_lineages = _manifest(manifest_path)
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    input_authority, input_authority_ref = _source_bound_authority(
        manifest_path, payload, {case["case_id"] for case in cases})
    if timeout is not None:
        if type(timeout) is not int or timeout < 1:
            raise P12OrfsRunError("timeout must be a positive integer")
    arm_candidates: dict[str, dict] = {}
    candidate_refs: dict[str, dict] = {}
    for case in cases:
        case_id = case["case_id"]
        candidates, refs = _candidate_map(case, manifest_path)
        arm_candidates[case_id] = candidates
        candidate_refs[case_id] = refs
    routing = None
    routing_meta = None
    if routing_decisions is not None:
        routing_path = Path(routing_decisions).expanduser().resolve()
        routing, routing_meta = _routing_map(routing_path, cases)
    _verify_source_bound_bindings(
        input_authority, input_authority_ref, cases, candidate_refs,
        routing, routing_meta)
    # ORFS scripts consume ORFS_TIMEOUT/ORFS_MAX_CPUS through their existing
    # environment contract.  This CLI deliberately does not reinterpret it.
    if timeout is not None:
        import os
        os.environ["ORFS_TIMEOUT"] = str(timeout)
    manifest_digest = _digest(payload)
    utility_contract = None
    if payload.get("utility_contract_id") is not None:
        utility_contract = known_utility_contracts()[
            payload["utility_contract_id"]]()
    try:
        cohort = execute_orfs_paired_cohort(
            cases, arm_candidates, campaign_id=campaign_id,
            campaign_manifest_digest=manifest_digest,
            platform_digest=payload["platform_digest"],
            pdk_digest=payload["pdk_digest"],
            oracle=OrfsCandidateOracle(), budget=budget,
            toolchain_digest=payload.get("toolchain_digest"),
            oracle_digest=payload.get("oracle_digest"),
            min_lineages=min_lineages,
            utility_contract=utility_contract)
    except (TypeError, ValueError, OSError) as exc:
        _terminal_report(
            output_path=output_path, campaign_id=campaign_id,
            manifest_path=manifest_path, manifest_digest=manifest_digest,
            candidate_refs=candidate_refs, routing=routing,
            routing_meta=routing_meta,
            input_authority_ref=input_authority_ref,
            utility_contract_id=payload.get("utility_contract_id"),
            utility_contract_digest=payload.get("utility_contract_digest"),
            error=exc)
        raise P12OrfsRunError(str(exc)) from exc
    receipt = {**cohort.to_dict(), "receipt_digest": cohort.receipt_digest}
    # Keep the canonical cohort receipt at the top level so this output can be
    # passed directly to ``build_p13_shadow_trigger_report.py``.  The nested
    # copy is retained as an explicit report boundary for callers that consume
    # runner metadata separately.
    report = {
        **receipt,
        "report_version": REPORT_VERSION,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": manifest_digest,
        "candidate_refs": candidate_refs,
        "routing_decisions": _routing_report(routing),
        "routing_decisions_ref": routing_meta,
        "input_authority_ref": input_authority_ref,
        "utility_contract_id": payload.get("utility_contract_id"),
        "utility_contract_digest": payload.get("utility_contract_digest"),
        "cohort_receipt": receipt,
        "cohort_receipt_digest": cohort.receipt_digest,
        "outcome_counts": cohort.outcome_counts,
        "lineage_count": cohort.lineage_count,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        # ``memory/docs/`` is a local governing input.  Keep the release
        # boundary explicit on the runner report as well as in the repository
        # ignore/release-freeze checks, so a P12 artifact cannot be mistaken
        # for a submitted design-document bundle.
        "memory_docs_submitted": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--routing-decisions", type=Path,
                        help="typed routing decisions covering every case")
    args = parser.parse_args(argv)
    try:
        report = run_p12_orfs_cohort(args.manifest, output=args.output,
                                     timeout=args.timeout,
                                     routing_decisions=args.routing_decisions)
    except (OSError, P12OrfsRunError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "cohort_receipt_digest": report["cohort_receipt_digest"],
        "lineage_count": report["lineage_count"],
        "outcome_counts": report["outcome_counts"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
