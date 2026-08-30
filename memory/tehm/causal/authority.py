"""Fail-closed causal evidence receipts for rule evaluation.

This is deliberately not the production rule-promotion authority.  It checks
whether a shadow causal path is strong enough for an isolated rule-evidence
experiment while preserving the existing six-gate lifecycle boundary.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .evidence_level import CausalEvidenceLevel, evidence_rank
from .path_builder import validate_persisted_path_row
from .replication import evaluate_replicated_effect
from .witness import (
    learner_edge_transition_coverage, parse_source_transition_ids,
)
from tehm.dataset import validate_membership_row


@dataclass(frozen=True)
class CausalRuleEvidenceReceipt:
    path_id: str
    eligible: bool
    evidence_level: str
    source_transition_ids: tuple[str, ...]
    unique_lineages: tuple[str, ...]
    learner_eligible: bool
    required_level: str
    reason: str
    promotion_eligible: bool = False

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "eligible": self.eligible,
            "evidence_level": self.evidence_level,
            "source_transition_ids": list(self.source_transition_ids),
            "unique_lineages": list(self.unique_lineages),
            "learner_eligible": self.learner_eligible,
            "required_level": self.required_level,
            "reason": self.reason,
            "promotion_eligible": self.promotion_eligible,
        }


def evaluate_causal_rule_evidence(
    conn: sqlite3.Connection,
    path_id: str,
    *,
    campaign_id: str,
    required_level: str = CausalEvidenceLevel.L2_CONTROLLED_INTERVENTION.value,
    min_lineages: int = 1,
) -> CausalRuleEvidenceReceipt:
    """Evaluate a path for an isolated rule-evidence lane only."""
    if not campaign_id:
        raise ValueError("campaign_id is required")
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown causal path: {path_id}")
    source_ids, source_error = parse_source_transition_ids(
        row["source_transitions_json"])
    if source_ids is None:
        return CausalRuleEvidenceReceipt(
            path_id=path_id, eligible=False,
            evidence_level=row["evidence_level"], source_transition_ids=(),
            unique_lineages=(), learner_eligible=False,
            required_level=required_level,
            reason=source_error or "malformed_source_transitions",
            promotion_eligible=False)
    try:
        validate_persisted_path_row(row, conn)
    except ValueError as exc:
        return CausalRuleEvidenceReceipt(
            path_id=path_id, eligible=False,
            evidence_level=row["evidence_level"],
            source_transition_ids=source_ids, unique_lineages=(),
            learner_eligible=False, required_level=required_level,
            reason="path_integrity_failed:" + str(exc),
            promotion_eligible=False)
    placeholders = ",".join("?" for _ in source_ids)
    memberships = []
    if source_ids:
        memberships = conn.execute(
            f"""SELECT dm.transition_id, dm.learner_eligible, dm.split,
                             s.lineage_id
                  FROM tehm_dataset_membership dm
                  JOIN tehm_transitions t ON t.transition_id=dm.transition_id
                  JOIN tehm_states s ON s.state_id=t.source_state_id
                 WHERE dm.transition_id IN ({placeholders})
                   AND dm.campaign_id=?""",
            (*source_ids, campaign_id)).fetchall()
    lineages = tuple(sorted({row["lineage_id"] for row in memberships
                             if row["lineage_id"]}))
    parsed_memberships: list[tuple[bool, str | None]] = []
    membership_types_valid = True
    for membership in memberships:
        try:
            parsed_memberships.append(validate_membership_row(membership))
        except ValueError:
            membership_types_valid = False
            parsed_memberships.append((False, None))
    all_members = len(memberships) == len(source_ids)
    all_learner = all(eligible for eligible, _ in parsed_memberships)
    all_training = all(split == "training" for _, split in parsed_memberships)
    level_ok = evidence_rank(row["evidence_level"]) >= evidence_rank(required_level)
    covered_sources = learner_edge_transition_coverage(
        conn, source_ids, campaign_id=campaign_id, required_level=required_level)
    controlled_support = bool(covered_sources)
    controlled_coverage_complete = set(covered_sources) == set(source_ids)
    reasons = []
    if not level_ok:
        reasons.append("path_evidence_level_below_required")
    if not all_members:
        reasons.append("missing_campaign_membership")
    if not all_learner or not all_training:
        reasons.append("learner_firewall_or_split_violation")
    if not membership_types_valid:
        reasons.append("learner_membership_type_invalid")
    if len(lineages) < max(1, int(min_lineages)):
        reasons.append("insufficient_disjoint_lineages")
    if evidence_rank(required_level) >= evidence_rank(
            CausalEvidenceLevel.L2_CONTROLLED_INTERVENTION.value):
        if not controlled_support:
            reasons.append("controlled_intervention_support_missing")
        elif not controlled_coverage_complete:
            reasons.append("controlled_intervention_source_coverage_incomplete")
    # A path marked L3 must carry the full replication witness, not merely an
    # old/stale evidence_level value.  Re-run the independent design/run gate
    # here so callers cannot use the rule-evidence seam to bypass it.
    if evidence_rank(row["evidence_level"]) >= evidence_rank(
            CausalEvidenceLevel.L3_REPLICATED_EFFECT.value):
        replication = evaluate_replicated_effect(
            conn, path_id, campaign_id=campaign_id,
            min_lineages=max(1, int(min_lineages)))
        if not replication.eligible:
            reasons.append("replication_witness_incomplete:" + replication.reason)
    eligible = not reasons
    return CausalRuleEvidenceReceipt(
        path_id=path_id, eligible=eligible,
        evidence_level=row["evidence_level"], source_transition_ids=source_ids,
        unique_lineages=lineages, learner_eligible=all_learner and all_training,
        required_level=required_level,
        reason="causal_rule_evidence_eligible" if eligible else ";".join(reasons),
        promotion_eligible=False)


__all__ = ["CausalRuleEvidenceReceipt", "evaluate_causal_rule_evidence"]
