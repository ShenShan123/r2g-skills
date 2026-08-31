"""Deterministic, shadow-only Experience Value selection.

Experience Value is an information-priority layer between immutable verified
transitions and derived-memory updates.  It never deletes evidence, changes a
legacy trigger result, mutates canonical rows, or grants lifecycle authority.
The implementation deliberately uses typed execution/database witnesses; it
does not ask a model to assign a score.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping

from tehm.canonical.transition import HARMFUL_OUTCOMES
from tehm.causal.mechanism import TransitionFacts, action_digest, load_transition_facts
from tehm.dataset import validate_membership_row
from tehm import db as tehm_db
from tehm.ids import stable_dumps
from tehm.assets.registry import get_asset
from tehm.verified_execution import require_verified_execution

from .conflict import ConflictReceipt, detect_conflicts
from .novelty import detect_novelty
from .receipts import ExperienceValueReceipt
from .triggers import (ConsolidationTriggerReceipt,
                        evaluate_consolidation_trigger)


EXPERIENCE_VALUE_VERSION = "experience-value-v0.1"
VALUE_WEIGHTS = {
    "novelty": 0.15,
    "severity": 0.20,
    "capability_gap": 0.15,
    "causal_discrimination": 0.10,
    "surprise": 0.10,
    "counterexample": 0.15,
    "memory_interference": 0.15,
    "redundancy": -0.15,
}

VALUE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tehm_experience_values (
    transition_id          TEXT NOT NULL,
    campaign_id            TEXT NOT NULL,
    novelty                REAL NOT NULL,
    severity               REAL NOT NULL,
    capability_gap        REAL NOT NULL,
    causal_discrimination REAL NOT NULL,
    surprise               REAL NOT NULL,
    counterexample         REAL NOT NULL,
    memory_interference    REAL NOT NULL,
    redundancy             REAL NOT NULL,
    value_score            REAL NOT NULL,
    priority               TEXT NOT NULL,
    update_layers_json     TEXT NOT NULL,
    receipt_json           TEXT NOT NULL,
    receipt_digest         TEXT NOT NULL UNIQUE,
    created_at             TEXT NOT NULL,
    PRIMARY KEY (transition_id, campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_experience_values_priority
    ON tehm_experience_values(campaign_id, priority, value_score);
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def ensure_experience_value_schema(
        conn: sqlite3.Connection, *, commit: bool = True) -> None:
    """Create the additive value table without changing the v4 meta version."""
    had_outer_transaction = conn.in_transaction
    # executescript() commits an outer transaction.  Individual statements
    # keep a value receipt atomic with the caller's observation savepoint.
    for statement in (item.strip() for item in VALUE_SCHEMA_SQL.split(";")
                      if item.strip()):
        conn.execute(statement)
    if commit and not had_outer_transaction:
        conn.commit()


def _strict_json(raw: object, field: str, expected: type) -> object:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"experience value {field} is malformed JSON") from exc
    if not isinstance(value, expected):
        raise ValueError(
            f"experience value {field} must decode to {expected.__name__}")
    return value


def _unit(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"experience value {field} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"experience value {field} must be finite in [0, 1]")
    return value


def _membership(conn: sqlite3.Connection, transition_id: str,
                campaign_id: str) -> tuple[bool, str]:
    row = conn.execute(
        "SELECT learner_eligible, split FROM tehm_dataset_membership "
        "WHERE transition_id=? AND campaign_id=?", (transition_id, campaign_id)
    ).fetchone()
    if row is None:
        raise ValueError("experience value requires explicit dataset membership")
    try:
        eligible, split = validate_membership_row(row)
    except ValueError as exc:
        # Preserve the existing audit-only compatibility rule for old/direct
        # SQL rows that marked a non-training split learner-eligible.
        if str(exc) != "non-training dataset membership cannot be learner-eligible":
            raise
        return False, str(row["split"])
    return bool(eligible and split == "training"), str(split)


def _harmful(facts: TransitionFacts) -> bool:
    delta = facts.delta
    return bool(
        facts.outcome in HARMFUL_OUTCOMES
        or facts.verifier.get("verdict") == "FAIL"
        or delta.get("created_regressions")
        or delta.get("newly_observed_failures")
        or delta.get("utility_verdict") == "HARMFUL"
    )


def _severity(facts: TransitionFacts) -> float:
    delta = facts.delta
    if facts.outcome == "REGRESSION" or delta.get("created_regressions"):
        return 1.0
    if delta.get("utility_verdict") == "HARMFUL":
        return 0.9
    if facts.verifier.get("verdict") == "FAIL" or facts.outcome == "FAIL":
        return 0.8
    if delta.get("newly_observed_failures"):
        return 0.7
    if facts.outcome == "PARTIAL":
        return 0.35
    return 0.0


def _rule_matches(facts: TransitionFacts, row: sqlite3.Row) -> bool:
    context = _strict_json(row["context_profile_json"], "rule context", dict)
    before = _strict_json(row["before_pattern_json"], "rule before pattern", dict)
    profile = context.get("compatibility_profile")
    if facts.compatibility_profile is not None and profile not in {
            None, facts.compatibility_profile}:
        return False
    family = before.get("type")
    action_family = facts.action.get("transformation_family")
    if family is not None and action_family is not None and family != action_family:
        return False
    return True


def _asset_matches(facts: TransitionFacts, asset: Mapping) -> bool:
    compatibility = asset.get("compatibility")
    if not isinstance(compatibility, Mapping):
        raise ValueError("experience value asset compatibility is malformed")
    profile = compatibility.get("compatibility_profile")
    if profile is None:
        profile = compatibility.get("profile")
    if facts.compatibility_profile is not None and profile not in {
            None, facts.compatibility_profile}:
        return False
    family = compatibility.get("transformation_family")
    if family is None:
        definition = asset.get("definition")
        if isinstance(definition, Mapping):
            family = definition.get("transformation_family")
    action_family = facts.action.get("transformation_family")
    return family is None or family == action_family


def _has_promoted_support(conn: sqlite3.Connection, facts: TransitionFacts) -> bool:
    """Find a promoted rule or asset applicable to this transition."""
    if _table_exists(conn, "tehm_rule_status"):
        rows = conn.execute(
            """SELECT r.*, rs.target_scope, rs.status
                 FROM tehm_rules r
                 JOIN tehm_rule_status rs ON rs.rule_id=r.rule_id
                WHERE rs.status='promoted'"""
        ).fetchall()
        for row in rows:
            if row["target_scope"] not in {"global", "", None}:
                # A scope-specific rule is still useful when its content
                # carries the requested compatibility profile; otherwise it
                # cannot be proven applicable to this transition.
                context = _strict_json(
                    row["context_profile_json"], "rule context", dict)
                if (facts.compatibility_profile is None or
                        context.get("compatibility_profile") not in {
                            None, facts.compatibility_profile}):
                    continue
            if _rule_matches(facts, row):
                return True
    if _table_exists(conn, "tehm_asset_status"):
        for row in conn.execute(
                "SELECT asset_id, target_scope FROM tehm_asset_status "
                "WHERE status='promoted'"):
            asset = get_asset(conn, str(row["asset_id"]))
            if asset is None:
                raise ValueError("experience value promoted asset is invalid")
            if _asset_matches(facts, asset):
                return True
    return False


def _promoted_counterexample(conn: sqlite3.Connection, facts: TransitionFacts) -> bool:
    if (not _harmful(facts) or not _table_exists(conn, "tehm_activations")
            or not _table_exists(conn, "tehm_rule_status")):
        return False
    rows = conn.execute(
        """SELECT a.activation_id, a.rule_id
             FROM tehm_activations a
             JOIN tehm_rule_status rs ON rs.rule_id=a.rule_id
            WHERE a.produced_transition_id=? AND rs.status='promoted'""",
        (facts.transition_id,)).fetchall()
    return bool(rows)


def _walk_signal(value: object, keys: frozenset[str]) -> list[object]:
    found: list[object] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in keys:
                found.append(item)
            found.extend(_walk_signal(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_signal(item, keys))
    return found


def _activation_payloads(conn: sqlite3.Connection, transition_id: str) -> list[dict]:
    if not _table_exists(conn, "tehm_activations"):
        return []
    rows = conn.execute(
        """SELECT retrieval_receipt_json, query_plan_json, verifier_json,
                         outcome FROM tehm_activations
                WHERE produced_transition_id=?""", (transition_id,)).fetchall()
    payloads: list[dict] = []
    for row in rows:
        payload: dict = {"outcome": row["outcome"]}
        for field in ("retrieval_receipt_json", "query_plan_json", "verifier_json"):
            raw = row[field]
            if raw is None:
                continue
            payload[field] = _strict_json(raw, f"activation {field}", dict)
        payloads.append(payload)
    return payloads


def _memory_interference(
        conn: sqlite3.Connection, facts: TransitionFacts,
        explicit: bool | None) -> float:
    if explicit is not None and type(explicit) is not bool:
        raise ValueError("experience value memory_interference must be boolean")
    harmful = _harmful(facts)
    if not harmful:
        return 1.0 if explicit is True else 0.0
    for payload in _activation_payloads(conn, facts.transition_id):
        direct = _walk_signal(payload, frozenset({"memory_interference"}))
        if any(value is True for value in direct):
            return 1.0
        no_memory = _walk_signal(
            payload, frozenset({"no_memory_outcome", "without_memory_outcome",
                                "ablation_without_memory"}))
        if any(value == "PASS" for value in no_memory):
            return 1.0
    return 1.0 if explicit is True else 0.0


def _prediction_candidates(conn: sqlite3.Connection, facts: TransitionFacts) -> list[str]:
    candidates: list[str] = []
    keys = frozenset({"predicted_outcome", "expected_outcome",
                      "predicted_verdict", "expected_verdict"})
    rows = conn.execute(
        "SELECT provenance_json FROM tehm_transitions WHERE transition_id=?",
        (facts.transition_id,)).fetchall()
    payloads: list[object] = [facts.action.get("payload")]
    for row in rows:
        payloads.append(_strict_json(row[0], "transition provenance", dict))
    for payload in _activation_payloads(conn, facts.transition_id):
        payloads.append(payload)
    for payload in payloads:
        for value in _walk_signal(payload, keys):
            if type(value) is not str or not value:
                raise ValueError("experience value prediction signal is malformed")
            candidates.append(value)
    return candidates


def _surprise(conn: sqlite3.Connection, facts: TransitionFacts,
              predicted_outcome: str | None) -> float:
    if predicted_outcome is not None:
        if type(predicted_outcome) is not str or not predicted_outcome:
            raise ValueError("experience value predicted_outcome is malformed")
        candidates = [predicted_outcome]
    else:
        candidates = _prediction_candidates(conn, facts)
    if not candidates:
        return 0.0
    actual = {facts.outcome, str(facts.verifier.get("verdict") or "")}
    return 1.0 if any(candidate not in actual for candidate in candidates) else 0.0


def _causal_discrimination(conn: sqlite3.Connection, facts: TransitionFacts,
                           conflict: ConflictReceipt) -> float:
    if _table_exists(conn, "tehm_intervention_pairs"):
        row = conn.execute(
            """SELECT 1 FROM tehm_intervention_pairs
                WHERE (control_transition_id=? OR treatment_transition_id=?)
                  AND validity_status='VALID_CONTROLLED_PAIR' LIMIT 1""",
            (facts.transition_id, facts.transition_id)).fetchone()
        if row is not None:
            return 1.0
    if conflict.has_conflict and conflict.evidence_transition_ids:
        # A typed, same-family competing outcome is useful discrimination
        # evidence even before a controlled pair is available, but remains
        # below the controlled-intervention score.
        return 0.5
    return 0.0


def _redundancy(conn: sqlite3.Connection, facts: TransitionFacts,
                campaign_id: str) -> float:
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
             JOIN tehm_dataset_membership dm ON dm.transition_id=t.transition_id
            WHERE dm.campaign_id=? AND dm.split='training'
              AND dm.learner_eligible=1 AND t.transition_id<>?
            ORDER BY t.transition_id""", (campaign_id, facts.transition_id)
    ).fetchall()
    matches = 0
    for row in rows:
        other = load_transition_facts(conn, str(row["transition_id"]))
        if (other.mechanism_family == facts.mechanism_family
                and other.compatibility_profile == facts.compatibility_profile
                and other.outcome == facts.outcome
                and other.primary_effect_key == facts.primary_effect_key
                and action_digest(other.action) == facts.action_digest):
            matches += 1
    if matches >= 2:
        return 1.0
    if matches == 1:
        return 0.5
    return 0.0


