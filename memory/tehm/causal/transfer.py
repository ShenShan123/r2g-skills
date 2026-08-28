"""Held-out transfer evaluation for the causal shadow graph.

L3 says that a controlled effect replicated across independent training
lineages.  L4 is a separate claim: the mechanism transfers to an unseen
held-out lineage/design.  This module is intentionally read-only.  It emits a
receipt for an evaluation lane and never changes the path row, canonical
evidence, rule lifecycle, or production retrieval authority.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.dataset import normalize_stored_learner_bool

from .evidence_level import CausalEvidenceLevel
from .matcher import match_causal_path
from .mechanism import load_transition_facts, mechanism_signature
from .replication import evaluate_replicated_effect


# The ORFS lane has a stricter contract than a generic RTL verifier.  Keep the
# list local to this evaluator so causal code does not depend on the batch
# executor (and therefore cannot accidentally import or mutate a staging DB).
_FULL_ORACLE_SIDES = ("before", "after")
_FULL_ORACLE_CHECKS = (
    "synthesis", "equivalence", "route", "finish", "timing", "drc",
    "lvs", "strict_signoff", "ppa", "graph", "artifact_digest",
    "input_binding", "timing_contract", "toolchain_binding",
)


def _full_oracle_complete(verifier: Mapping) -> bool:
    """Return true only for an explicitly complete two-arm ORFS receipt."""
    full = verifier.get("full_oracle")
    if not isinstance(full, Mapping):
        return False
    for side in _FULL_ORACLE_SIDES:
        receipt = full.get(side)
        if not isinstance(receipt, Mapping) or receipt.get("complete") is not True:
            return False
        checks = receipt.get("checks")
        if not isinstance(checks, Mapping):
            return False
        # Requiring the exact pinned check set prevents a reduced or
        # caller-invented ``{"complete": true}`` receipt from satisfying an
        # ORFS held-out gate.
        if set(checks) != set(_FULL_ORACLE_CHECKS):
            return False
        if any(checks.get(name) is not True for name in _FULL_ORACLE_CHECKS):
            return False
    return True


def full_oracle_complete(verifier: Mapping) -> bool:
    """Public read-only predicate shared by ORFS audits and L4 evaluation."""
    return _full_oracle_complete(verifier)


def _source_transition_ids(raw: object) -> tuple[tuple[str, ...] | None, str | None]:
    try:
        values = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None, "malformed_source_transitions"
    if not isinstance(values, list) or not values:
        return None, "source_transitions_missing"
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return None, "malformed_source_transitions"
    ids = tuple(value.strip() for value in values)
    if len(set(ids)) != len(ids):
        return None, "duplicate_source_transitions"
    return tuple(sorted(ids)), None


def _transfer_ids(values) -> tuple[tuple[str, ...] | None, str | None]:
    if isinstance(values, (str, bytes)) or values is None:
        return None, "malformed_transfer_transitions"
    try:
        values = tuple(values)
    except TypeError:
        return None, "malformed_transfer_transitions"
    if not values:
        return None, "transfer_transitions_missing"
    if any(type(value) is not str or not value.strip() for value in values):
        return None, "malformed_transfer_transitions"
    ids = tuple(value.strip() for value in values)
    if len(set(ids)) != len(ids):
        return None, "duplicate_transfer_transitions"
    return tuple(sorted(ids)), None


@dataclass(frozen=True)
class TransferReceipt:
    """Non-authoritative receipt for one L3-to-held-out transfer check."""

    path_id: str
    eligible: bool
    evidence_level: str
    training_campaign_id: str
    transfer_campaign_id: str
    training_transition_ids: tuple[str, ...] = ()
    transfer_transition_ids: tuple[str, ...] = ()
    training_lineages: tuple[str, ...] = ()
    transfer_lineages: tuple[str, ...] = ()
    training_designs: tuple[str, ...] = ()
    transfer_designs: tuple[str, ...] = ()
    matched_transition_ids: tuple[str, ...] = ()
    mismatched_transition_ids: tuple[str, ...] = ()
    details: tuple[dict, ...] = ()
    reason: str = ""
    promotion_eligible: bool = False

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "eligible": self.eligible,
            "evidence_level": self.evidence_level,
            "training_campaign_id": self.training_campaign_id,
            "transfer_campaign_id": self.transfer_campaign_id,
            "training_transition_ids": list(self.training_transition_ids),
            "transfer_transition_ids": list(self.transfer_transition_ids),
            "training_lineages": list(self.training_lineages),
            "transfer_lineages": list(self.transfer_lineages),
            "training_designs": list(self.training_designs),
            "transfer_designs": list(self.transfer_designs),
            "matched_transition_ids": list(self.matched_transition_ids),
            "mismatched_transition_ids": list(self.mismatched_transition_ids),
            "details": list(self.details),
            "reason": self.reason,
            "promotion_eligible": self.promotion_eligible,
        }


def _failure(
    *, path_id: str, training_campaign_id: str, transfer_campaign_id: str,
    reason: str, training_transition_ids: tuple[str, ...] = (),
    transfer_transition_ids: tuple[str, ...] = (),
    training_lineages: tuple[str, ...] = (),
    transfer_lineages: tuple[str, ...] = (),
    training_designs: tuple[str, ...] = (),
    transfer_designs: tuple[str, ...] = (),
    details: tuple[dict, ...] = (),
    evidence_level: str = CausalEvidenceLevel.L3_REPLICATED_EFFECT.value,
) -> TransferReceipt:
    return TransferReceipt(
        path_id=path_id, eligible=False,
        evidence_level=evidence_level,
        training_campaign_id=training_campaign_id,
        transfer_campaign_id=transfer_campaign_id,
        training_transition_ids=training_transition_ids,
        transfer_transition_ids=transfer_transition_ids,
        training_lineages=training_lineages,
        transfer_lineages=transfer_lineages,
        training_designs=training_designs,
        transfer_designs=transfer_designs,
        details=details, reason=reason, promotion_eligible=False)


def evaluate_transfer_supported_mechanism(
    conn: sqlite3.Connection,
    path_id: str,
    transfer_transition_ids,
    *,
    training_campaign_id: str,
    transfer_campaign_id: str | None = None,
    min_training_lineages: int = 2,
    min_transfer_lineages: int = 1,
    require_full_oracle: bool = False,
) -> TransferReceipt:
    """Evaluate L4 transfer without mutating any database row.

    ``path_id`` must be a replicated training path.  Transfer transitions are
    required to be explicit held-out, non-learner evidence in the selected
    campaign, with disjoint lineage and design witnesses.  Concrete module,
    state, and guard names are intentionally parameters of the mechanism; the
    typed action domain, mechanism family, compatibility profile, and effect
    key remain hard matching dimensions.  A held-out observation that was
    already clean before the action is not sufficient: transfer must show a
    verified unseen fail-to-pass repair.  ``require_full_oracle`` is used by
    ORFS authority audits to require the pinned two-arm full-oracle receipt;
    generic RTL callers may leave it false and use their own executable oracle
    contract.
    """
    if not training_campaign_id:
        raise ValueError("training_campaign_id is required")
    transfer_campaign_id = str(transfer_campaign_id or training_campaign_id)
    if not transfer_campaign_id:
        raise ValueError("transfer_campaign_id is required")
    path = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    if path is None:
        raise KeyError(f"unknown causal path: {path_id}")
    source_ids, source_error = _source_transition_ids(
        path["source_transitions_json"])
    ids, transfer_error = _transfer_ids(transfer_transition_ids)
    if source_ids is None:
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason=source_error or "malformed_source_transitions",
                        evidence_level=str(path["evidence_level"]))
    if ids is None:
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason=transfer_error or "malformed_transfer_transitions",
                        training_transition_ids=source_ids,
                        evidence_level=str(path["evidence_level"]))
    if set(source_ids) & set(ids):
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason="transfer_reuses_training_transition",
                        training_transition_ids=source_ids,
                        transfer_transition_ids=ids,
                        evidence_level=str(path["evidence_level"]))

    replication = evaluate_replicated_effect(
        conn, path_id, campaign_id=training_campaign_id,
        min_lineages=max(1, int(min_training_lineages)), persist=False)
    if not replication.eligible:
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason="training_replication_incomplete:" + replication.reason,
                        training_transition_ids=source_ids,
                        transfer_transition_ids=ids,
                        training_lineages=replication.unique_lineages,
                        training_designs=replication.unique_designs,
                        evidence_level=replication.evidence_level)

    placeholders = ",".join("?" for _ in source_ids)
    training_rows = conn.execute(
        f"""SELECT t.transition_id, s.lineage_id, s.design_id
              FROM tehm_transitions t
              JOIN tehm_states s ON s.state_id=t.source_state_id
              JOIN tehm_dataset_membership dm ON dm.transition_id=t.transition_id
             WHERE t.transition_id IN ({placeholders})
               AND dm.campaign_id=? AND dm.split='training'
               AND dm.learner_eligible=1""",
        (*source_ids, training_campaign_id)).fetchall()
    training_lineages = tuple(sorted({str(row["lineage_id"]) for row in training_rows
                                      if row["lineage_id"]}))
    training_designs = tuple(sorted({str(row["design_id"]) for row in training_rows
                                    if row["design_id"]}))

    placeholders = ",".join("?" for _ in ids)
    transfer_rows = conn.execute(
        f"""SELECT t.transition_id, s.lineage_id, s.design_id,
                           dm.split, dm.learner_eligible
              FROM tehm_transitions t
              JOIN tehm_states s ON s.state_id=t.source_state_id
              LEFT JOIN tehm_dataset_membership dm
                ON dm.transition_id=t.transition_id AND dm.campaign_id=?
             WHERE t.transition_id IN ({placeholders})""",
        (transfer_campaign_id, *ids)).fetchall()
    by_id = {str(row["transition_id"]): row for row in transfer_rows}
    if len(by_id) != len(ids):
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason="transfer_transition_missing",
                        training_transition_ids=source_ids,
                        transfer_transition_ids=ids,
                        training_lineages=training_lineages,
                        training_designs=training_designs)
    transfer_lineages = tuple(sorted({str(row["lineage_id"]) for row in transfer_rows
                                      if row["lineage_id"]}))
    transfer_designs = tuple(sorted({str(row["design_id"]) for row in transfer_rows
                                    if row["design_id"]}))
    transfer_firewall_violation = False
    for transfer_row in transfer_rows:
        if transfer_row["split"] != "heldout":
            transfer_firewall_violation = True
            break
        try:
            eligible = normalize_stored_learner_bool(
                transfer_row["learner_eligible"])
        except ValueError:
            transfer_firewall_violation = True
            break
        if eligible:
            transfer_firewall_violation = True
            break
    if transfer_firewall_violation:
        reason = "transfer_firewall_violation"
    elif len(transfer_lineages) < max(1, int(min_transfer_lineages)):
        reason = "insufficient_transfer_lineages"
    elif not transfer_designs:
        reason = "transfer_design_witness_missing"
    elif set(transfer_lineages) & set(training_lineages):
        reason = "transfer_lineage_not_disjoint"
    elif set(transfer_designs) & set(training_designs):
        reason = "transfer_design_not_disjoint"
    else:
        reason = ""
    if reason:
        return _failure(path_id=path_id,
                        training_campaign_id=training_campaign_id,
                        transfer_campaign_id=transfer_campaign_id,
                        reason=reason, training_transition_ids=source_ids,
                        transfer_transition_ids=ids,
                        training_lineages=training_lineages,
                        transfer_lineages=transfer_lineages,
                        training_designs=training_designs,
                        transfer_designs=transfer_designs)

    try:
        support = json.loads(path["support_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        support = {}
    effects = support.get("primary_effect_keys") if isinstance(support, Mapping) else None
    effects = {str(item) for item in effects or [] if item is not None}
    details = []
    matched = []
    mismatched = []
    for transition_id in ids:
        facts = load_transition_facts(conn, transition_id)
        verifier = facts.verifier or {}
        clean = bool(
            facts.outcome == "PASS" and
            facts.delta.get("original_failure") in {"REMOVED", "PRESENT"} and
            verifier.get("verdict") == "PASS" and
            verifier.get("oracle_type") not in {None, "UNKNOWN"} and
            verifier.get("evidence_refs") and
            not facts.delta.get("created_regressions") and
            not facts.delta.get("newly_observed_failures"))
        full_oracle = _full_oracle_complete(verifier)
        if require_full_oracle:
            clean = bool(clean and full_oracle)
        signature = mechanism_signature(facts)
        # Concrete module/state/guard values are bound slots, while these
        # typed dimensions define the transferable mechanism family.
        transferable = {
            key: signature.get(key) for key in (
                "mechanism_family", "action_domain", "transformation_family",
                "compatibility_profile")}
        match = match_causal_path(
            path, {"mechanism_family": facts.mechanism_family,
                   "compatibility_profile": facts.compatibility_profile,
                   "mechanism_signature": transferable})
        effect_match = bool(facts.primary_effect_key and
                            str(facts.primary_effect_key) in effects)
        item = {
            "transition_id": transition_id,
            "lineage_id": by_id[transition_id]["lineage_id"],
            "design_id": by_id[transition_id]["design_id"],
            "verifier_pass": clean,
            "full_oracle_required": bool(require_full_oracle),
            "full_oracle_complete": full_oracle,
            "mechanism_match": match.to_dict(),
            "effect_match": effect_match,
        }
        details.append(item)
        if clean and match.eligible and effect_match:
            matched.append(transition_id)
        else:
            mismatched.append(transition_id)
    eligible = len(matched) == len(ids)
    if eligible:
        reason = "transfer_supported_mechanism"
        level = CausalEvidenceLevel.L4_TRANSFER_SUPPORTED_MECHANISM.value
    else:
        reason = "heldout_transfer_witness_failed"
        level = CausalEvidenceLevel.L3_REPLICATED_EFFECT.value
    return TransferReceipt(
        path_id=path_id, eligible=eligible, evidence_level=level,
        training_campaign_id=training_campaign_id,
        transfer_campaign_id=transfer_campaign_id,
        training_transition_ids=source_ids,
        transfer_transition_ids=ids,
        training_lineages=training_lineages,
        transfer_lineages=transfer_lineages,
        training_designs=training_designs,
        transfer_designs=transfer_designs,
        matched_transition_ids=tuple(sorted(matched)),
        mismatched_transition_ids=tuple(sorted(mismatched)),
        details=tuple(details), reason=reason, promotion_eligible=False)


__all__ = ["TransferReceipt", "evaluate_transfer_supported_mechanism",
           "full_oracle_complete"]
