#!/usr/bin/env python3
"""Bind an independently authored P13 evolution-label manifest to a cohort.

This command is a provenance binder, not a label generator.  The label
manifest must explicitly contain the Revision2 reason for every cohort case
and immutable evidence references.  Outcome/gold fields are rejected so a
caller cannot silently infer an evolution reason from ORFS results here.
The resulting receipt remains evaluation-only and cannot mutate canonical
memory or production runtime state.
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

from tehm.evaluation import OrfsPairedCohortReceipt, RtlPairedCohortReceipt  # noqa: E402
from tehm.evolution import P13EvolutionReasonReceipt  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402


LABEL_MANIFEST_VERSION = "p13-evolution-reason-label-manifest-v1"
_FORBIDDEN_KEYS = frozenset({
    "fix", "gold_patch", "repaired_rtl", "heldout_answer", "gold",
    "outcome", "baseline_outcome", "memory_outcome", "paired_outcomes",
    "candidate_outcome", "repair_result", "harm_result",
})


class P13ReasonReceiptError(ValueError):
    """The cohort or independently authored reason manifest is unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(value: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(value)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13ReasonReceiptError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13ReasonReceiptError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P13ReasonReceiptError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise P13ReasonReceiptError(f"{name} must be a sha256 digest")
    return value


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_KEYS or _contains_forbidden(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _cohort(path: Path) -> tuple[str, str, set[str]]:
    payload = _load_json(path, "cohort receipt")
    errors: list[str] = []
    for cls, label in ((OrfsPairedCohortReceipt, "ORFS"),
                       (RtlPairedCohortReceipt, "RTL")):
        try:
            receipt = cls.from_dict(payload)
            return receipt.campaign_id, receipt.receipt_digest, set(receipt.case_receipts)
        except (TypeError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    raise P13ReasonReceiptError(
        "cohort receipt is neither a valid ORFS nor RTL receipt (" +
        "; ".join(errors) + ")")


def _evidence_refs(
        payload: Mapping, manifest_path: Path,
        *, forbidden_paths: tuple[Path, ...] = ()) -> tuple[dict, ...]:
    raw = payload.get("evidence_refs")
    if (not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or
            not raw):
        raise P13ReasonReceiptError("reason manifest requires non-empty evidence_refs")
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise P13ReasonReceiptError("reason evidence_ref must be an object")
        raw_path = item.get("path") or item.get("file")
        if type(raw_path) is not str or not raw_path.strip():
            raise P13ReasonReceiptError("reason evidence_ref requires path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise P13ReasonReceiptError(f"reason evidence is not a file: {path}")
        if path in forbidden_paths:
            raise P13ReasonReceiptError(
                "reason evidence must be independent from cohort and label manifest")
        digest = _sha256(path)
        expected = item.get("sha256") or item.get("digest")
        if expected is not None and expected != digest:
            raise P13ReasonReceiptError(f"reason evidence digest mismatch: {path}")
        ref = dict(item)
        ref["path"] = str(path)
        ref["sha256"] = digest
        key = stable_dumps(ref)
        if key in seen:
            raise P13ReasonReceiptError("reason evidence_refs contain duplicates")
        seen.add(key)
        result.append(ref)
    return tuple(result)


def _case_evidence_refs(
        payload: Mapping, manifest_path: Path, case_ids: set[str],
        *, forbidden_paths: tuple[Path, ...] = ()) -> dict[str, tuple[dict, ...]] | None:
    """Resolve optional immutable evidence refs for each labelled case."""
    raw = payload.get("case_evidence_refs")
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != case_ids:
        raise P13ReasonReceiptError(
            "case_evidence_refs must cover exactly all cohort cases")
    result: dict[str, tuple[dict, ...]] = {}
    for case_id in sorted(case_ids):
        result[case_id] = _evidence_refs(
            {"evidence_refs": raw[case_id]}, manifest_path,
            forbidden_paths=forbidden_paths)
    return result


def build_p13_evolution_reason_receipt(
        cohort: Path | str, labels: Path | str, *, output: Path | str) -> dict:
    """Bind explicit labels to the exact current cohort receipt digest."""
    cohort_path = Path(cohort).expanduser().resolve()
    labels_path = Path(labels).expanduser().resolve()
    campaign_id, cohort_digest, case_ids = _cohort(cohort_path)
    payload = _load_json(labels_path, "evolution reason label manifest")
    if payload.get("version") != LABEL_MANIFEST_VERSION:
        raise P13ReasonReceiptError("evolution reason label manifest version mismatch")
    if _text(payload.get("campaign_id"), "campaign_id") != campaign_id:
        raise P13ReasonReceiptError("label campaign_id does not match cohort")
    if _digest_text(payload.get("cohort_receipt_digest"),
                    "cohort_receipt_digest") != cohort_digest:
        raise P13ReasonReceiptError("label cohort_receipt_digest does not match cohort")
    if _contains_forbidden(payload):
        raise P13ReasonReceiptError(
            "evolution reason label manifest contains outcome or gold fields")
    label_source = _text(payload.get("label_source"), "label_source")
    raw_reasons = payload.get("evolution_reasons")
    if (not isinstance(raw_reasons, Mapping) or
            set(raw_reasons) != case_ids):
        raise P13ReasonReceiptError(
            "evolution_reasons must cover exactly all cohort cases")
    refs = _evidence_refs(
        payload, labels_path, forbidden_paths=(cohort_path, labels_path))
    case_refs = _case_evidence_refs(
        payload, labels_path, case_ids,
        forbidden_paths=(cohort_path, labels_path))
    try:
        receipt = P13EvolutionReasonReceipt(
            campaign_id=campaign_id, cohort_receipt_digest=cohort_digest,
            label_source=label_source, evidence_refs=refs,
            evolution_reasons=dict(raw_reasons), case_evidence_refs=case_refs)
    except (TypeError, ValueError) as exc:
        raise P13ReasonReceiptError(str(exc)) from exc
    report = {
        **receipt.to_dict(),
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.receipt_digest,
        "cohort_receipt": str(cohort_path),
        "cohort_receipt_sha256": _sha256(cohort_path),
        "labels_manifest": str(labels_path),
        "labels_manifest_sha256": _sha256(labels_path),
        "labels_manifest_digest": _digest(payload),
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
    }
    output_path = Path(output).expanduser().resolve()
    if output_path in {cohort_path, labels_path}:
        raise P13ReasonReceiptError(
            "reason receipt output must be separate from cohort and labels")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True,
                        help="independently authored reason-label manifest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_p13_evolution_reason_receipt(
            args.cohort, args.labels, output=args.output)
    except (OSError, P13ReasonReceiptError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "cohort_receipt_digest": report["cohort_receipt_digest"],
        "receipt_digest": report["receipt_digest"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