def _normalise_signal_result(result: Mapping | None, *, transition_id: str,
                             campaign_id: str) -> Mapping:
    if result is None:
        return {}
    if not isinstance(result, Mapping):
        raise ValueError("experience value novelty result must be a mapping")
    for key, expected in (("transition_id", transition_id),
                          ("campaign_id", campaign_id)):
        if key in result and result[key] != expected:
            raise ValueError("experience value signal identity mismatch")
    return result


def _priority(*, value_score: float, severity: float, counterexample: float,
              interference: float) -> str:
    if severity >= 1.0 or counterexample >= 1.0 or interference >= 1.0:
        return "P0_CRITICAL"
    if value_score >= 0.5:
        return "P1_HIGH"
    if value_score >= 0.2:
        return "P2_MEDIUM"
    return "P3_LOW"


def _layers(*, learner_eligible: bool, novelty: float, severity: float,
            capability_gap: float, discrimination: float, surprise: float,
            counterexample: float, interference: float,
            trigger: ConsolidationTriggerReceipt | None) -> tuple[str, ...]:
    if not learner_eligible:
        return ("NONE",)
    selected: list[str] = []
    if novelty or severity or counterexample or interference:
        selected.append("STATE")
    if novelty or discrimination or surprise:
        selected.append("CAUSAL")
    if trigger is not None and trigger.triggered:
        selected.append("RULE")
    if capability_gap:
        selected.extend(("ASSET", "CAPABILITY"))
    return tuple(selected) if selected else ("NONE",)


