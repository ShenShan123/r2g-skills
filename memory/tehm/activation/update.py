"""Step 8: update the experience graph (design doc 10, 19.5).

On a real executed + verified activation:
  * the produced transition is captured into the canonical store (a NEW verified
    transition feeds the next crystallization round),
  * the ``tehm_activations`` row is persisted (the runtime authority),
  * the rule's utility is updated (activations / positive / neutral / harmful).
"""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.canonical.verifier import VerifierSnapshot
from tehm.ids import stable_dumps

UPDATE_VERSION = "update-v0.1"


def capture_produced_transition(conn: sqlite3.Connection, store, *,
                                activation_id: str, context, action: dict,
                                execution: dict, verification: dict,
                                authority_mode: str = "production") -> str:
    """Capture the verified transition an activation produced."""
    evidence = _verifier_snapshot(verification)
    delta = execution.get("observation_delta") or _delta_from(execution, verification)
    before_state = dict(execution.get("before_state") or {})
    after_state = dict(execution.get("after_state") or {})
    # An executor may return a post-edit graph itself.  When it does not, keep
    # the target context graph attached to both snapshots rather than silently
    # dropping the structural proof at the activation boundary.
    if getattr(context, "structural_graph", None) is not None:
        before_state.setdefault("structural_graph", context.structural_graph)
        after_state.setdefault("structural_graph", context.structural_graph)
    record = ExecutionRecord(
        record_id=f"activation:{activation_id}",
        domain="flow.signoff",
        project_id=context.design_id,
        design_id=context.design_id,
        lineage_id=context.design_id,
        repository_ref=None,
        before=before_state,
        action=action,
        after=after_state,
        observation_delta=delta,
        verification=evidence,
        episode={"episode_id": f"act_ep:{activation_id}",
                 "mechanism_family": action.get("transformation_family"),
                 "lineage_id": context.design_id,
                 "step_index": 0,
                 "terminal_status":
                     "VERIFIED_REPAIR" if evidence.get("verdict") == "PASS" else "PARTIAL"},
    )
    # Activation-time evaluation copies must never become learner support.
    # Production promotion is the only authority allowed to feed live
    # training; audit/evaluation receipts stay in an explicit AB split.
    receipt = capture(
        conn, store, record,
        dataset_campaign_id=("live" if authority_mode == "production"
                             else f"activation-{authority_mode}"),
        dataset_split=("training" if authority_mode == "production" else "ab"),
        dataset_learner_eligible=(authority_mode == "production"))
    # Online evolution is an explicit shadow observation after canonical
    # capture.  It emits causal/event receipts but never changes rule status or
    # production authority.
    from tehm.evolution.manager import observe_transition
    observe_transition(
        conn, receipt.transition_id,
        campaign_id=("live" if authority_mode == "production"
                     else f"activation-{authority_mode}"))
    return receipt.transition_id


def persist_activation(conn: sqlite3.Connection, record, *, commit: bool = True) -> None:
    """Write an activation receipt or accept an exact content replay.

    Activation IDs are deterministic, so ``INSERT OR REPLACE`` could erase a
    prior verification/binding result when a retry reused the ID with changed
    content.  Trial reconciliation may still update its explicitly mutable
    verifier/rollback columns elsewhere; this entry point never overwrites an
    existing receipt silently.
    """
    had_outer_transaction = conn.in_transaction
    values = (
        record.activation_id, record.rule_id, record.target_state_id,
        stable_dumps(record.query_plan), stable_dumps(record.retrieval_receipt),
        record.applicability_status, record.predicate_snapshot_id,
        record.binding_status, stable_dumps(record.binding),
        record.executability_status,
        stable_dumps(record.obligation_transfer), record.obligation_coverage,
        record.verification_status, stable_dumps(record.verifier),
        record.outcome, stable_dumps(record.created_regressions),
        record.produced_transition_id,
        stable_dumps(record.rollback_receipt) if record.rollback_receipt else None,
        record.trial_uuid, record.created_at)
    columns = ("activation_id", "rule_id", "target_state_id", "query_plan_json",
               "retrieval_receipt_json", "applicability_status",
               "predicate_snapshot_id", "binding_status", "binding_json",
               "executability_status", "obligation_transfer_json",
               "obligation_coverage", "verification_status", "verifier_json",
               "outcome", "created_regressions_json", "produced_transition_id",
               "rollback_receipt_json", "trial_uuid", "created_at")
    existing = conn.execute(
        "SELECT " + ", ".join(columns) +
        " FROM tehm_activations WHERE activation_id=?",
        (record.activation_id,)).fetchone()
    if existing is not None:
        # created_at is an audit timestamp, not activation identity.
        mismatches = [column for column, value in zip(columns, values)
                      if column != "created_at" and existing[column] != value]
        if mismatches:
            raise ValueError(
                "activation replay conflicts with immutable receipt "
                f"{record.activation_id}: {', '.join(mismatches)}")
    else:
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            "INSERT INTO tehm_activations (" + ", ".join(columns) +
            ") VALUES (" + placeholders + ")", values)
    if commit and not had_outer_transaction:
        conn.commit()


