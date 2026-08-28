"""Eight-step activation orchestrator (design doc 10, 21.3, 11).

Step 1 (retrieve) is Phase 7; this pipeline takes an admissible rule_id and runs
steps 2-8. The three activation-time axes — Applicable, Executable, Verifiable —
are stored SEPARATELY (design doc 11) and never collapsed into one flag.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field

from contracts import RepairContext
from tehm import db as tehm_db
from tehm.activation.applicability import check_applicability
from tehm.activation.binding import bind_rule
from tehm.activation.execute_adapter import execute_action
from tehm.activation.instantiate import instantiate_rewrite
from tehm.activation.obligation_transfer import (
    finalize_obligations, transfer_obligations)
from tehm.activation.update import (
    capture_produced_transition,
    persist_activation,
    update_rule_utility,
)
from tehm.activation.verify import verify_execution
from tehm.ids import stable_dumps
from tehm.retrieval.index import build_index
from tehm.retrieval.query_planner import plan_query
from tehm.retrieval.result import APPLICABLE

ACTIVATION_VERSION = "activation-v0.1"


class ActivationError(RuntimeError):
    pass


@dataclass
class ActivationRecord:
    """The runtime activation authority (design doc 11, 19.5).

    Applicable / Executable / Verifiable live in SEPARATE fields.
    """

    activation_id: str
    rule_id: str
    target_state_id: str
    query_plan: dict = field(default_factory=dict)
    retrieval_receipt: dict = field(default_factory=dict)
    applicability_status: str = "UNRESOLVED"
    predicate_snapshot_id: str | None = None
    binding_status: str = "UNRESOLVED"
    binding: dict = field(default_factory=dict)
    executability_status: str = "NOT_EXECUTABLE"
    obligation_transfer: dict = field(default_factory=dict)
    obligation_coverage: float | None = None
    verification_status: str = "UNKNOWN"
    verifier: dict = field(default_factory=dict)
    outcome: str = "UNKNOWN"
    created_regressions: list = field(default_factory=list)
    produced_transition_id: str | None = None
    rollback_receipt: dict | None = None
    trial_uuid: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "activation_id": self.activation_id,
            "rule_id": self.rule_id,
            "target_state_id": self.target_state_id,
            "query_plan": self.query_plan,
            "retrieval_receipt": self.retrieval_receipt,
            "applicability_status": self.applicability_status,
            "predicate_snapshot_id": self.predicate_snapshot_id,
            "binding_status": self.binding_status,
            "binding": self.binding,
            "executability_status": self.executability_status,
            "obligation_transfer": self.obligation_transfer,
            "obligation_coverage": self.obligation_coverage,
            "verification_status": self.verification_status,
            "verifier": self.verifier,
            "outcome": self.outcome,
            "created_regressions": list(self.created_regressions),
            "produced_transition_id": self.produced_transition_id,
            "rollback_receipt": self.rollback_receipt,
            "trial_uuid": self.trial_uuid,
            "created_at": self.created_at,
        }


def activate(conn: sqlite3.Connection, store, *, rule_id: str,
             context: RepairContext, provided_binding: dict | None = None,
             executor=None, oracle=None, oracle_registry: set | None = None,
             capture_evidence: bool = True, dry_run: bool = False,
             allow_invalid: bool = False,
             authority_mode: str = "production",
             trial_uuid: str | None = None) -> ActivationRecord:
    """Run steps 2-8 of the activation pipeline for one admissible rule."""
    # ``allow_invalid`` exists solely for controlled ablation experiments that
    # intentionally remove the validity gate.  Runtime callers must leave the
    # default False; no production backend passes this flag.
    if authority_mode not in {"production", "evaluation", "audit"}:
        raise ValueError(f"unknown activation authority_mode: {authority_mode!r}")
    lifecycle_statuses = (frozenset({"promoted"})
                          if authority_mode == "production" else None)
    index = build_index(conn, lifecycle_statuses=lifecycle_statuses,
                        require_validity=not allow_invalid)
    rule = index.get(rule_id)
    if rule is None:
        raise ActivationError(
            f"rule not found or not admissible (PROVISIONAL_VALID/VALIDATED): {rule_id}")

    created_at = tehm_db.now_local()
    query = plan_query(context)
    target_state_id = _target_state_id(context)

    # Step 2: applicability
    applicability = check_applicability(rule, context)

    # Step 3: structural binding
    binding = bind_rule(rule, context, provided_binding=provided_binding)

    # Step 4: obligation transfer
    obligations = transfer_obligations(rule, context, oracle_registry=oracle_registry)

    # Step 5: instantiate the rewrite
    action = instantiate_rewrite(rule, binding, context)

    # Executable (design doc 11): applicable AND bound AND obligations transferable.
    executable = (applicability == APPLICABLE and binding.status == "BOUND")
    executability_status = "EXECUTABLE" if executable else "NOT_EXECUTABLE"

    # Step 6: sandbox execute (via the injected R2G execution base)
    execution = None
    if not dry_run and executable:
        execution = execute_action(action, context, executor=executor)

    # Step 7: oracle verify
    verification = verify_execution(execution, rule.get("obligations") or [],
                                    oracle=oracle)
    obligations = finalize_obligations(obligations, verification)
    oc = obligations["obligation_coverage"]
    verification_status = verification.get("verdict", "UNKNOWN")

    # Step 8: update (capture new transition + persist + utility)
    produced_transition_id = None
    if (not dry_run and execution is not None
            and verification_status in ("PASS", "FAIL") and capture_evidence):
        produced_transition_id = capture_produced_transition(
            conn, store, activation_id=_activation_id(rule_id, target_state_id,
                                                       context),
            context=context, action=action, execution=execution,
            verification=verification, authority_mode=authority_mode)

    outcome = _outcome(verification)
    created_regressions = list(verification.get("created_regressions") or [])
    record = ActivationRecord(
        activation_id=_activation_id(rule_id, target_state_id, context),
        rule_id=rule_id,
        target_state_id=target_state_id,
        query_plan=query.query_plan,
        retrieval_receipt={"source": "tehm_rule", "rule_id": rule_id},
        applicability_status=applicability,
        binding_status=binding.status,
        binding=binding.to_dict(),
        executability_status=executability_status,
        obligation_transfer=obligations,
        obligation_coverage=oc,
        verification_status=verification_status,
        verifier=verification,
        outcome=outcome,
        created_regressions=created_regressions,
        produced_transition_id=produced_transition_id,
        trial_uuid=trial_uuid,
        created_at=created_at,
    )
    if not dry_run:
        # Activation authority, utility and feedback are one derived update.
        # Keep the caller's outer transaction intact; if feedback persistence
        # fails, the activation row must not be left as a committed prefix.
        had_outer_transaction = conn.in_transaction
        savepoint = "tehm_activation_update_v1"
        conn.execute(f"SAVEPOINT {savepoint}")
        savepoint_active = True
        try:
            persist_activation(conn, record, commit=False)
            update_rule_utility(
                conn, rule_id, outcome, activation_id=record.activation_id,
                campaign_id=("live" if authority_mode == "production"
                             else f"activation-{authority_mode}"),
                learner_eligible=(authority_mode == "production"),
                created_regressions=created_regressions, commit=False)
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_active = False
            if not had_outer_transaction:
                conn.commit()
        except Exception:
            if savepoint_active:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
    return record


def _outcome(verification: dict) -> str:
    if verification.get("created_regressions"):
        return "REGRESSION"
    verdict = verification.get("verdict")
    if verdict == "FAIL":
        return "FAIL"
    if verdict == "PASS":
        return "PASS"
    return "UNKNOWN"


def _target_state_id(context: RepairContext) -> str:
    payload = stable_dumps({
        "design": context.design_id,
        "platform": context.platform,
        "check": context.check,
        "cfg": context.cfg,
    })
    return f"target_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def _activation_id(rule_id: str, target_state_id: str, context: RepairContext) -> str:
    payload = stable_dumps({"rule": rule_id, "target": target_state_id,
                            "check": context.check})
    return f"act_{hashlib.sha1(payload.encode()).hexdigest()[:16]}"
