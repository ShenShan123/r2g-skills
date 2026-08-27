"""Retrieval index over crystallized rules (design doc 9.3).

v1 is a lightweight metadata index (by_check / by_family / by_obligation) over
the RULE STORE — embeddings/ANN are explicitly NOT the memory itself, only a
high-recall index. Only rules meeting minimum validity are indexed (honesty H6).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.crystallization.validity import ADMISSIBLE_FOR_LIFECYCLE
from tehm.ids import is_hole

INDEX_VERSION = "rule-index-v0.1"


@dataclass
class RuleIndex:
    rules: dict = field(default_factory=dict)          # rule_id -> rule dict
    by_check: dict = field(default_factory=dict)       # target_check -> [rule_id]
    by_family: dict = field(default_factory=dict)      # family -> [rule_id]
    by_obligation: dict = field(default_factory=dict)  # obligation -> [rule_id]

    def rule_ids(self) -> list:
        return list(self.rules.keys())

    def get(self, rule_id: str) -> dict | None:
        return self.rules.get(rule_id)

    def __len__(self) -> int:
        return len(self.rules)


def build_index(conn: sqlite3.Connection, *,
                lifecycle_statuses: frozenset[str] | None = None,
                require_validity: bool = True) -> RuleIndex:
    """Load admissible rules from ``tehm_rules`` and index them.

    ``lifecycle_statuses`` is an authority filter, independent of validity.
    Crystallization and A/B enrollment need the complete admissible rule store,
    while runtime retrieval must pass ``{"promoted"}`` (design doc 20.8).
    Keeping the two gates explicit prevents a VALIDATED-but-shadow rule from
    silently acquiring runtime authority.  ``require_validity=False`` is an
    evaluation-only escape hatch for the procedural ablation harness; callers
    must opt in explicitly and production retrieval never uses it.
    """
    index = RuleIndex()
    rows = conn.execute(
        "SELECT rule_id, domain, before_pattern_json, after_pattern_json, "
        "obligations_json, validity_status, confidence_json, utility_json, "
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
        rule = {
            "rule_id": row["rule_id"],
            "domain": row["domain"],
            "before_pattern": tehm_db.read_json(row["before_pattern_json"]),
            "after_pattern": tehm_db.read_json(row["after_pattern_json"]),
            "obligations": _json_list(row["obligations_json"]),
            "validity_status": row["validity_status"],
            "confidence": tehm_db.read_json(row["confidence_json"]),
            "utility": tehm_db.read_json(row["utility_json"]),
            "risk_profile": _json_list(row["risk_profile_json"]),
            "source_episodes": sources_by_rule.get(row["rule_id"], []),
        }
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


def _concrete_check(rule: dict) -> str | None:
    """The rule's match.target_check if it is a concrete literal (a hole is no
    constraint — the rule matches any check)."""
    check = (rule.get("before_pattern") or {}).get("target_check")
    if isinstance(check, str) and not is_hole(check):
        return check
    return None


def _json_list(text) -> list:
    if not text:
        return []
    data = tehm_db.read_json(text)
    return data if isinstance(data, list) else []
