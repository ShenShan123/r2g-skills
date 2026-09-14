#!/usr/bin/env python3
"""Build an external-ORFS P15 calibration slice from typed paired receipts.

The router prediction is replayed from the frozen pre-interference ORFS route
(``CONSIDER``), while the oracle label is derived independently from the
complete ``NO_MEMORY``/``ALWAYS_MEMORY`` execution pair.  This deliberately
does not turn an ``INAPPLICABLE`` post-revision safety veto into a binary
calibration prediction; that route is recorded as outside the P15 binary
contract.  The output is evaluation-only and never updates memory or policy.
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

from scripts import run_r3_orfs_interference_shadow as shadow  # noqa: E402
from scripts.build_no_skill_calibration_report import (  # noqa: E402
    MANIFEST_VERSION, build_no_skill_calibration_report,
)
from tehm.evaluation.no_skill_calibration import (  # noqa: E402
    derive_no_skill_oracle_label,
)
from tehm.ids import stable_dumps  # noqa: E402


VERSION = "tehm-r3-orfs-p15-calibration-v0.1"
DEFAULT_CHALLENGE = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-challenge-20260903"
)


class OrfsP15CalibrationError(ValueError):
    """The external ORFS calibration slice is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _strata(case: dict) -> dict[str, str]:
    project = Path(case["project_dir"])
    # The project path is only a descriptive stratum; no path is used to
    # derive the expected label.  The execution oracle remains the authority.
    design = project.name.removeprefix("sky130hs_").removesuffix("_0")
    return {
        "mechanism_family": "ORFS_DENSITY_RELIEF",
        "design": design or "external_orfs",
        "platform": str(case.get("platform") or "unknown"),
        "flow_regime": "orfs_route_real",
        "model_identity": "typed-paired-oracle-v1",
        "state_shift_dimension": "none",
    }


def _prospective_calibration_partition(challenge_root, cases_payload, cohort):
    """Execution-bound membership, not a post-outcome split argument."""
    manifest_path = challenge_root / "receipts" / "campaign_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OrfsP15CalibrationError("prospective calibration manifest is missing") from exc
    if (not isinstance(manifest, dict) or _digest(manifest) != cohort.campaign_manifest_digest or
            manifest.get("campaign_id") != cohort.campaign_id or
            manifest.get("lane") != "CALIBRATION" or manifest.get("learner_eligible") is not False):
        raise OrfsP15CalibrationError("execution-bound prospective calibration partition is not established")
    frozen = manifest.get("cases")
    cases = cases_payload.get("cases")
    if (not isinstance(frozen, list) or not frozen or cases != frozen or
            len({case.get("case_id") for case in frozen if isinstance(case, dict)}) != len(frozen) or
            {case.get("case_id") for case in frozen} != set(cohort.case_receipts)):
        raise OrfsP15CalibrationError("calibration case membership differs from the executed manifest")
    for case in frozen:
        cid = case["case_id"]
        if (case.get("dataset_split") != "calibration" or case.get("role") != "calibration" or
                case.get("learner_eligible") is not False or
                case.get("lineage_id") != cohort.lineage_ids[cid]):
            raise OrfsP15CalibrationError("training, validation, or held-out evidence cannot be relabeled calibration")
    return {"path": str(manifest_path), "sha256": _file_digest(manifest_path),
            "campaign_manifest_digest": cohort.campaign_manifest_digest,
            "learner_eligible": False, "split": "calibration", "membership_execution_bound": True}