def update_rule_utility(conn: sqlite3.Connection, rule_id: str, outcome: str,
                        *, activation_id: str | None = None,
                        campaign_id: str = "activation-backend",
                        learner_eligible: bool = False,
                        created_regressions: list | tuple = (),
                        commit: bool = True) -> None:
    """Bump utility and emit an activation-feedback event when identified.

    Utility is an accumulated derived field; the event is the append-only
    provenance needed by the online evolution lane.  Feedback without an
    activation identifier keeps the historical helper behavior but does not
    fabricate an event source.  Evaluation/backend seams default to a
    non-learner campaign so feedback cannot silently become learner support.
    """
    from tehm.canonical.transition import HARMFUL_OUTCOMES, NEUTRAL_OUTCOMES, POSITIVE_OUTCOMES

    # A learner-eligible utility update is derived runtime state, not a
    # free-standing counter increment. Require an activation receipt to name
    # one canonical transition and replay its complete executable oracle
    # before touching the rule.
    if learner_eligible:
        if not activation_id or type(activation_id) is not str:
            raise ValueError(
                "learner-eligible utility feedback requires activation_id")
        activation = conn.execute(
            "SELECT produced_transition_id FROM tehm_activations "
            "WHERE activation_id=?", (activation_id,)).fetchone()
        transition_id = (activation["produced_transition_id"]
                         if activation is not None else None)
        if not transition_id:
            raise ValueError(
                "learner-eligible utility feedback requires a produced "
                "canonical transition")
        from tehm.verified_execution import require_verified_transition
        require_verified_transition(conn, str(transition_id))

    if activation_id and outcome != "UNKNOWN":
        prior_feedback = conn.execute(
            """SELECT 1 FROM tehm_memory_events
                WHERE source_type='activation' AND source_id=?
                  AND event_type IN ('SUPPORT_INCREASED', 'UTILITY_DRIFT',
                                     'RULE_HARMFUL') LIMIT 1""",
            (activation_id,)).fetchone()
        if prior_feedback is not None:
            return

    row = conn.execute("SELECT utility_json FROM tehm_rules WHERE rule_id=?",
                       (rule_id,)).fetchone()
    utility = tehm_db.read_json(row["utility_json"]) if row else {}
    utility_before = dict(utility)
    utility["activations"] = int(utility.get("activations") or 0) + 1
    if outcome in POSITIVE_OUTCOMES:
        utility["positive"] = int(utility.get("positive") or 0) + 1
    elif outcome in NEUTRAL_OUTCOMES:
        utility["neutral"] = int(utility.get("neutral") or 0) + 1
    elif outcome in HARMFUL_OUTCOMES:
        utility["harmful"] = int(utility.get("harmful") or 0) + 1
    # Utility and its append-only feedback event form one derived update.  A
    # failed event write (for example a missing activation witness) must not
    # leave counters advanced without the provenance needed to replay them.
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_activation_utility_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        conn.execute("UPDATE tehm_rules SET utility_json=? WHERE rule_id=?",
                     (stable_dumps(utility), rule_id))
        if activation_id and outcome != "UNKNOWN":
            from tehm.evolution.events import append_memory_event

            if outcome in HARMFUL_OUTCOMES or created_regressions:
                event_type = "RULE_HARMFUL"
            elif outcome in POSITIVE_OUTCOMES:
                event_type = "SUPPORT_INCREASED"
            else:
                event_type = "UTILITY_DRIFT"
            append_memory_event(
                conn, event_type=event_type, source_type="activation",
                source_id=activation_id, campaign_id=campaign_id,
                learner_eligible=learner_eligible,
                payload={"rule_id": rule_id, "outcome": outcome,
                         "utility_before": utility_before,
                         "utility_after": dict(utility),
                         "created_regressions": list(created_regressions)},
                commit=False)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        # ``commit`` retains the historical default for direct callers;
        # callers composing a larger transaction can explicitly defer it.
        if commit and not had_outer_transaction:
            conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


# -- helpers ------------------------------------------------------------------

def _verifier_snapshot(verification: dict) -> dict:
    snapshot = VerifierSnapshot.from_oracle_result(verification)
    out = snapshot.to_dict()
    out["created_regressions"] = verification.get("created_regressions") or []
    out["newly_observed_failures"] = verification.get("newly_observed_failures") or []
    return out


def _delta_from(execution: dict, verification: dict) -> dict:
    """Derive an observation delta when the executor did not supply one."""
    before = execution.get("before_state") or {}
    after = execution.get("after_state") or {}
    counts_before = _failure_count(before)
    counts_after = _failure_count(after)
    return {
        "original_failure": "REMOVED" if verification.get("verdict") == "PASS"
                            else "UNKNOWN",
        "first_divergence": {"before": counts_before, "after": counts_after},
        "failing_tests": {"before": 1 if counts_before else 0,
                          "after": 1 if counts_after else 0},
        "created_regressions": verification.get("created_regressions") or [],
        "newly_observed_failures": verification.get("newly_observed_failures") or [],
    }


def _failure_count(state: dict) -> int | None:
    reports = state.get("reports") or {}
    for report in reports.values():
        total = report.get("total_violations")
        if total is not None:
            return int(total)
    return None
