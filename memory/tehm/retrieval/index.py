"""Retrieval index over crystallized rules (design doc 9.3).

v1 is a lightweight metadata index (by_check / by_family / by_obligation) over
the RULE STORE — embeddings/ANN are explicitly NOT the memory itself, only a
high-recall index. Only rules meeting minimum validity are indexed (honesty H6).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.crystallization.validity import ADMISSIBLE_FOR_LIFECYCLE
from tehm.ids import is_hole, rule_id as mint_rule_id

INDEX_VERSION = "rule-index-v0.2"


@dataclass
class RuleIndex:
    rules: dict = field(default_factory=dict)          # rule_id -> rule dict
    by_check: dict = field(default_factory=dict)       # target_check -> [rule_id]
    by_family: dict = field(default_factory=dict)      # family -> [rule_id]
    by_obligation: dict = field(default_factory=dict)  # obligation -> [rule_id]
    rejected: dict = field(default_factory=dict)       # rule_id -> integrity reason

    def rule_ids(self) -> list:
        return list(self.rules.keys())

    def get(self, rule_id: str) -> dict | None:
        return self.rules.get(rule_id)

    def __len__(self) -> int:
        return len(self.rules)


def build_index(conn: sqlite3.Connection, *,
                lifecycle_statuses: frozenset[str] | None = None,
                require_validity: bool = True) -> RuleIndex:
    """Load admissible, integrity-checked rules from ``tehm_rules`` and index them.

    ``lifecycle_statuses`` is an authority filter, independent of validity.
    Crystallization and A/B enrollment need the complete admissible rule store,
    while runtime retrieval must pass ``{"promoted"}`` (design doc 20.8).
    Keeping the two gates explicit prevents a VALIDATED-but-shadow rule from
    silently acquiring runtime authority.  ``require_validity=False`` is an
    evaluation-only escape hatch for the procedural ablation harness; callers
    must opt in explicitly and production retrieval never uses it.  Definition
    integrity is always checked here: no runtime flag can make a tampered rule
    executable.
    """
    index = RuleIndex()
    rows = conn.execute(
        "SELECT rule_id, domain, before_pattern_json, after_pattern_json, "
        "hard_preconditions_json, context_profile_json, obligations_json, "
        "validity_status, validity_profile_json, confidence_json, utility_json, "
        "risk_profile_json FROM tehm_rules").fetchall()
    # source episodes live in tehm_rule_sources (design doc 19.4).
    sources_by_rule: dict[str, list[str]] = {}
    for r in conn.execute(
            "SELECT rule_id, episode_id FROM tehm_rule_sources"):
        sources_by_rule.setdefault(r["rule_id"], []).append(r["episode_id"])
    allowed_rule_ids: set[str] | None = None
    if lifecycle_statuses is not None:
        placeholders = ",".join("?" for _ in lifecycle_statuses)
        if not placeholders:
            return index
        allowed_rule_ids = {r["rule_id"] for r in conn.execute(
            f"SELECT rule_id FROM tehm_rule_status WHERE status IN ({placeholders})",
            tuple(sorted(lifecycle_statuses)))}
    for row in rows:
        if require_validity and row["validity_status"] not in ADMISSIBLE_FOR_LIFECYCLE:
            continue
        if allowed_rule_ids is not None and row["rule_id"] not in allowed_rule_ids:
            continue
        try:
            rule = _load_rule(row, sources_by_rule.get(row["rule_id"], []))
        except RuleIntegrityError as exc:
            # A malformed or tampered definition is not a reason to make all
            # other rules unavailable.  It is recorded for audit and omitted
            # from every retrieval/activation index (fail closed).
            index.rejected[row["rule_id"]] = str(exc)
            continue
        rule["transformation_family"] = rule["before_pattern"].get("type")
        index.rules[rule["rule_id"]] = rule

        target_check = _concrete_check(rule)
        if target_check:
            index.by_check.setdefault(target_check, []).append(rule["rule_id"])
        family = rule["transformation_family"]
        if family:
            index.by_family.setdefault(family, []).append(rule["rule_id"])
        for obligation in rule["obligations"]:
            index.by_obligation.setdefault(obligation, []).append(rule["rule_id"])
    return index


class RuleIntegrityError(ValueError):
    """Persisted rule definition failed the runtime loading contract."""


def _load_rule(row: sqlite3.Row, source_episodes: list[str]) -> dict:
    """Decode and verify one canonical rule row.

    ``tehm_db.read_json`` is intentionally forgiving for reporting paths, but
    runtime retrieval cannot turn malformed JSON into an empty predicate or
    obligation list.  Immutable rule fields are re-hashed against ``rule_id``;
    mutable feedback fields are still type-checked so reranking cannot consume
    fabricated defaults.
    """
    rule_key = row["rule_id"]
    if not isinstance(rule_key, str) or not rule_key:
        raise RuleIntegrityError("missing rule_id")
    domain = row["domain"]
    if not isinstance(domain, str) or not domain:
        raise RuleIntegrityError("domain must be a non-empty string")

    before = _json_object(row["before_pattern_json"], "before_pattern")
    after = _json_object(row["after_pattern_json"], "after_pattern")
    hard_preconditions = _json_string_list(
        row["hard_preconditions_json"], "hard_preconditions")
    obligations = _json_string_list(row["obligations_json"], "obligations")
    expected_id = mint_rule_id(
        domain=domain, before_pattern=before, after_pattern=after,
        hard_preconditions=hard_preconditions, obligations=obligations)
    if rule_key != expected_id:
        raise RuleIntegrityError(
            f"content digest mismatch: expected {expected_id}")

    context_predicates = _json_object(
        row["context_profile_json"], "context_predicates")
    validity_profile = _json_object(
        row["validity_profile_json"], "validity_profile")
    confidence = _json_object(row["confidence_json"], "confidence")
    utility = _json_object(row["utility_json"], "utility")
    risk_profile = _json_list(row["risk_profile_json"], "risk_profile")

    before_profile = before.get("compatibility_profile")
    context_profile = context_predicates.get("compatibility_profile")
    if (before_profile is not None and
            (not isinstance(before_profile, str) or not before_profile)):
        raise RuleIntegrityError("before compatibility_profile is invalid")
    if (context_profile is not None and
            (not isinstance(context_profile, str) or not context_profile)):
        raise RuleIntegrityError("context compatibility_profile is invalid")
    if (before_profile is not None and context_profile is not None and
            before_profile != context_profile):
        raise RuleIntegrityError("compatibility_profile conflict")

    return {
        "rule_id": rule_key,
        "domain": domain,
        "action_domain": before.get("action_domain"),
        "before_pattern": before,
        "after_pattern": after,
        "hard_preconditions": hard_preconditions,
        "context_predicates": context_predicates,
        "obligations": obligations,
        "validity_status": row["validity_status"],
        "validity_profile": validity_profile,
        "confidence": confidence,
        "utility": utility,
        "risk_profile": risk_profile,
        "source_episodes": list(source_episodes),
    }


def _concrete_check(rule: dict) -> str | None:
    """The rule's match.target_check if it is a concrete literal (a hole is no
    constraint — the rule matches any check)."""
    check = (rule.get("before_pattern") or {}).get("target_check")
    if isinstance(check, str) and not is_hole(check):
        return check
    return None


def _json_list(text, field: str = "list") -> list:
    return _json_value(text, list, field)


def _json_object(text, field: str) -> dict:
    return _json_value(text, dict, field)


def _json_string_list(text, field: str) -> list[str]:
    values = _json_value(text, list, field)
    if any(not isinstance(item, str) or not item for item in values):
        raise RuleIntegrityError(f"{field} must contain non-empty strings")
    return values


def _json_value(text, expected_type, field: str):
    if not text:
        raise RuleIntegrityError(f"{field} JSON is empty")
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuleIntegrityError(f"{field} JSON is malformed") from exc
    if not isinstance(data, expected_type):
        raise RuleIntegrityError(
            f"{field} must decode to {expected_type.__name__}")
    return data
