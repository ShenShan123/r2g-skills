#!/usr/bin/env python3
"""Rebind preregistered state-shift evidence to an executed P12 cohort.

The preregistration audit and its state-shift/routing receipts are frozen before
execution.  A later P12 execution necessarily has a different campaign ID, so
this command replays those typed receipts through the state-shift detector and
binds the resulting derivations to the exact cohort receipt.  It never derives
a reason from P12 outcomes and grants no mutation or production authority.
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
from tehm.evaluation import OrfsPairedCohortReceipt  # noqa: E402
from tehm.evolution import (  # noqa: E402
    EvolutionReasonDerivationReceipt,
    derive_state_shift_reason,
    p13_reason_receipt_from_derivations,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.state.shift_receipts import StateShiftReceipt  # noqa: E402


BUNDLE_VERSION = "p13-typed-reason-bundle-v1"


class P13StateShiftReasonBundleError(ValueError):
    """The preregistration audit cannot be safely rebound to the cohort."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(payload: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(payload)).encode()).hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13StateShiftReasonBundleError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13StateShiftReasonBundleError(f"{name} must be a JSON object")
    return payload


def _preregistered_cases(payload: Mapping) -> dict[str, Mapping]:
    if payload.get("execution_started") is not False:
        raise P13StateShiftReasonBundleError(
            "state-shift audit must prove execution_started=false")
    if payload.get("promotion_attempted") is not False:
        raise P13StateShiftReasonBundleError(
            "state-shift audit must prove promotion_attempted=false")
    if payload.get("production_database_writes") is not False:
        raise P13StateShiftReasonBundleError(
            "state-shift audit must prove production_database_writes=false")
    raw = payload.get("cases")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise P13StateShiftReasonBundleError(
            "state-shift audit cases must be a non-empty sequence")
    cases: dict[str, Mapping] = {}
    for item in raw:
        if not isinstance(item, Mapping) or type(item.get("case_id")) is not str:
            raise P13StateShiftReasonBundleError(
                "state-shift audit case requires case_id")
        case_id = item["case_id"].strip()
        if not case_id or case_id in cases:
            raise P13StateShiftReasonBundleError(
                "state-shift audit case IDs must be unique and non-empty")
        if item.get("admitted_to_state_shift_bucket") is not True:
            raise P13StateShiftReasonBundleError(
                f"state-shift audit case {case_id} was not preregistered")
        cases[case_id] = item
    return cases


def build_p13_state_shift_reason_bundle(
        cohort: Path | str, preregistration_audit: Path | str, *,
        output: Path | str) -> dict:
    """Replay preregistered typed evidence and write a cohort-bound bundle."""
    cohort_path = Path(cohort).expanduser().resolve()
    audit_path = Path(preregistration_audit).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path in {cohort_path, audit_path}:
        raise P13StateShiftReasonBundleError(
            "typed reason bundle output must be separate from its inputs")
    try:
        cohort_receipt = OrfsPairedCohortReceipt.from_dict(
            _load(cohort_path, "ORFS cohort receipt"))
    except (TypeError, ValueError) as exc:
        raise P13StateShiftReasonBundleError(
            "cohort is not a valid ORFS paired receipt") from exc
    audit = _load(audit_path, "state-shift preregistration audit")
    cases = _preregistered_cases(audit)
    if set(cases) != set(cohort_receipt.case_receipts):
        raise P13StateShiftReasonBundleError(
            "state-shift audit must cover exactly all cohort cases")

    derivations: dict[str, tuple[EvolutionReasonDerivationReceipt, ...]] = {}
    routes: dict[str, dict] = {}
    for case_id in sorted(cases):
        item = cases[case_id]
        paired = cohort_receipt.case_receipts[case_id]
        lineage_id = item.get("lineage_id")
        if lineage_id != paired.lineage_id:
            raise P13StateShiftReasonBundleError(
                f"state-shift audit lineage mismatch for {case_id}")
        try:
            shift = StateShiftReceipt.from_dict(item.get("state_shift"))
            route = MemoryRoutingDecision.from_dict(item.get("routing"))
            preregistered = EvolutionReasonDerivationReceipt.from_dict(
                item.get("evolution_reason"))
            derived = derive_state_shift_reason(
                shift, campaign_id=cohort_receipt.campaign_id, case_id=case_id,
                routing=route, lineage_id=lineage_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise P13StateShiftReasonBundleError(
                f"state-shift typed evidence is invalid for {case_id}") from exc
        if derived is None:
            raise P13StateShiftReasonBundleError(
                f"state-shift detector did not fire for {case_id}")
        # The preregistered derivation may use its preparation campaign ID, but
        # every detector input and all semantic fields must otherwise be exact.
        prior = preregistered.to_dict()
        current = derived.to_dict()
        prior.pop("campaign_id")
        current.pop("campaign_id")
        if prior != current:
            raise P13StateShiftReasonBundleError(
                f"state-shift derivation drifted after preregistration for {case_id}")
        if (paired.routing_receipt_id != route.routing_receipt_id or
                paired.routing_decision != route.decision or
                paired.no_skill_reason != route.no_skill_reason or
                paired.state_shift_receipt_id != shift.receipt_id):
            raise P13StateShiftReasonBundleError(
                f"state-shift cohort routing binding mismatch for {case_id}")
        derivations[case_id] = (derived,)
        routes[case_id] = {
            "routing_receipt_id": route.routing_receipt_id,
            "decision_digest": route.decision_digest,
        }

    try:
        reason = p13_reason_receipt_from_derivations(
            derivations, campaign_id=cohort_receipt.campaign_id,
            cohort_receipt_digest=cohort_receipt.receipt_digest)
    except (TypeError, ValueError) as exc:
        raise P13StateShiftReasonBundleError(
            "typed state-shift reason aggregation failed") from exc
    payload = {
        "version": BUNDLE_VERSION,
        "campaign_id": cohort_receipt.campaign_id,
        "cohort_receipt_digest": cohort_receipt.receipt_digest,
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_sha256": _sha256(cohort_path),
        "preregistration_audit": str(audit_path),
        "preregistration_audit_sha256": _sha256(audit_path),
        "preregistration_audit_digest": audit.get("audit_digest"),
        "reason_receipt": {
            **reason.to_dict(), "receipt_id": reason.receipt_id,
            "receipt_digest": reason.receipt_digest,
        },
        "derivation_receipts": {
            case_id: [{
                **entry.to_dict(), "receipt_id": entry.receipt_id,
                "receipt_digest": entry.receipt_digest,
            } for entry in entries]
            for case_id, entries in sorted(derivations.items())
        },
        "routing_bindings": routes,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    payload["bundle_digest"] = _digest(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--preregistration-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = build_p13_state_shift_reason_bundle(
            args.cohort, args.preregistration_audit, output=args.output)
    except (OSError, P13StateShiftReasonBundleError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": payload["campaign_id"],
        "cohort_receipt_digest": payload["cohort_receipt_digest"],
        "reason_receipt_digest": payload["reason_receipt"]["receipt_digest"],
        "bundle_digest": payload["bundle_digest"],
        "canonical_memory_mutation": payload["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
