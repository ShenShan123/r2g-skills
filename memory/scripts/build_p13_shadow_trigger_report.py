#!/usr/bin/env python3
"""Replay a typed P12 cohort into the P13 shadow-trigger boundary.

This is an evidence assembler, not a learner.  It loads a frozen RTL/ORFS
cohort, an explicit learner-partition manifest, and (optionally) typed routing
and evolution-reason receipts.  It never derives a reason from an execution
outcome and never writes canonical memory or imports production runtime state.
Missing routes, missing reasons, and audit-only cases become explicit
non-trigger receipts rather than being silently promoted.
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
from tehm.evaluation import (  # noqa: E402
    OrfsPairedCohortReceipt,
    RtlPairedCohortReceipt,
)
from tehm.evolution import (  # noqa: E402
    P13EvolutionReasonReceipt,
    P12ShadowTriggerError,
    build_p12_shadow_update_triggers,
)
from tehm.ids import stable_dumps  # noqa: E402


REPORT_VERSION = "p13-shadow-trigger-report-v1"
EVOLUTION_REASON_RECEIPT_VERSION = "p13-evolution-reason-receipt-v1"


class P13ShadowTriggerReportError(ValueError):
    """Input evidence cannot safely be replayed at the P13 boundary."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13ShadowTriggerReportError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13ShadowTriggerReportError(f"{name} must be a JSON object")
    return payload


def _cohort(path: Path):
    payload = _load_json(path, "cohort receipt")
    errors: list[str] = []
    for cls, label in ((OrfsPairedCohortReceipt, "ORFS"),
                       (RtlPairedCohortReceipt, "RTL")):
        try:
            return label, cls.from_dict(payload)
        except (TypeError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    raise P13ShadowTriggerReportError(
        "cohort receipt is neither a valid ORFS nor RTL receipt (" +
        "; ".join(errors) + ")")


def _manifest_partition(manifest: Mapping, case_ids: set[str], campaign_id: str) -> tuple[bool, dict[str, bool]]:
    if manifest.get("campaign_id") != campaign_id:
        raise P13ShadowTriggerReportError("manifest campaign_id does not match cohort")
    campaign_eligible = manifest.get("learner_eligible")
    if type(campaign_eligible) is not bool:
        raise P13ShadowTriggerReportError("manifest learner_eligible must be boolean")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)) or not raw_cases:
        raise P13ShadowTriggerReportError("manifest cases must be a non-empty sequence")
    result: dict[str, bool] = {}
    for item in raw_cases:
        if not isinstance(item, Mapping) or type(item.get("case_id")) is not str:
            raise P13ShadowTriggerReportError("manifest cases require case_id")
        case_id = item["case_id"].strip()
        if not case_id or case_id in result:
            raise P13ShadowTriggerReportError("manifest case IDs must be unique and non-empty")
        explicit = item.get("learner_eligible")
        if explicit is None:
            split = item.get("dataset_split")
            role = item.get("role")
            if type(split) is not str or type(role) is not str:
                raise P13ShadowTriggerReportError(
                    f"manifest case {case_id} lacks explicit learner partition")
            # The training role/split is an explicit manifest assertion; it is
            # not inferred from any P12 outcome or oracle field.
            explicit = split == "training" and role == "training"
        elif type(explicit) is not bool:
            raise P13ShadowTriggerReportError(
                f"manifest learner_eligible for {case_id} must be boolean")
        result[case_id] = bool(explicit)
    if set(result) != case_ids:
        raise P13ShadowTriggerReportError(
            "manifest learner partition must cover exactly all cohort cases")
    return campaign_eligible, result


def _routes(payload: Mapping, case_ids: set[str]) -> dict[str, MemoryRoutingDecision]:
    raw = payload.get("routes", payload.get("routing_decisions", payload))
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P13ShadowTriggerReportError(
            "routing decisions must cover exactly all cohort cases")
    result: dict[str, MemoryRoutingDecision] = {}
    for case_id in sorted(case_ids):
        try:
            result[case_id] = MemoryRoutingDecision.from_dict(raw[case_id])
        except (TypeError, ValueError) as exc:
            raise P13ShadowTriggerReportError(
                f"routing decision for {case_id} is invalid: {exc}") from exc
    return result