def run(*, challenge_artifacts: Path | str = DEFAULT_CHALLENGE,
        artifacts: Path | str, force: bool = False,
        minimum_sample_count: int = 2,
        minimum_reason_cases: int = 1) -> dict:
    if type(minimum_sample_count) is not int or minimum_sample_count < 1:
        raise OrfsP15CalibrationError("minimum_sample_count must be positive")
    if type(minimum_reason_cases) is not int or minimum_reason_cases < 1:
        raise OrfsP15CalibrationError("minimum_reason_cases must be positive")
    challenge_root = Path(challenge_artifacts).expanduser().resolve()
    output = Path(artifacts).expanduser().resolve()
    if output.exists():
        raise OrfsP15CalibrationError(f"output exists; calibration evidence is immutable; use a new directory: {output}")
    if force:
        raise OrfsP15CalibrationError("--force replacement is disabled for immutable calibration evidence")
    if challenge_root.is_relative_to(output):
        raise OrfsP15CalibrationError("calibration output cannot contain its source evidence")

    (cases_payload, cohort, routes, _candidates, _derivations, _triggers,
     _admissions, reason) = shadow._load_challenge(challenge_root)
    partition = _prospective_calibration_partition(challenge_root, cases_payload, cohort)
    if len(cohort.case_receipts) < 2 or cohort.lineage_count < 2:
        raise OrfsP15CalibrationError(
            "ORFS calibration requires at least two source-disjoint lineages")
    if set(routes) != set(cohort.case_receipts):
        raise OrfsP15CalibrationError("routing receipts do not cover the ORFS cohort")
    if any(route.decision not in {"APPLY", "CONSIDER", "NO_SKILL"}
           for route in routes.values()):
        raise OrfsP15CalibrationError(
            "pre-interference calibration route is outside binary P15 contract")

    output.mkdir(parents=True)
    receipts_root = output / "receipts"
    receipts_root.mkdir()

    paired_index: dict[str, dict] = {}
    routing_decisions: dict[str, dict] = {}
    oracle_labels: dict[str, dict] = {}
    derivations: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    case_by_id = {case["case_id"]: case for case in cases_payload["cases"]}
    for case_id in sorted(cohort.case_receipts):
        route = routes[case_id]
        paired = cohort.case_receipts[case_id]
        label = derive_no_skill_oracle_label(
            paired, strata=_strata(case_by_id[case_id]),
            split="calibration")
        derivations[case_id] = label["derivation"]
        if not label["classifiable"]:
            excluded[case_id] = label["unclassifiable_reason"]
            continue
        paired_index[case_id] = {
            "routing_receipt_id": paired.routing_receipt_id,
        }
        routing_decisions[case_id] = {
            **route.to_dict(),
            "routing_receipt_id": route.routing_receipt_id,
            "decision_digest": route.decision_digest,
        }
        oracle_labels[case_id] = {
            "expected_decision": label["expected_decision"],
            "expected_reason": label["expected_reason"],
            "confidence": label["confidence"],
            "strata": label["strata"],
        }

    cohort_path = challenge_root / "receipts" / "cohort.json"
    cases_path = challenge_root / "receipts" / "cases.json"
    reason_path = challenge_root / "receipts" / "p13_reason_receipt.json"
    for path in (cohort_path, cases_path, reason_path):
        if not path.is_file():
            raise OrfsP15CalibrationError(f"calibration evidence is missing: {path}")
    derivation_path = receipts_root / "oracle_label_derivations.json"
    _write_json(derivation_path, {
        "version": "no-skill-oracle-label-derivations-v2",
        "campaign_id": cohort.campaign_id,
        "split": "calibration",
        "derivations": derivations,
        "excluded_cases": excluded,
        "source_reason_receipt_digest": reason.receipt_digest,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_authority_changed": False,
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    })
    if not oracle_labels:
        raise OrfsP15CalibrationError(
            "no classifiable oracle samples; all derivations retained; calibration NOT_ESTABLISHED")
    manifest = {
        "version": MANIFEST_VERSION,
        "campaign_id": f"{cohort.campaign_id}:p15-orfs",
        "split": "calibration",
        "oracle_label_source": "typed-paired-orfs-oracle-v2",
        "paired_routing_index": {"case_receipts": paired_index},
        "routing_decisions": routing_decisions,
        "oracle_labels": oracle_labels,
        "evidence_refs": [
            {"id": "prospective-calibration-partition", "path": partition["path"],
             "sha256": partition["sha256"]},
            {"id": "orfs-cohort", "path": str(cohort_path),
             "sha256": _file_digest(cohort_path)},
            {"id": "orfs-cases", "path": str(cases_path),
             "sha256": _file_digest(cases_path)},
            {"id": "orfs-reason", "path": str(reason_path),
             "sha256": _file_digest(reason_path)},
            {"id": "oracle-label-derivations", "path": str(derivation_path),
             "sha256": _file_digest(derivation_path)},
        ],
    }
    manifest_path = receipts_root / "calibration_manifest.json"
    _write_json(manifest_path, manifest)
    report_path = receipts_root / "calibration_report.json"
    report = build_no_skill_calibration_report(
        manifest_path, output=report_path,
        minimum_sample_count=minimum_sample_count,
        minimum_reason_cases=minimum_reason_cases,
        calibration_bins=10)
    receipt = report["receipt"]
    summary = {
        "version": VERSION,
        "campaign_id": manifest["campaign_id"],
        "split": "calibration",
        "backend": "external_orfs",
        "source_cohort": str(challenge_root),
        "prospective_calibration_partition": partition,
        "source_disjoint_lineages": cohort.lineage_ids,
        "sample_count": len(oracle_labels),
        "executed_case_count": len(cohort.case_receipts),
        "excluded_cases": excluded,
        "router_confidence_policy": "ABSENT_NOT_IMPUTED",
        "derived_oracle_decisions": {
            case_id: value["expected_decision"]
            for case_id, value in sorted(oracle_labels.items())
        },
        "derived_oracle_reasons": {
            case_id: value["expected_reason"]
            for case_id, value in sorted(oracle_labels.items())
        },
        "router_predictions": {
            case_id: routes[case_id].decision
            for case_id in sorted(routes)
        },
        "calibration_report": str(report_path),
        "calibration_receipt": receipt,
        "interpretation": (
            "Only pairs establishing safe repair or forced-memory harm are binary "
            "samples. Neutral and unsuccessful pairs do not establish NO_MATCH. "
            "No router probabilities are imputed. Post-revision INAPPLICABLE "
            "vetoes remain outside the binary P15 contract."),
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_authority_changed": False,
        "production_runtime_imported": False,
        "production_promotion_eligible": False,
        "memory_docs_submitted": False,
    }
    _write_json(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--challenge-artifacts", type=Path, default=DEFAULT_CHALLENGE)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--minimum-sample-count", type=int, default=2)
    parser.add_argument("--minimum-reason-cases", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(challenge_artifacts=args.challenge_artifacts,
                      artifacts=args.artifacts, force=args.force,
                      minimum_sample_count=args.minimum_sample_count,
                      minimum_reason_cases=args.minimum_reason_cases)
    except (OSError, TypeError, ValueError, OrfsP15CalibrationError) as exc:
        print(f"ORFS P15 calibration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["calibration_receipt"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
