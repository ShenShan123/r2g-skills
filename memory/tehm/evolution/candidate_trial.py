"""Isolated online candidate-trial lane (design doc Phase B4).

The online manager may propose a revision, but it must not turn that proposal
into production authority.  This module copies the TEHM store into an
in-memory staging database, materializes the proposed rules there, enrolls
them as ``candidate`` only in that copy, and reuses the existing A/B trial
adapter.  The evaluator callbacks are the seam for the shared Icarus/ORFS
executor; deterministic unit tests can inject a controlled evaluator without
changing the authority boundary.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from tehm.ids import stable_dumps
from tehm.lifecycle.promotion_gates import evaluate_promotion_gates
from tehm.lifecycle.rule_status import RuleLifecycleError, enter_shadow, set_status
from tehm.lifecycle.trial_adapter import TEHMRuleTrialSubject, run_trial

from .incremental_crystallize import crystallize_affected_groups
from .receipts import IncrementalCrystallizationReceipt
from .rollback import build_isolated_rollback_receipt


class CandidateTrialError(RuntimeError):
    """Raised when a candidate trial cannot be kept inside staging."""


@dataclass(frozen=True)
class CandidateTrialReceipt:
    campaign_id: str
    transition_ids: tuple[str, ...]
    candidate_rule_ids: tuple[str, ...]
    trial_results: tuple[dict, ...]
    gate_report: dict
    promotion_eligible: bool
    equivalence_verified: bool
    source_digest_before: str
    source_digest_after: str
    source_unchanged: bool
    staging_digest: str
    staging_counts: dict = field(default_factory=dict)
    staging_rule_statuses: tuple[dict, ...] = field(default_factory=tuple)
    rollback_receipt: dict = field(default_factory=dict)
    reason: str = ""
    promotion_attempted: bool = False
    production_promotion_eligible: bool = False
    canonical_memory_mutation: str = "none"
    lifecycle_mutation: str = "isolated_staging_only"

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "transition_ids": list(self.transition_ids),
            "candidate_rule_ids": list(self.candidate_rule_ids),
            "trial_results": list(self.trial_results),
            "gate_report": self.gate_report,
            "promotion_eligible": self.promotion_eligible,
            "equivalence_verified": self.equivalence_verified,
            "source_digest_before": self.source_digest_before,
            "source_digest_after": self.source_digest_after,
            "source_unchanged": self.source_unchanged,
            "rollback_receipt": self.rollback_receipt,
            "staging_digest": self.staging_digest,
            "staging_counts": self.staging_counts,
            "staging_rule_statuses": list(self.staging_rule_statuses),
            "reason": self.reason,
            "promotion_attempted": self.promotion_attempted,
            "production_promotion_eligible": self.production_promotion_eligible,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "lifecycle_mutation": self.lifecycle_mutation,
        }


def _connection_digest(conn: sqlite3.Connection) -> str:
    """Hash a SQLite logical dump, independent of WAL sidecar bytes."""
    dump = "\n".join(conn.iterdump())
    return "sha256:" + hashlib.sha256(dump.encode()).hexdigest()


def _staging_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    staging = sqlite3.connect(":memory:")
    staging.row_factory = sqlite3.Row
    staging.execute("PRAGMA foreign_keys=ON")
    conn.backup(staging)
    return staging


def _trial_uuid(campaign_id: str, transition_ids: tuple[str, ...],
                rule_id: str) -> str:
    digest = hashlib.sha1(stable_dumps({
        "campaign_id": campaign_id,
        "transition_ids": transition_ids,
        "rule_id": rule_id,
    }).encode()).hexdigest()[:20]
    return f"online_candidate_{digest}"


def _staging_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("tehm_rules", "tehm_rule_sources", "tehm_rule_status",
              "tehm_rule_revisions", "tehm_trials")
    return {
        table: int(conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in tables
    }


def run_shadow_candidate_trial(
    conn: sqlite3.Connection,
    preview: IncrementalCrystallizationReceipt,
    *,
    context,
    arm_a_evaluator: Callable[[dict, object], Mapping],
    arm_b_evaluator: Callable[[dict, object], Mapping],
    repeats: int = 2,
    promotion_gates: Mapping | None = None,
) -> CandidateTrialReceipt:
    """Run a candidate A/B trial entirely in an isolated staging copy.

    ``arm_a_evaluator`` and ``arm_b_evaluator`` are deliberately injected at
    the shared execution boundary.  A real caller may close over an Icarus or
    ORFS runner; tests may use deterministic responses.  This function never
    calls production lifecycle authority and never writes the source
    connection.  Even when every supplied gate is true, the returned receipt
    records ``promotion_attempted=False`` and production promotion remains
    the responsibility of an explicit authority call.
    """
    if not isinstance(preview, IncrementalCrystallizationReceipt):
        raise TypeError("preview must be IncrementalCrystallizationReceipt")
    if preview.mode != "preview":
        raise CandidateTrialError("candidate trial requires mode=preview")
    if preview.full_rebuild_equivalent is not True:
        raise CandidateTrialError(
            "candidate trial requires an equivalent full-rebuild witness")
    if not preview.campaign_id or not preview.transition_ids:
        raise CandidateTrialError("preview campaign and transitions are required")
    if repeats < 1:
        raise ValueError("repeats must be positive")

    source_before = _connection_digest(conn)
    staging = _staging_copy(conn)
    staging_before = _connection_digest(staging)
    try:
        persisted = crystallize_affected_groups(
            staging, list(preview.transition_ids),
            campaign_id=preview.campaign_id)
        preview_ids = tuple(sorted(rule["rule_id"] for rule in preview.rules))
        persisted_ids = tuple(sorted(rule["rule_id"] for rule in persisted.rules))
        if persisted_ids != preview_ids:
            raise CandidateTrialError(
                "staging crystallization does not match preview rule IDs")
        if persisted.full_rebuild_equivalent is not True:
            raise CandidateTrialError(
                "staging candidate lacks full-rebuild equivalence")

        target_scope = str(getattr(context, "check", "") or "signoff")
        statuses: list[dict] = []
        for rule_id in persisted_ids:
            try:
                enter_shadow(
                    staging, rule_id=rule_id, target_scope=target_scope,
                    provenance={"authority": "online_candidate_trial",
                                "campaign_id": preview.campaign_id,
                                "preview": True})
                version = set_status(
                    staging, rule_id=rule_id, target_scope=target_scope,
                    status="candidate",
                    provenance={"authority": "isolated_staging",
                                "campaign_id": preview.campaign_id})
            except RuleLifecycleError as exc:
                raise CandidateTrialError(
                    f"candidate rule {rule_id} failed lifecycle entry") from exc
            statuses.append({"rule_id": rule_id, "target_scope": target_scope,
                             "status": "candidate", "status_version": version})

        trial_results: list[dict] = []
        for status in statuses:
            subject = TEHMRuleTrialSubject(
                rule_id=status["rule_id"], status_version=status["status_version"])
            trial_results.append(run_trial(
                staging, subject=subject, context=context,
                arm_a_evaluator=arm_a_evaluator,
                arm_b_evaluator=arm_b_evaluator, repeats=repeats,
                trial_uuid=_trial_uuid(
                    preview.campaign_id, preview.transition_ids,
                    status["rule_id"])))

        gate_report = evaluate_promotion_gates(
            promotion_gates, strict=True)
        trial_wins = bool(trial_results) and all(
            trial["verdict"] == "win" and
            trial["arm_a_samples"] != trial["arm_b_samples"]
            for trial in trial_results)
        promotion_eligible = bool(gate_report["eligible"] and trial_wins)
        source_after = _connection_digest(conn)
        source_unchanged = source_before == source_after
        if not source_unchanged:
            raise CandidateTrialError(
                "source TEHM connection changed during isolated candidate trial")
        staging_after = _connection_digest(staging)
        rollback = build_isolated_rollback_receipt(
            source_digest_before=source_before,
            source_digest_after=source_after,
            staging_digest_before=staging_before,
            staging_digest_after=staging_after,
            staging_discarded=True)
        reason = ("candidate trial passed supplied gates; explicit authority "
                  "call remains required" if promotion_eligible else
                  "candidate remains staging-only: trial or gates incomplete")
        return CandidateTrialReceipt(
            campaign_id=preview.campaign_id,
            transition_ids=preview.transition_ids,
            candidate_rule_ids=persisted_ids,
            trial_results=tuple(trial_results),
            gate_report=gate_report,
            promotion_eligible=promotion_eligible,
            equivalence_verified=True,
            source_digest_before=source_before,
            source_digest_after=source_after,
            source_unchanged=source_unchanged,
            rollback_receipt=rollback.to_dict(),
            staging_digest=_connection_digest(staging),
            staging_counts=_staging_counts(staging),
            staging_rule_statuses=tuple(statuses),
            reason=reason,
            production_promotion_eligible=False,
        )
    finally:
        staging.close()


__all__ = ["CandidateTrialError", "CandidateTrialReceipt",
           "run_shadow_candidate_trial"]
