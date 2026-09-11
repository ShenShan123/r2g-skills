#!/usr/bin/env python3
"""Audit whether the current real StateShift delta can enter Sprint R3-7.

This is a read-only, fail-closed bridge between the real R3-6 attribution and
the held-out/Delta-M experiment.  It follows the content-bound P14 -> P13 ->
support-expansion -> P12/anti-forgetting chain and distinguishes an absent
capability witness from a failed implementation.  It never constructs a
candidate, widens a support envelope, writes SQLite, or attempts promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evaluation.orfs_cohort import OrfsPairedCohortReceipt  # noqa: E402
from tehm.evolution import (  # noqa: E402
    AntiForgettingWitness, StateShiftSupportExpansionReceipt,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.state import StateShiftReceipt, SupportEnvelope  # noqa: E402


VERSION = "p14-state-shift-r3-7-eligibility-v1"
REQUIRED_REMAINING_GATES = (
    "C6_heldout_transfer",
    "C7_heldout_non_regression",
    "C8_delta_memory_ablation",
)


class R37EligibilityError(ValueError):
    """The upstream evidence chain is malformed or crosses a boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _read(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise R37EligibilityError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise R37EligibilityError(f"{name} must be an object")
    return payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _path_ref(ref: object, name: str) -> tuple[Path, dict]:
    if not isinstance(ref, dict):
        raise R37EligibilityError(f"{name} reference is missing")
    raw = ref.get("path")
    if type(raw) is not str or not raw.strip():
        raise R37EligibilityError(f"{name} path is missing")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise R37EligibilityError(f"{name} is not a file: {path}")
    if _sha256(path) != ref.get("sha256"):
        raise R37EligibilityError(f"{name} file digest mismatch")
    return path, ref


def _self_digest(payload: dict, field: str, name: str) -> str:
    supplied = payload.get(field)
    replay = dict(payload)
    replay.pop(field, None)
    if supplied != _digest(replay):
        raise R37EligibilityError(f"{name} {field} mismatch")
    return supplied


def _closed(payload: dict, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("production_integration") not in {
                None, "not_attempted"} or
            payload.get("promotion_attempted") not in {None, False} or
            payload.get("memory_docs_submitted") not in {None, False}):
        raise R37EligibilityError(f"{name} crosses the evaluation boundary")


def _derive_assessment(*, p14: dict, cohort: OrfsPairedCohortReceipt,
                       expansion: StateShiftSupportExpansionReceipt,
                       envelope: SupportEnvelope,
                       heldout: dict,
                       non_target_regression_free: bool) -> dict:
    """Derive readiness only from replayed evidence, never caller booleans."""
    case_ids = set(cohort.case_receipts)
    if case_ids != set(expansion.case_lineages):
        raise R37EligibilityError(
            "P12 cohort cases differ from the support-expansion cases")
    if cohort.receipt_digest != expansion.cohort_receipt_digest:
        raise R37EligibilityError(
            "P12 cohort digest does not bind the support expansion")

    baseline_outcomes = {
        case_id: receipt.arm_receipts["NO_MEMORY"].outcome
        for case_id, receipt in sorted(cohort.case_receipts.items())
    }
    target_baseline_has_failure = any(
        outcome in {"FAIL", "PARTIAL"} for outcome in baseline_outcomes.values())
    heldout_row = heldout.get("heldout")
    if not isinstance(heldout_row, dict):
        raise R37EligibilityError("held-out audit payload is missing")
    shift = StateShiftReceipt.from_dict(heldout_row.get("state_shift"))
    heldout_lineage = heldout_row.get("lineage_id")
    if type(heldout_lineage) is not str or not heldout_lineage:
        raise R37EligibilityError("held-out lineage is missing")
    source_disjoint = heldout_lineage not in set(expansion.case_lineages.values())
    heldout_in_support = (
        shift.transferable and
        shift.support_envelope_digest == envelope.envelope_digest and
        heldout_row.get("routing_decision") in {"CONSIDER", "APPLY"}
    )
    heldout_executable = (
        heldout_row.get("memory_action_executed") is True and
        heldout_row.get("repair_success_claimed") is True
    )

    upstream = (
        p14.get("r3_6_complete") is True and
        all(p14.get("r3_6_gates", {}).values()) and
        tuple(p14.get("remaining_gates", ())) == REQUIRED_REMAINING_GATES and
        p14.get("standard_capability_claim_promotable") is False
    )
    environment_frozen = all((
        bool(cohort.toolchain_digest), bool(cohort.oracle_digest),
        bool(cohort.platform_digest), bool(cohort.pdk_digest),
        1 <= cohort.candidate_budget <= 3,
    ))
    gates = {
        "F1_upstream_r3_6_exact_chain": upstream,
        "F2_execution_environment_frozen": environment_frozen,
        "F3_current_delta_has_target_gain_opportunity":
            target_baseline_has_failure,
        "F4_source_disjoint_heldout_is_inside_verified_support":
            source_disjoint and heldout_in_support,
        "F5_executable_heldout_delta_m_triplet_available":
            source_disjoint and heldout_in_support and heldout_executable,
        "F6_existing_non_target_witness_is_regression_free":
            non_target_regression_free,
    }
    blockers = []
    if not target_baseline_has_failure:
        blockers.append("current_target_M_t_already_passes")
    if source_disjoint and not heldout_in_support:
        blockers.append("source_disjoint_heldout_is_outside_support_envelope")
    if not heldout_executable:
        blockers.append("heldout_is_firewall_audit_not_executable_transfer")
    if not (source_disjoint and heldout_in_support and heldout_executable):
        blockers.append("delta_m_triplet_not_available")

    ready = all(gates.values())
    return {
        "status": "READY" if ready else "NOT_ESTABLISHED",
        "r3_7_execution_authorized": ready,
        "gates": gates,
        "blockers": sorted(set(blockers)),
        "target_baseline_outcomes": baseline_outcomes,
        "heldout": {
            "case_id": heldout_row.get("case_id"),
            "lineage_id": heldout_lineage,
            "source_disjoint": source_disjoint,
            "support_envelope_digest": shift.support_envelope_digest,
            "transferable": shift.transferable,
            "routing_decision": heldout_row.get("routing_decision"),
            "no_skill_reason": heldout_row.get("no_skill_reason"),
            "memory_action_executed": heldout_row.get(
                "memory_action_executed"),
            "repair_success_claimed": heldout_row.get(
                "repair_success_claimed"),
        },
        "required_recovery": (
            [] if ready else [
                "derive a structural generalization from preregistered "
                "learner-eligible evidence",
                "create a new P13 shadow delta whose SupportEnvelope includes "
                "a source-disjoint held-out family without using held-out data",
                "execute M_t, M_t+1, and M_t+1-DeltaM through the real router, "
                "selector, binding, candidate, and oracle chain",
            ]
        ),
        "recommended_next_stage": (
            "R3_7_HELDOUT_DELTA_M" if ready else
            "R3_8_MEMORY_INTERFERENCE"
        ),
    }


def audit_r3_7_eligibility(p14_report: Path | str, *, artifacts: Path | str) -> dict:
    p14_path = Path(p14_report).expanduser().resolve()
    output = Path(artifacts).expanduser().resolve()
    if output.exists():
        raise R37EligibilityError("R3-7 audit directory must not already exist")

    p14 = _read(p14_path, "P14 report")
    _self_digest(p14, "report_digest", "P14 report")
    _closed(p14, "P14 report")

    p13_path, p13_ref = _path_ref(
        p14.get("p13_shadow_update_report"), "P13 shadow update report")
    p13 = _read(p13_path, "P13 shadow update report")
    if (_self_digest(p13, "report_digest", "P13 shadow update report") !=
            p13_ref.get("report_digest")):
        raise R37EligibilityError("P13 report content binding mismatch")
    _closed(p13, "P13 shadow update report")

    expansion_path, expansion_ref = _path_ref(
        p13.get("support_expansion_report"), "support expansion report")
    expansion_report = _read(expansion_path, "support expansion report")
    if (_self_digest(
            expansion_report, "report_digest", "support expansion report") !=
            expansion_ref.get("report_digest")):
        raise R37EligibilityError("support expansion content binding mismatch")
    _closed(expansion_report, "support expansion report")
    expansion = StateShiftSupportExpansionReceipt.from_dict(
        expansion_report.get("support_expansion_receipt"))
    envelope = SupportEnvelope.from_dict(
        expansion_report.get("child_support_envelope"))
    if (expansion.receipt_digest != expansion_ref.get("receipt_digest") or
            envelope.envelope_digest !=
            expansion.child_support_envelope_digest):
        raise R37EligibilityError("typed support expansion binding mismatch")

    cohort_path, cohort_ref = _path_ref(
        expansion_report.get("p12_cohort_report"), "P12 cohort report")
    cohort_report = _read(cohort_path, "P12 cohort report")
    cohort = OrfsPairedCohortReceipt.from_dict(
        cohort_report.get("cohort_receipt"))
    if (cohort.receipt_digest != cohort_ref.get("cohort_receipt_digest") or
            cohort.receipt_digest != cohort_report.get("receipt_digest")):
        raise R37EligibilityError("typed P12 cohort binding mismatch")
    _closed(cohort_report, "P12 cohort report")

    anti_path, anti_ref = _path_ref(
        p13.get("anti_forgetting_report"), "anti-forgetting report")
    anti = _read(anti_path, "anti-forgetting report")
    # This typed witness predates the top-level ``evaluation_only`` field, so
    # bind its immutable authority fields explicitly rather than weakening
    # ``_closed`` for newer reports.
    if (anti.get("canonical_memory_mutation") != "none" or
            anti.get("production_runtime_imported") is not False or
            anti.get("production_integration") != "not_attempted" or
            anti.get("memory_docs_submitted") is not False or
            anti.get("eligible") is not True):
        raise R37EligibilityError(
            "anti-forgetting report crosses the evaluation boundary")
    evidence = anti.get("evidence")
    witness = AntiForgettingWitness.from_dict(anti.get("witness"))
    if not isinstance(evidence, dict):
        raise R37EligibilityError("anti-forgetting evidence is incomplete")
    if witness.receipt_digest != anti_ref.get("witness_digest"):
        raise R37EligibilityError("anti-forgetting witness digest mismatch")
    heldout_path, _ = _path_ref(
        evidence.get("heldout_audit"), "held-out firewall audit")
    heldout = _read(heldout_path, "held-out firewall audit")
    _self_digest(heldout, "report_digest", "held-out firewall audit")
    _closed(heldout, "held-out firewall audit")
    if heldout.get("passed") is not True:
        raise R37EligibilityError("held-out firewall audit did not pass")

    assessment = _derive_assessment(
        p14=p14, cohort=cohort, expansion=expansion, envelope=envelope,
        heldout=heldout,
        non_target_regression_free=witness.non_target_regression_free,
    )
    report = {
        "version": VERSION,
        "campaign_id": p14.get("campaign_id"),
        "inputs": {
            "p14_report": {"path": str(p14_path), "sha256": _sha256(p14_path),
                           "report_digest": p14["report_digest"]},
            "p13_report": {"path": str(p13_path), "sha256": _sha256(p13_path),
                           "report_digest": p13["report_digest"]},
            "support_expansion": {
                "path": str(expansion_path), "sha256": _sha256(expansion_path),
                "receipt_digest": expansion.receipt_digest,
                "support_envelope_digest": envelope.envelope_digest,
            },
            "p12_cohort": {"path": str(cohort_path), "sha256": _sha256(cohort_path),
                           "receipt_digest": cohort.receipt_digest},
            "anti_forgetting": {"path": str(anti_path), "sha256": _sha256(anti_path),
                                "witness_digest": witness.receipt_digest},
            "heldout_firewall_audit": {
                "path": str(heldout_path), "sha256": _sha256(heldout_path),
                "report_digest": heldout["report_digest"]},
        },
        "assessment": assessment,
        "claim_boundary": (
            "The current delta proves selection evolution on already-passing exact "
            "training contexts. The disjoint GCD observation proves the firewall, "
            "not held-out transfer. Earlier direct-constructed Icarus candidates "
            "are not reused as evidence for this ORFS memory delta."
        ),
        "legacy_evidence_reused": False,
        "promotion_attempted": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    output.mkdir(parents=True)
    _write(output / "r3-7-eligibility-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--p14-report", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_r3_7_eligibility(
            args.p14_report, artifacts=args.artifacts)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "artifacts": str(args.artifacts.expanduser().resolve()),
        "status": report["assessment"]["status"],
        "blockers": report["assessment"]["blockers"],
        "recommended_next_stage": report["assessment"][
            "recommended_next_stage"],
        "report_digest": report["report_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