def evaluate_experience_value(
    conn: sqlite3.Connection, transition_id: str, *, campaign_id: str = "live",
    novelty_result: Mapping | None = None,
    conflict: ConflictReceipt | None = None,
    trigger: ConsolidationTriggerReceipt | None = None,
    memory_interference: bool | None = None,
    predicted_outcome: str | None = None,
) -> ExperienceValueReceipt:
    """Compute one deterministic value receipt without writing any row."""
    if type(transition_id) is not str or not transition_id:
        raise ValueError("experience value transition_id is required")
    if type(campaign_id) is not str or not campaign_id:
        raise ValueError("experience value campaign_id is required")
    facts = load_transition_facts(conn, transition_id)
    learner_eligible, _split = _membership(conn, transition_id, campaign_id)
    if learner_eligible:
        require_verified_execution(facts)

    novelty_data = _normalise_signal_result(
        novelty_result or detect_novelty(conn, transition_id,
                                         campaign_id=campaign_id),
        transition_id=transition_id, campaign_id=campaign_id)
    novelty_status = novelty_data.get("status")
    if novelty_status not in {"NOVEL_MECHANISM", "KNOWN_MECHANISM"}:
        raise ValueError("experience value novelty status is invalid")
    novelty = 1.0 if novelty_status == "NOVEL_MECHANISM" else 0.0

    if conflict is None:
        conflict = detect_conflicts(conn, transition_id, campaign_id=campaign_id)
    if (not isinstance(conflict, ConflictReceipt)
            or conflict.transition_id != transition_id
            or conflict.campaign_id != campaign_id):
        raise ValueError("experience value conflict receipt identity mismatch")
    if trigger is not None:
        if (not isinstance(trigger, ConsolidationTriggerReceipt)
                or trigger.transition_id != transition_id
                or trigger.campaign_id != campaign_id):
            raise ValueError("experience value trigger receipt identity mismatch")
    else:
        # Standalone value evaluation must replay the same legacy decision as
        # observe_transition().  This is read-only and deliberately does not
        # replace or mutate the trigger receipt; it only makes update-layer
        # selection deterministic across both call paths.
        trigger = evaluate_consolidation_trigger(
            conn, transition_id, campaign_id=campaign_id,
            learner_eligible=learner_eligible,
            novelty=str(novelty_status), conflict=conflict)

    severity = _severity(facts)
    capability_gap = 0.0 if _has_promoted_support(conn, facts) else 1.0
    discrimination = _causal_discrimination(conn, facts, conflict)
    surprise = _surprise(conn, facts, predicted_outcome)
    counterexample = 1.0 if _promoted_counterexample(conn, facts) else 0.0
    interference = _memory_interference(conn, facts, memory_interference)
    redundancy = _redundancy(conn, facts, campaign_id)

    raw_score = sum(VALUE_WEIGHTS[name] * value for name, value in {
        "novelty": novelty, "severity": severity,
        "capability_gap": capability_gap,
        "causal_discrimination": discrimination, "surprise": surprise,
        "counterexample": counterexample,
        "memory_interference": interference, "redundancy": redundancy,
    }.items())
    value_score = round(min(1.0, max(0.0, raw_score)), 6)
    priority = _priority(value_score=value_score, severity=severity,
                         counterexample=counterexample,
                         interference=interference)

    reason_pairs = (
        ("NOT_LEARNER_ELIGIBLE", not learner_eligible),
        ("NOVEL_MECHANISM", novelty > 0),
        ("CATASTROPHIC_REGRESSION", severity >= 1.0),
        ("HARMFUL_EXECUTION", 0 < severity < 1.0),
        ("CAPABILITY_GAP", capability_gap > 0),
        ("CAUSAL_DISCRIMINATION", discrimination > 0),
        ("PREDICTION_SURPRISE", surprise > 0),
        ("PROMOTED_MEMORY_COUNTEREXAMPLE", counterexample > 0),
        ("MEMORY_INTERFERENCE", interference > 0),
        ("REDUNDANT_EXPERIENCE", redundancy > 0),
    )
    reasons = tuple(name for name, enabled in reason_pairs if enabled)
    if not reasons or (len(reasons) == 1 and reasons == ("NOT_LEARNER_ELIGIBLE",)):
        reasons = ("ROUTINE_SUCCESS",) if learner_eligible else reasons
    layers = _layers(
        learner_eligible=learner_eligible, novelty=novelty, severity=severity,
        capability_gap=capability_gap, discrimination=discrimination,
        surprise=surprise, counterexample=counterexample,
        interference=interference, trigger=trigger)
    return ExperienceValueReceipt(
        transition_id=transition_id, campaign_id=campaign_id,
        novelty=novelty, severity=severity, capability_gap=capability_gap,
        causal_discrimination=discrimination, surprise=surprise,
        counterexample=counterexample, memory_interference=interference,
        redundancy=redundancy, value_score=value_score, priority=priority,
        update_layers=layers, reasons=reasons)


