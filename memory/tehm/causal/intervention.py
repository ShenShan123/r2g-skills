"""Controlled intervention-pair receipts for the causal shadow lane."""
from __future__ import annotations

import hashlib
import sqlite3

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .edges import CausalEdge, persist_edge
from .evidence_level import CausalEvidenceLevel
from .mechanism import action_digest, load_transition_facts
from .receipts import InterventionReceipt


def build_intervention_pair(
    control_transition_id,
    treatment_transition_id: str | None = None,
    *args,
    conn: sqlite3.Connection | None = None,
    target_scope: str | None = None,
    lineage_id: str | None = None,
    campaign_id: str | None = None,
    commit: bool = True,
) -> InterventionReceipt:
    """Compare two executed transitions without mutating canonical evidence.

    A valid pair requires matched source graph/toolchain context and a changed
    action.  Invalid pairs are still returned as audit receipts, but are never
    labelled controlled intervention evidence.  An already-active caller
    transaction remains open; ``commit=True`` commits only when this helper
    owns the transaction.  ``commit=False`` leaves the derived pair/edge rows
    for the enclosing savepoint or transaction.
    """
    if isinstance(control_transition_id, sqlite3.Connection):
        conn = control_transition_id
        if not args:
            raise TypeError(
                "build_intervention_pair(conn, control_transition_id, "
                "treatment_transition_id) requires three positional arguments")
        control_transition_id = str(treatment_transition_id)
        treatment_transition_id = str(args[0])
    else:
        control_transition_id = str(control_transition_id)
        if treatment_transition_id is None:
            raise TypeError("treatment_transition_id is required")
        treatment_transition_id = str(treatment_transition_id)
    if conn is None:
        raise ValueError("conn is required to build an intervention pair")
    requested_campaign = (str(campaign_id).strip()
                          if campaign_id is not None else None)
    if campaign_id is not None and not requested_campaign:
        raise ValueError("campaign_id must be non-empty when provided")
    control = load_transition_facts(conn, control_transition_id)
    treatment = load_transition_facts(conn, treatment_transition_id)
    if control_transition_id == treatment_transition_id:
        raise ValueError("control and treatment transitions must differ")
    matched_context = (
        control.failure_graph_digest
        if control.failure_graph_digest and
        control.failure_graph_digest == treatment.failure_graph_digest
        else None)
    toolchain_control = control.source_state.get("verifier") or {}
    toolchain_treatment = treatment.source_state.get("verifier") or {}
    same_toolchain = stable_dumps(toolchain_control) == stable_dumps(toolchain_treatment)
    changed_action = action_digest(control.action) != action_digest(treatment.action)
    same_lineage = (control.lineage_id is not None and
                    control.lineage_id == treatment.lineage_id)
    # An L2 edge is learner evidence, so both transitions must be assigned to
    # the same explicit training campaign.  Do not infer eligibility from a
    # caller boolean or from whichever membership row happens to sort first.
    def _learner_campaigns(transition_id: str) -> set[str]:
        rows = conn.execute(
            """SELECT campaign_id FROM tehm_dataset_membership
                WHERE transition_id=? AND split='training'
                  AND learner_eligible=1""", (transition_id,)).fetchall()
        return {str(row["campaign_id"]) for row in rows
                if row["campaign_id"]}

    common_campaigns = (_learner_campaigns(control_transition_id) &
                        _learner_campaigns(treatment_transition_id))
    selected_campaign = (requested_campaign if requested_campaign is not None else
                         ("live" if "live" in common_campaigns else
                          sorted(common_campaigns)[0]
                          if common_campaigns else None))
    same_learner_campaign = bool(
        selected_campaign and selected_campaign in common_campaigns)
    oracle_equivalence = {
        "same_oracle_type": control.verifier.get("oracle_type") == treatment.verifier.get("oracle_type"),
        "same_scope": control.verifier.get("scope") == treatment.verifier.get("scope"),
        "same_toolchain": same_toolchain,
        "same_learner_campaign": same_learner_campaign,
        "campaign_id": selected_campaign,
    }
    actual_oracle = (control.verifier.get("oracle_type") not in {None, "UNKNOWN"}
                     and treatment.verifier.get("oracle_type") not in {None, "UNKNOWN"}
                     and control.verifier.get("verdict") not in {None, "UNKNOWN"}
                     and treatment.verifier.get("verdict") not in {None, "UNKNOWN"})
    valid = bool(matched_context and same_toolchain and changed_action and same_lineage
                 and same_learner_campaign
                 and oracle_equivalence["same_oracle_type"]
                 and oracle_equivalence["same_scope"] and actual_oracle)
    validity = "VALID_CONTROLLED_PAIR" if valid else "INVALID_UNMATCHED_CONTEXT"
    evidence_level = (CausalEvidenceLevel.L2_CONTROLLED_INTERVENTION.value
                      if valid else CausalEvidenceLevel.L0_ASSOCIATION.value)
    scope = str(target_scope or control.source_state.get("domain") or "unknown")
    pair_payload = {
        "control": control_transition_id,
        "treatment": treatment_transition_id,
        "scope": scope,
        "matched_context_digest": matched_context,
        "changed_action_digest": action_digest(treatment.action),
        "validity_status": validity,
        "lineage_id": lineage_id or control.lineage_id,
        "campaign_id": selected_campaign,
    }
    pair_id = "intervention_pair_" + hashlib.sha1(
        stable_dumps(pair_payload).encode()).hexdigest()[:16]
    outcome_delta = {
        "control": {"outcome": control.outcome, "verdict": control.verifier.get("verdict")},
        "treatment": {"outcome": treatment.outcome, "verdict": treatment.verifier.get("verdict")},
    }
    receipt = InterventionReceipt(
        pair_id=pair_id,
        control_transition_id=control_transition_id,
        treatment_transition_id=treatment_transition_id,
        target_scope=scope,
        matched_context_digest=matched_context,
        changed_action_digest=action_digest(treatment.action),
        validity_status=validity,
        evidence_level=evidence_level,
        lineage_id=lineage_id or control.lineage_id,
        outcome_delta=outcome_delta,
        oracle_equivalence=oracle_equivalence)
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_intervention_pair_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        now = tehm_db.now_local()
        expected_row = {
            "pair_id": receipt.pair_id,
            "control_transition_id": receipt.control_transition_id,
            "treatment_transition_id": receipt.treatment_transition_id,
            "target_scope": receipt.target_scope,
            "matched_context_digest": receipt.matched_context_digest,
            "changed_action_digest": receipt.changed_action_digest,
            "outcome_delta_json": stable_dumps(receipt.outcome_delta),
            "oracle_equivalence_json": stable_dumps(receipt.oracle_equivalence),
            "lineage_id": receipt.lineage_id,
            "validity_status": receipt.validity_status,
        }
        existing = conn.execute(
            "SELECT pair_id, control_transition_id, treatment_transition_id, "
            "target_scope, matched_context_digest, changed_action_digest, "
            "outcome_delta_json, oracle_equivalence_json, lineage_id, "
            "validity_status FROM tehm_intervention_pairs WHERE pair_id=?",
            (receipt.pair_id,)).fetchone()
        if existing is not None:
            mismatches = [field for field, value in expected_row.items()
                          if existing[field] != value]
            if mismatches:
                raise ValueError(
                    "intervention pair replay conflicts with immutable pair "
                    f"{receipt.pair_id}: {', '.join(mismatches)}")
        else:
            conn.execute(
                """INSERT INTO tehm_intervention_pairs
                   (pair_id, control_transition_id, treatment_transition_id,
                    target_scope, matched_context_digest, changed_action_digest,
                    outcome_delta_json, oracle_equivalence_json, lineage_id,
                    validity_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt.pair_id, receipt.control_transition_id,
                 receipt.treatment_transition_id, receipt.target_scope,
                 receipt.matched_context_digest, receipt.changed_action_digest,
                 expected_row["outcome_delta_json"],
                 expected_row["oracle_equivalence_json"], receipt.lineage_id,
                 receipt.validity_status, now))
        causal_edge_id = None
        if valid:
            # Materialise a single L2 edge only after the pair has matched
            # source context, toolchain, oracle scope, lineage, and changed
            # action.  The pair itself remains a separate audit object.
            from .path_builder import build_transition_causal_fragment

            control_fragment = build_transition_causal_fragment(
                conn, control_transition_id, campaign_id=selected_campaign,
                commit=False)
            treatment_fragment = build_transition_causal_fragment(
                conn, treatment_transition_id, campaign_id=selected_campaign,
                commit=False)
            treatment_action = next(
                node for node in treatment_fragment.nodes
                if node.node_type == "ACTION")
            treatment_outcome = next(
                node for node in treatment_fragment.nodes
                if node.node_type == "ORACLE_OUTCOME")
            campaign = treatment_fragment.campaign_id
            learner = bool(control_fragment.learner_eligible and
                           treatment_fragment.learner_eligible)
            edge = CausalEdge(
                treatment_action.causal_node_id, "SUPPORTS",
                treatment_outcome.causal_node_id,
                CausalEvidenceLevel.L2_CONTROLLED_INTERVENTION.value,
                support={"pair_id": pair_id, "control": control_transition_id,
                         "treatment": treatment_transition_id},
                confidence={"controlled_pair": True},
                evidence_refs=(pair_id, control_transition_id,
                               treatment_transition_id),
                campaign_id=campaign, learner_eligible=learner)
            causal_edge_id = persist_edge(conn, edge)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    if commit and not had_outer_transaction:
        conn.commit()
    return InterventionReceipt(
        pair_id=receipt.pair_id,
        control_transition_id=receipt.control_transition_id,
        treatment_transition_id=receipt.treatment_transition_id,
        target_scope=receipt.target_scope,
        matched_context_digest=receipt.matched_context_digest,
        changed_action_digest=receipt.changed_action_digest,
        validity_status=receipt.validity_status,
        evidence_level=receipt.evidence_level,
        lineage_id=receipt.lineage_id,
        outcome_delta=receipt.outcome_delta,
        oracle_equivalence=receipt.oracle_equivalence,
        causal_edge_id=causal_edge_id)


__all__ = ["InterventionReceipt", "build_intervention_pair"]
