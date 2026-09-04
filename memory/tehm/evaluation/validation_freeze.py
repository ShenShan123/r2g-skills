"""Freeze a completed P12 cohort as a validation/negative-control lane.

Revision3 separates a stable replay cohort from an evolution challenge cohort.
This module binds an existing cohort report and its P13 replay report into a
small content-addressed receipt whose expected action is ``RETAIN``.  It does
not derive an evolution reason, write SQLite, or grant production authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tehm.ids import stable_dumps

from .orfs_cohort import OrfsPairedCohortReceipt
from .rtl_cohort import RtlPairedCohortReceipt


VALIDATION_FREEZE_VERSION = "validation-cohort-freeze-v0.1"


class ValidationFreezeError(ValueError):
    """A validation cohort cannot be frozen as a safe negative control."""


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValidationFreezeError(f"validation freeze {field} is required")
    return value.strip()


def _digest_text(value: object, field: str) -> str:
    value = _text(value, field)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise ValidationFreezeError(f"validation freeze {field} must be a sha256 digest")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _load(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationFreezeError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ValidationFreezeError(f"{label} must be a JSON object")
    return payload


def _verify_report_boundary(report: Mapping) -> None:
    """Verify the freeze wrapper before trusting its nested receipt.

    The nested :class:`ValidationCohortFreezeReceipt` is content addressed,
    but the emitted JSON wrapper also carries the release and authority
    boundary.  Replay must reject a wrapper that was edited to claim that
    governing docs were submitted or that production was touched.  The
    report digest is calculated before it is added to the wrapper.
    """
    if report.get("version") != "validation-cohort-freeze-report-v1":
        raise ValidationFreezeError("validation freeze report version is invalid")
    expected_boundary = {
        "lane": "VALIDATION",
        "expected_action": "RETAIN",
        "expected_evolution": False,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }
    for field, expected in expected_boundary.items():
        if report.get(field) != expected:
            raise ValidationFreezeError(
                f"validation freeze report crosses {field} boundary")
    supplied = report.get("report_digest")
    if supplied != _digest({key: value for key, value in report.items()
                            if key != "report_digest"}):
        raise ValidationFreezeError("validation freeze report digest mismatch")


def _cohort_from_report(report: Mapping):
    payload = report.get("cohort_receipt")
    if not isinstance(payload, Mapping):
        raise ValidationFreezeError("validation cohort report lacks nested cohort_receipt")
    errors: list[str] = []
    for cls, kind in ((OrfsPairedCohortReceipt, "ORFS"),
                      (RtlPairedCohortReceipt, "RTL")):
        try:
            return kind, cls.from_dict(payload)
        except (TypeError, ValueError) as exc:
            errors.append(f"{kind}: {exc}")
    raise ValidationFreezeError("nested cohort receipt is invalid (" + "; ".join(errors) + ")")


def _validate_all_pass(cohort, report: Mapping) -> tuple[tuple[str, ...], tuple[str, ...], dict]:
    if report.get("campaign_id") != cohort.campaign_id:
        raise ValidationFreezeError("cohort report campaign_id does not match receipt")
    report_digest = report.get("cohort_receipt_digest")
    if report_digest != cohort.receipt_digest:
        raise ValidationFreezeError("cohort report receipt digest does not match nested receipt")
    if report.get("canonical_memory_mutation") != "none":
        raise ValidationFreezeError("validation cohort reports canonical mutation")
    if report.get("production_runtime_imported") is True:
        raise ValidationFreezeError("validation cohort imported production runtime")
    if cohort.evaluation_only is not True or cohort.source_disjoint is not True:
        raise ValidationFreezeError("validation cohort is not evaluation-only/source-disjoint")
    case_ids = tuple(sorted(cohort.case_receipts))
    lineages = tuple(sorted(set(cohort.lineage_ids.values())))
    if len(lineages) < 2:
        raise ValidationFreezeError("validation cohort requires at least two lineages")
    counts = report.get("outcome_counts")
    if counts != cohort.outcome_counts:
        raise ValidationFreezeError("cohort report outcome counts do not replay")
    expected = len(case_ids)
    for arm, values in sorted(counts.items()):
        if (values.get("PASS") != expected or values.get("FAIL", 0) or
                values.get("PARTIAL", 0) or values.get("UNKNOWN", 0)):
            raise ValidationFreezeError(
                f"validation cohort is not all-PASS for arm {arm}")
    return case_ids, lineages, counts


def _validate_negative_trigger(trigger: Mapping, *, campaign_id: str,
                                cohort_digest: str, case_count: int) -> tuple[int, int, tuple[str, ...]]:
    if trigger.get("campaign_id") != campaign_id:
        raise ValidationFreezeError("trigger report campaign_id does not match cohort")
    if trigger.get("cohort_receipt_digest") != cohort_digest:
        raise ValidationFreezeError("trigger report cohort digest does not match cohort")
    trigger_count = trigger.get("trigger_count")
    triggered_count = trigger.get("triggered_count")
    if trigger_count != case_count or triggered_count != 0:
        raise ValidationFreezeError("validation trigger report is not a zero-trigger replay")
    if trigger.get("p13_eligible") is not False:
        raise ValidationFreezeError("validation trigger report unexpectedly enables P13")
    blocked = trigger.get("blocked_reasons")
    if not isinstance(blocked, list) or "no_evolution_signal" not in blocked:
        raise ValidationFreezeError("validation trigger report lacks no_evolution_signal")
    if trigger.get("canonical_memory_mutation") != "none":
        raise ValidationFreezeError("validation trigger report reports canonical mutation")
    if trigger.get("production_runtime_imported") is True:
        raise ValidationFreezeError("validation trigger report imported production runtime")
    return int(trigger_count), int(triggered_count), tuple(sorted(set(blocked)))


@dataclass(frozen=True)
class ValidationCohortFreezeReceipt:
    """Content-addressed Validation Cohort V0 receipt."""

    campaign_id: str
    cohort_kind: str
    cohort_report_path: str
    cohort_report_sha256: str
    cohort_receipt_digest: str
    trigger_report_path: str
    trigger_report_sha256: str
    trigger_report_digest: str
    case_ids: tuple[str, ...]
    lineage_ids: tuple[str, ...]
    outcome_counts: dict
    trigger_count: int
    triggered_count: int
    blocked_reasons: tuple[str, ...]
    lane: str = "VALIDATION"
    expected_evolution: bool = False
    expected_action: str = "RETAIN"
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    production_runtime_imported: bool = False
    version: str = VALIDATION_FREEZE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _text(self.campaign_id, "campaign_id"))
        object.__setattr__(self, "cohort_kind", _text(self.cohort_kind, "cohort_kind"))
        object.__setattr__(self, "cohort_report_path", _text(self.cohort_report_path,
                                                               "cohort_report_path"))
        object.__setattr__(self, "trigger_report_path", _text(self.trigger_report_path,
                                                                "trigger_report_path"))
        for field in ("cohort_report_sha256", "cohort_receipt_digest",
                      "trigger_report_sha256", "trigger_report_digest"):
            object.__setattr__(self, field, _digest_text(getattr(self, field), field))
        if self.lane != "VALIDATION" or self.expected_evolution is not False:
            raise ValidationFreezeError("validation freeze lane/evolution contract is invalid")
        if self.expected_action != "RETAIN":
            raise ValidationFreezeError("validation freeze expected action must be RETAIN")
        if self.evaluation_only is not True or self.canonical_memory_mutation != "none":
            raise ValidationFreezeError("validation freeze must be evaluation-only and immutable")
        if self.production_runtime_imported is not False:
            raise ValidationFreezeError("validation freeze cannot import production runtime")
        if self.version != VALIDATION_FREEZE_VERSION:
            raise ValidationFreezeError("validation freeze version is invalid")
        if not isinstance(self.case_ids, tuple) or len(self.case_ids) < 1:
            raise ValidationFreezeError("validation freeze case_ids are required")
        if any(type(item) is not str or not item for item in self.case_ids):
            raise ValidationFreezeError("validation freeze case_ids are invalid")
        if not isinstance(self.lineage_ids, tuple) or len(self.lineage_ids) < 2:
            raise ValidationFreezeError("validation freeze requires two lineages")
        if any(type(item) is not str or not item for item in self.lineage_ids):
            raise ValidationFreezeError("validation freeze lineage_ids are invalid")
        if len(set(self.lineage_ids)) != len(self.lineage_ids):
            raise ValidationFreezeError("validation freeze lineage_ids contain duplicates")
        if not isinstance(self.outcome_counts, dict):
            raise ValidationFreezeError("validation freeze outcome_counts are invalid")
        if type(self.trigger_count) is not int or self.trigger_count != len(self.case_ids):
            raise ValidationFreezeError("validation freeze trigger_count is invalid")
        if self.triggered_count != 0:
            raise ValidationFreezeError("validation freeze triggered_count must be zero")
        if not isinstance(self.blocked_reasons, tuple) or "no_evolution_signal" not in self.blocked_reasons:
            raise ValidationFreezeError("validation freeze blocked_reasons lack no_evolution_signal")

    def to_dict(self) -> dict:
        return {
            "version": self.version, "campaign_id": self.campaign_id,
            "cohort_kind": self.cohort_kind,
            "cohort_report_path": self.cohort_report_path,
            "cohort_report_sha256": self.cohort_report_sha256,
            "cohort_receipt_digest": self.cohort_receipt_digest,
            "trigger_report_path": self.trigger_report_path,
            "trigger_report_sha256": self.trigger_report_sha256,
            "trigger_report_digest": self.trigger_report_digest,
            "case_ids": list(self.case_ids), "lineage_ids": list(self.lineage_ids),
            "outcome_counts": self.outcome_counts,
            "trigger_count": self.trigger_count, "triggered_count": self.triggered_count,
            "blocked_reasons": list(self.blocked_reasons), "lane": self.lane,
            "expected_evolution": self.expected_evolution,
            "expected_action": self.expected_action,
            "evaluation_only": self.evaluation_only,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "production_runtime_imported": self.production_runtime_imported,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def receipt_id(self) -> str:
        return "validation_cohort_freeze_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "ValidationCohortFreezeReceipt":
        if not isinstance(payload, Mapping):
            raise ValidationFreezeError("validation freeze receipt must be an object")
        required = set(cls.__dataclass_fields__)
        if not required <= set(payload):
            raise ValidationFreezeError("validation freeze receipt is missing fields")
        receipt = cls(
            version=payload["version"], campaign_id=payload["campaign_id"],
            cohort_kind=payload["cohort_kind"], cohort_report_path=payload["cohort_report_path"],
            cohort_report_sha256=payload["cohort_report_sha256"],
            cohort_receipt_digest=payload["cohort_receipt_digest"],
            trigger_report_path=payload["trigger_report_path"],
            trigger_report_sha256=payload["trigger_report_sha256"],
            trigger_report_digest=payload["trigger_report_digest"],
            case_ids=tuple(payload["case_ids"]), lineage_ids=tuple(payload["lineage_ids"]),
            outcome_counts=dict(payload["outcome_counts"]),
            trigger_count=payload["trigger_count"], triggered_count=payload["triggered_count"],
            blocked_reasons=tuple(payload["blocked_reasons"]), lane=payload["lane"],
            expected_evolution=payload["expected_evolution"],
            expected_action=payload["expected_action"],
            evaluation_only=payload["evaluation_only"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            production_runtime_imported=payload["production_runtime_imported"],)
        if payload.get("receipt_digest") not in (None, receipt.receipt_digest):
            raise ValidationFreezeError("validation freeze receipt digest mismatch")
        if payload.get("receipt_id") not in (None, receipt.receipt_id):
            raise ValidationFreezeError("validation freeze receipt ID mismatch")
        return receipt


def freeze_validation_cohort(cohort_report: Path | str, trigger_report: Path | str,
                             *, output: Path | str) -> dict:
    """Freeze an all-PASS zero-trigger report as Validation Cohort V0."""
    cohort_path = Path(cohort_report).expanduser().resolve()
    trigger_path = Path(trigger_report).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if cohort_path == trigger_path or output_path in {cohort_path, trigger_path}:
        raise ValidationFreezeError("validation freeze output must be separate from inputs")
    cohort_report_payload = _load(cohort_path, "cohort report")
    trigger_report_payload = _load(trigger_path, "trigger report")
    kind, cohort = _cohort_from_report(cohort_report_payload)
    case_ids, lineage_ids, counts = _validate_all_pass(cohort, cohort_report_payload)
    trigger_count, triggered_count, blocked = _validate_negative_trigger(
        trigger_report_payload, campaign_id=cohort.campaign_id,
        cohort_digest=cohort.receipt_digest, case_count=len(case_ids))
    receipt = ValidationCohortFreezeReceipt(
        campaign_id=cohort.campaign_id, cohort_kind=kind,
        cohort_report_path=str(cohort_path), cohort_report_sha256=_sha256(cohort_path),
        cohort_receipt_digest=cohort.receipt_digest,
        trigger_report_path=str(trigger_path), trigger_report_sha256=_sha256(trigger_path),
        trigger_report_digest=_digest(trigger_report_payload), case_ids=case_ids,
        lineage_ids=lineage_ids, outcome_counts=counts, trigger_count=trigger_count,
        triggered_count=triggered_count, blocked_reasons=blocked)
    report = {
        "version": "validation-cohort-freeze-report-v1",
        "lane": "VALIDATION", "expected_action": "RETAIN",
        "expected_evolution": False, "campaign_id": receipt.campaign_id,
        "cohort_report": str(cohort_path), "trigger_report": str(trigger_path),
        "cohort_receipt_digest": cohort.receipt_digest,
        "case_ids": list(receipt.case_ids), "lineage_ids": list(receipt.lineage_ids),
        "outcome_counts": receipt.outcome_counts,
        "trigger_count": receipt.trigger_count, "triggered_count": receipt.triggered_count,
        "blocked_reasons": list(receipt.blocked_reasons),
        "canonical_memory_mutation": "none", "production_runtime_imported": False,
        "memory_docs_submitted": False,
        "freeze_receipt": {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
                           "receipt_digest": receipt.receipt_digest},
    }
    report["report_digest"] = _digest(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def replay_validation_freeze(path: Path | str) -> ValidationCohortFreezeReceipt:
    """Re-read a freeze receipt and verify both immutable report files unchanged."""
    freeze_path = Path(path).expanduser().resolve()
    report = _load(freeze_path, "validation freeze report")
    _verify_report_boundary(report)
    receipt_payload = report.get("freeze_receipt")
    receipt = ValidationCohortFreezeReceipt.from_dict(receipt_payload)
    cohort_path = Path(receipt.cohort_report_path)
    trigger_path = Path(receipt.trigger_report_path)
    if _sha256(cohort_path) != receipt.cohort_report_sha256:
        raise ValidationFreezeError("validation cohort report changed after freeze")
    if _sha256(trigger_path) != receipt.trigger_report_sha256:
        raise ValidationFreezeError("validation trigger report changed after freeze")
    # Re-run semantic checks, not merely file hashes, before declaring replay.
    kind, cohort = _cohort_from_report(_load(cohort_path, "cohort report"))
    if kind != receipt.cohort_kind or cohort.receipt_digest != receipt.cohort_receipt_digest:
        raise ValidationFreezeError("validation cohort receipt no longer replays")
    cohort_payload = _load(cohort_path, "cohort report")
    case_ids, lineages, _counts = _validate_all_pass(cohort, cohort_payload)
    trigger_payload = _load(trigger_path, "trigger report")
    _validate_negative_trigger(trigger_payload, campaign_id=receipt.campaign_id,
                               cohort_digest=receipt.cohort_receipt_digest,
                               case_count=len(case_ids))
    if case_ids != receipt.case_ids or lineages != receipt.lineage_ids:
        raise ValidationFreezeError("validation freeze cohort membership drifted")
    return receipt


__all__ = ["VALIDATION_FREEZE_VERSION", "ValidationFreezeError",
           "ValidationCohortFreezeReceipt", "freeze_validation_cohort",
           "replay_validation_freeze"]