def _evidence_refs(
        payload: Mapping, reason_path: Path,
        *, forbidden_paths: tuple[Path, ...] = ()) -> tuple[dict, ...]:
    refs = payload.get("evidence_refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
        raise P13ShadowTriggerReportError(
            "evolution reason receipt requires non-empty evidence_refs")
    result: list[dict] = []
    seen: set[str] = set()
    for item in refs:
        if not isinstance(item, Mapping):
            raise P13ShadowTriggerReportError(
                "each evolution reason evidence_ref must be an object")
        raw_path = item.get("path") or item.get("file")
        if type(raw_path) is not str or not raw_path.strip():
            raise P13ShadowTriggerReportError(
                "evolution reason evidence_ref requires path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = reason_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise P13ShadowTriggerReportError(
                f"evolution reason evidence is not a file: {path}")
        if path in forbidden_paths:
            raise P13ShadowTriggerReportError(
                "evolution reason evidence must be independent from cohort, "
                "manifest, routing, and reason inputs")
        digest = _sha256(path)
        expected = item.get("sha256") or item.get("digest")
        if expected is not None and expected != digest:
            raise P13ShadowTriggerReportError(
                f"evolution reason evidence digest mismatch: {path}")
        ref = dict(item)
        ref["path"] = str(path)
        ref["sha256"] = digest
        key = stable_dumps(ref)
        if key in seen:
            raise P13ShadowTriggerReportError(
                "evolution reason evidence_refs contain duplicates")
        seen.add(key)
        result.append(ref)
    return tuple(result)


def _reasons(payload: Mapping, case_ids: set[str], *, campaign_id: str,
             cohort_digest: str, reason_path: Path,
             forbidden_paths: tuple[Path, ...] = ()) -> tuple[dict[str, tuple[str, ...]], dict]:
    # Parse the supplied receipt first, so a caller-provided digest is checked
    # against exactly what was written.  Paths are then canonicalized and
    # hashed before producing the report-bound receipt below.
    try:
        supplied = P13EvolutionReasonReceipt.from_dict(payload)
    except (P12ShadowTriggerError, TypeError, ValueError) as exc:
        raise P13ShadowTriggerReportError(str(exc)) from exc
    if supplied.campaign_id != campaign_id:
        raise P13ShadowTriggerReportError(
            "evolution reason receipt campaign_id does not match cohort")
    if supplied.cohort_receipt_digest != cohort_digest:
        raise P13ShadowTriggerReportError(
            "evolution reason receipt cohort digest does not match cohort")
    if set(supplied.evolution_reasons) != case_ids:
        raise P13ShadowTriggerReportError(
            "evolution reasons must cover exactly all cohort cases")
    refs = _evidence_refs(payload, reason_path, forbidden_paths=forbidden_paths)
    normalized = P13EvolutionReasonReceipt(
        campaign_id=campaign_id, cohort_receipt_digest=cohort_digest,
        label_source=supplied.label_source, evidence_refs=refs,
        evolution_reasons=supplied.evolution_reasons).to_dict()
    bound = P13EvolutionReasonReceipt.from_dict(normalized)
    return dict(bound.evolution_reasons), {
                    "path": str(reason_path), "sha256": _sha256(reason_path),
                    "receipt_id": bound.receipt_id,
                    "receipt_digest": bound.receipt_digest,
                    "label_source": bound.label_source,
                    "evidence_refs": list(refs)}


def build_p13_shadow_trigger_report(
        cohort_path: Path | str, manifest_path: Path | str, *, output: Path | str,
        routing_path: Path | str | None = None,
        evolution_reasons_path: Path | str | None = None,
        memory_arm: str = "ALWAYS_MEMORY", min_lineages: int = 2) -> dict:
    """Build one fail-closed, replayable P12-to-P13 report."""
    cohort_path = Path(cohort_path).expanduser().resolve()
    manifest_path = Path(manifest_path).expanduser().resolve()
    if type(min_lineages) is not int or min_lineages < 1:
        raise P13ShadowTriggerReportError("min_lineages must be positive")
    cohort_kind, cohort = _cohort(cohort_path)
    manifest = _load_json(manifest_path, "campaign manifest")
    case_ids = set(cohort.case_receipts)
    learner_eligible, partition = _manifest_partition(
        manifest, case_ids, cohort.campaign_id)
    routes = None
    route_meta = None
    route_path = None
    if routing_path is not None:
        route_path = Path(routing_path).expanduser().resolve()
        route_payload = _load_json(route_path, "routing decisions")
        routes = _routes(route_payload, case_ids)
        route_meta = {"path": str(route_path), "sha256": _sha256(route_path)}
    reasons = None
    reason_meta = None
    reason_path = None
    if evolution_reasons_path is not None:
        reason_path = Path(evolution_reasons_path).expanduser().resolve()
        reason_payload = _load_json(reason_path, "evolution reasons")
        reasons, reason_meta = _reasons(
            reason_payload, case_ids, campaign_id=cohort.campaign_id,
            cohort_digest=cohort.receipt_digest, reason_path=reason_path,
            forbidden_paths=tuple(path for path in (
                cohort_path, manifest_path, route_path, reason_path)
                if path is not None))
    try:
        triggers = build_p12_shadow_update_triggers(
            cohort, memory_arm=memory_arm, learner_eligible=learner_eligible,
            min_lineages=min_lineages, routing_decisions=routes,
            case_learner_eligibility=partition, evolution_reasons=reasons)
    except (P12ShadowTriggerError, TypeError, ValueError) as exc:
        raise P13ShadowTriggerReportError(str(exc)) from exc
    blocked = sorted({item.reason for item in triggers if not item.triggered})
    triggered_count = sum(1 for item in triggers if item.triggered)
    report = {
        "version": REPORT_VERSION,
        "cohort_kind": cohort_kind,
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_sha256": _sha256(cohort_path),
        "cohort_receipt_digest": cohort.receipt_digest,
        "campaign_id": cohort.campaign_id,
        "campaign_manifest": str(manifest_path),
        "campaign_manifest_sha256": _sha256(manifest_path),
        "campaign_manifest_digest": manifest.get("campaign_manifest_digest"),
        "memory_arm": memory_arm,
        "min_lineages": min_lineages,
        "case_learner_eligibility": dict(sorted(partition.items())),
        "routing_decisions": route_meta,
        "evolution_reasons": reason_meta,
        "trigger_count": len(triggers),
        "triggered_count": triggered_count,
        "blocked_reasons": blocked,
        "triggers": [
            {**item.to_dict(), "receipt_digest": item.receipt_digest}
            for item in triggers
        ],
        "p13_eligible": bool(triggers) and triggered_count == len(triggers),
        "p13_block_reason": None if not blocked else ",".join(blocked),
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "shadow_update_policy": "isolated_staging_only",
    }
    report["report_digest"] = _digest(report)
    output_path = Path(output).expanduser().resolve()
    forbidden_outputs = {cohort_path, manifest_path}
    if route_path is not None:
        forbidden_outputs.add(route_path)
    if reason_path is not None:
        forbidden_outputs.add(reason_path)
    if output_path in forbidden_outputs:
        raise P13ShadowTriggerReportError(
            "P13 trigger report output must be separate from all evidence inputs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--routing-decisions", type=Path)
    parser.add_argument("--evolution-reasons", type=Path)
    parser.add_argument("--memory-arm", default="ALWAYS_MEMORY")
    parser.add_argument("--min-lineages", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_shadow_trigger_report(
            args.cohort, args.manifest, output=args.output,
            routing_path=args.routing_decisions,
            evolution_reasons_path=args.evolution_reasons,
            memory_arm=args.memory_arm, min_lineages=args.min_lineages)
    except (OSError, P13ShadowTriggerReportError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "p13_eligible": report["p13_eligible"],
        "trigger_count": report["trigger_count"],
        "triggered_count": report["triggered_count"],
        "blocked_reasons": report["blocked_reasons"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0 if report["p13_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