def experience_value_digest(receipt: ExperienceValueReceipt) -> str:
    return "sha256:" + hashlib.sha256(
        stable_dumps(receipt.to_dict()).encode()).hexdigest()


def record_experience_value(
    conn: sqlite3.Connection, receipt: ExperienceValueReceipt, *,
    created_at: str | None = None, commit: bool = True,
) -> ExperienceValueReceipt:
    """Persist one immutable derived receipt and return the stored value."""
    if not isinstance(receipt, ExperienceValueReceipt):
        raise TypeError("record_experience_value requires an ExperienceValueReceipt")
    facts = load_transition_facts(conn, receipt.transition_id)
    if facts.transition_id != receipt.transition_id:
        raise ValueError("experience value transition witness mismatch")
    ensure_experience_value_schema(conn, commit=False)
    payload_json = stable_dumps(receipt.to_dict())
    digest = experience_value_digest(receipt)
    now = created_at or tehm_db.now_local()
    had_outer_transaction = conn.in_transaction
    existing = conn.execute(
        "SELECT * FROM tehm_experience_values WHERE transition_id=? AND campaign_id=?",
        (receipt.transition_id, receipt.campaign_id)).fetchone()
    if existing is not None:
        if (existing["receipt_json"] != payload_json
                or existing["receipt_digest"] != digest):
            raise ValueError("experience value replay conflicts with immutable receipt")
        stored = load_experience_value(conn, receipt.transition_id,
                                       receipt.campaign_id)
        return stored
    conn.execute(
        """INSERT INTO tehm_experience_values
           (transition_id, campaign_id, novelty, severity, capability_gap,
            causal_discrimination, surprise, counterexample, memory_interference,
            redundancy, value_score, priority, update_layers_json, receipt_json,
            receipt_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (receipt.transition_id, receipt.campaign_id, receipt.novelty,
         receipt.severity, receipt.capability_gap, receipt.causal_discrimination,
         receipt.surprise, receipt.counterexample, receipt.memory_interference,
         receipt.redundancy, receipt.value_score, receipt.priority,
         stable_dumps(list(receipt.update_layers)), payload_json, digest, now))
    stored = load_experience_value(conn, receipt.transition_id,
                                   receipt.campaign_id)
    if commit and not had_outer_transaction:
        conn.commit()
    return stored


def load_experience_value(conn: sqlite3.Connection, transition_id: str,
                          campaign_id: str = "live") -> ExperienceValueReceipt:
    ensure_experience_value_schema(conn, commit=False)
    row = conn.execute(
        "SELECT * FROM tehm_experience_values WHERE transition_id=? AND campaign_id=?",
        (transition_id, campaign_id)).fetchone()
    if row is None:
        raise ValueError("experience value receipt not found")
    payload = _strict_json(row["receipt_json"], "receipt", dict)
    receipt = ExperienceValueReceipt.from_dict(payload)
    if (receipt.transition_id != transition_id or receipt.campaign_id != campaign_id
            or row["receipt_digest"] != experience_value_digest(receipt)):
        raise ValueError("experience value receipt digest mismatch")
    expected = {
        "novelty": receipt.novelty, "severity": receipt.severity,
        "capability_gap": receipt.capability_gap,
        "causal_discrimination": receipt.causal_discrimination,
        "surprise": receipt.surprise, "counterexample": receipt.counterexample,
        "memory_interference": receipt.memory_interference,
        "redundancy": receipt.redundancy, "value_score": receipt.value_score,
        "priority": receipt.priority,
        "update_layers_json": stable_dumps(list(receipt.update_layers)),
    }
    if any(row[field] != value for field, value in expected.items()):
        raise ValueError("experience value columns conflict with receipt")
    return receipt


def evaluate_and_record_experience_value(
    conn: sqlite3.Connection, transition_id: str, *, campaign_id: str = "live",
    created_at: str | None = None, commit: bool = True,
    **kwargs,
) -> ExperienceValueReceipt:
    receipt = evaluate_experience_value(
        conn, transition_id, campaign_id=campaign_id, **kwargs)
    return record_experience_value(conn, receipt, created_at=created_at,
                                  commit=commit)


__all__ = [
    "EXPERIENCE_VALUE_VERSION", "VALUE_SCHEMA_SQL", "VALUE_WEIGHTS",
    "ensure_experience_value_schema", "evaluate_experience_value",
    "evaluate_and_record_experience_value", "experience_value_digest",
    "load_experience_value", "record_experience_value",
]
