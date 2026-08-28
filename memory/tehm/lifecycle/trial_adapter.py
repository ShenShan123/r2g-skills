"""A/B trial adapter (design doc 20.11, 24.3, 26 Phase 9).

Backend-neutral trial subject:
    Arm A = control (cold-start / no rule)
    Arm B = forced TEHM rule activation

Judging is variance-aware over repeated trials (mirrors the legacy
``ab_runner.judge_repeated_ex`` LCB math). Verdicts land in ``tehm_trials`` and
the lifecycle transition in ``tehm_rule_status`` — never legacy tables.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from tehm import db as tehm_db
from tehm.ids import stable_dumps


@runtime_checkable
class TrialSubject(Protocol):
    subject_id: str
    status_version: int

    def instantiate_arm_a(self, context): ...
    def instantiate_arm_b(self, context): ...
    def evaluate(self, plan, context): ...   # returns {"success": bool, "metrics": dict}


@dataclass
class TEHMRuleTrialSubject:
    """Arm A = control; Arm B = force the TEHM rule activation."""

    rule_id: str
    status_version: int = 0

    @property
    def subject_id(self) -> str:
        return self.rule_id

    def instantiate_arm_a(self, context):
        return {"arm": "a", "control": True, "rule_id": None}

    def instantiate_arm_b(self, context):
        return {"arm": "b", "control": False, "rule_id": self.rule_id}

    def evaluate(self, plan, context):
        raise NotImplementedError(
            "evaluation is injected (the shared R2G executor/oracle base)")


def lcb(samples: list[float], z: float = 1.0) -> float:
    """Lower confidence bound of success (design doc 20.11, legacy LCB)."""
    if not samples:
        return 0.0
    mean = statistics.mean(samples)
    if len(samples) == 1:
        return mean - z * 0.5            # maximal uncertainty on one sample
    se = statistics.stdev(samples) / math.sqrt(len(samples))
    return mean - z * se


def judge_trial(arm_a_samples: list[float], arm_b_samples: list[float], *,
                z: float = 1.0) -> tuple[str, str]:
    """Variance-aware (verdict, reason) over k repeats (design doc 24.3)."""
    lcb_a, lcb_b = lcb(arm_a_samples, z), lcb(arm_b_samples, z)
    if lcb_b > lcb_a and any(arm_b_samples):
        return ("win", f"rule arm B LCB({lcb_b:.3f}) > control LCB({lcb_a:.3f})")
    if lcb_a > lcb_b:
        return ("loss", f"control LCB({lcb_a:.3f}) > rule arm B LCB({lcb_b:.3f})")
    return ("inconclusive", f"LCBs not separated (A={lcb_a:.3f}, B={lcb_b:.3f})")


def _check_deterministic_replay(conn: sqlite3.Connection, *,
                                trial_id: str, trial_uuid: str,
                                expected: dict) -> bool:
    """Reject conflicting writes for a deterministic trial identity.

    A retry with the same deterministic identity is allowed only when every
    persisted evidence field is identical.  This protects the authority ledger
    from silent replacement data loss, including for legacy UUID-less trials.
    Callers that enrich metrics after the initial evidence write continue to
    use an explicit UPDATE (the ORFS/RTL crash-recovery path).
    """
    existing = conn.execute(
        "SELECT * FROM tehm_trials WHERE trial_uuid=? OR trial_id=?",
        (trial_uuid or None, trial_id)).fetchone()
    if existing is None:
        return False
    mismatches = [
        key for key, value in expected.items()
        if existing[key] != value
    ]
    if mismatches:
        fields = ", ".join(mismatches)
        raise ValueError(
            f"trial evidence replay conflicts with immutable trial "
            f"{trial_uuid}: {fields}")
    return True


def run_trial(conn: sqlite3.Connection, *, subject: TrialSubject,
              context, arm_a_evaluator, arm_b_evaluator,
              repeats: int = 2, trial_uuid: str = "",
              commit: bool = True) -> dict:
    """Execute and judge one A/B trial over ``repeats`` rounds.

    ``arm_a_evaluator(plan, context)`` / ``arm_b_evaluator(plan, context)``:
    the injected shared-base evaluators (real flow in production, fakes in tests).
    Persists the verdict to ``tehm_trials``.
    """
    plan_a = subject.instantiate_arm_a(context)
    plan_b = subject.instantiate_arm_b(context)
    a_samples: list[float] = []
    b_samples: list[float] = []
    for _ in range(repeats):
        a_samples.append(1.0 if arm_a_evaluator(plan_a, context).get("success") else 0.0)
        b_samples.append(1.0 if arm_b_evaluator(plan_b, context).get("success") else 0.0)
    verdict, reason = judge_trial(a_samples, b_samples)

    trial = {
        "trial_id": f"trial_{trial_uuid}" if trial_uuid else
                    f"trial:{subject.subject_id}",
        "rule_id": subject.subject_id,
        "target_scope": str(getattr(context, "check", "") or "signoff"),
        "arm_a_samples": a_samples,
        "arm_b_samples": b_samples,
        "verdict": verdict,
        "reason": reason,
        "status_version": subject.status_version,
    }
    metrics_json = stable_dumps({"reason": reason,
                                 "arm_a_samples": a_samples,
                                 "arm_b_samples": b_samples})
    if _check_deterministic_replay(
            conn, trial_id=trial["trial_id"], trial_uuid=trial_uuid,
            expected={
                "trial_id": trial["trial_id"],
                "rule_id": trial["rule_id"],
                "target_scope": trial["target_scope"],
                "arm_a_run_id": None,
                "arm_b_run_id": None,
                "verdict": verdict,
                "metrics_json": metrics_json,
                "match_level": "exact",
                "trial_uuid": trial_uuid or None,
                "status_version": subject.status_version,
            }):
        return trial
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT INTO tehm_trials (
               trial_id, rule_id, target_scope, arm_a_run_id, arm_b_run_id,
               verdict, metrics_json, match_level, trial_uuid, status_version,
               created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trial["trial_id"], trial["rule_id"], trial["target_scope"],
         None, None, verdict, stable_dumps({"reason": reason,
                                            "arm_a_samples": a_samples,
                                            "arm_b_samples": b_samples}),
         "exact", trial_uuid or None, subject.status_version,
         tehm_db.now_local()))
    if commit and not had_outer_transaction:
        conn.commit()
    return trial


def record_external_trial(conn: sqlite3.Connection, *, rule_id: str,
                          target_scope: str, verdict: str, metrics: dict,
                          status_version: int, trial_uuid: str,
                          arm_a_run_id: str | None,
                          arm_b_run_id: str | None,
                          match_level: str = "exact",
                          commit: bool = True) -> dict:
    """Persist a trial executed by the shared real ORFS base.

    Unlike ``run_trial`` this function does not own execution; it records the
    already-observed arm evidence without translating it through legacy tables.
    ``trial_uuid`` is mandatory so crash/retry remains idempotent.
    """
    if not trial_uuid:
        raise ValueError("external TEHM trial requires deterministic trial_uuid")
    trial_id = f"trial_{trial_uuid}"
    metrics_json = stable_dumps(metrics)
    if _check_deterministic_replay(
            conn, trial_id=trial_id, trial_uuid=trial_uuid,
            expected={
                "trial_id": trial_id,
                "rule_id": rule_id,
                "target_scope": target_scope,
                "arm_a_run_id": arm_a_run_id,
                "arm_b_run_id": arm_b_run_id,
                "verdict": verdict,
                "metrics_json": metrics_json,
                "match_level": match_level,
                "trial_uuid": trial_uuid,
                "status_version": status_version,
            }):
        return {"trial_id": trial_id, "rule_id": rule_id,
                "target_scope": target_scope, "verdict": verdict,
                "trial_uuid": trial_uuid, "status_version": status_version,
                "metrics": metrics}
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT INTO tehm_trials (
               trial_id, rule_id, target_scope, arm_a_run_id, arm_b_run_id,
               verdict, metrics_json, match_level, trial_uuid, status_version,
               created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trial_id, rule_id, target_scope, arm_a_run_id, arm_b_run_id,
         verdict, stable_dumps(metrics), match_level, trial_uuid,
         status_version, tehm_db.now_local()))
    if commit and not had_outer_transaction:
        conn.commit()
    return {"trial_id": trial_id, "rule_id": rule_id,
            "target_scope": target_scope, "verdict": verdict,
            "trial_uuid": trial_uuid, "status_version": status_version,
            "metrics": metrics}
