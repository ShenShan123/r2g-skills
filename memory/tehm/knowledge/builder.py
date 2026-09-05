"""Build Mechanism Knowledge claims from validated causal paths."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from tehm.causal.evidence_level import evidence_rank, validate_evidence_level
from tehm.causal.mechanism import load_transition_facts
from tehm.causal.path_builder import validate_persisted_path_row

from .claims import KNOWLEDGE_STATUSES, MechanismKnowledge, knowledge_identity
from .negative_context import derive_negative_context


def _json_object(raw: object, field: str) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"mechanism knowledge {field} is malformed JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"mechanism knowledge {field} must be an object")
    return value


def _string_values(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"mechanism knowledge {field} must be a list")
    if any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"mechanism knowledge {field} contains invalid strings")
    return [item.strip() for item in value]


def _existing_version(conn: sqlite3.Connection, knowledge_id: str) -> int:
    if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("tehm_mechanism_knowledge",)).fetchone() is None:
        return 0
    row = conn.execute(
        "SELECT MAX(version) AS version FROM tehm_mechanism_knowledge "
        "WHERE knowledge_id=?", (knowledge_id,)).fetchone()
    return max(0, int(row["version"] or 0))


def build_knowledge_from_path(
    conn: sqlite3.Connection, path_id: str, *, status: str | None = None,
) -> MechanismKnowledge:
    """Derive one claim; only the knowledge registry persists it."""
    if type(path_id) is not str or not path_id:
        raise ValueError("mechanism knowledge path_id is required")
    row = conn.execute(
        "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()
    if row is None:
        raise ValueError("mechanism knowledge causal path not found")
    validate_persisted_path_row(row, conn)
    evidence_level = validate_evidence_level(row["evidence_level"])
    try:
        source_ids = tuple(json.loads(row["source_transitions_json"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("mechanism knowledge path sources are malformed") from exc
    if not source_ids or any(type(item) is not str or not item for item in source_ids):
        raise ValueError("mechanism knowledge path sources are malformed")
    facts = tuple(load_transition_facts(conn, value) for value in sorted(source_ids))
    family = str(row["mechanism_family"])
    profile = row["compatibility_profile"]
    if any(f.mechanism_family != family or f.compatibility_profile != profile
           for f in facts):
        raise ValueError("mechanism knowledge path family/profile witness conflicts")

    # ORFS baseline controls are witnesses for the causal comparison, not
    # repair actions or predictions of the treatment's outcome. Keep them in
    # antecedent/source provenance, but never vote them into the intervention.
    intervention_facts = tuple(
        f for f in facts if f.action.get("domain") != "flow.BASELINE_CONTROL")
    if not intervention_facts:
        raise ValueError("mechanism knowledge path has no intervention witnesses")
    action_domains = sorted({str(f.action.get("domain")) for f in intervention_facts})
    transformation_families = sorted({
        str(f.action.get("transformation_family")) for f in intervention_facts})
    action_digests = sorted({f.action_digest for f in intervention_facts})
    outcomes = Counter(f.outcome for f in facts)
    verdicts = Counter(str(f.verifier.get("verdict") or "UNKNOWN") for f in facts)
    intervention_outcomes = Counter(f.outcome for f in intervention_facts)
    intervention_verdicts = Counter(
        str(f.verifier.get("verdict") or "UNKNOWN") for f in intervention_facts)
    effects = sorted({str(f.primary_effect_key) for f in intervention_facts
                      if f.primary_effect_key})
    obligations: set[str] = set()
    failure_modes: set[str] = set()
    for facts_item in facts:
        for key in ("obligations", "preserved_obligations"):
            obligations.update(_string_values(facts_item.verifier.get(key), key))
        for key in ("created_regressions", "newly_observed_failures"):
            failure_modes.update(_string_values(facts_item.delta.get(key), key))
    antecedent = {
        "mechanism_family": family,
        "compatibility_profile": profile,
        "source_outcomes": dict(sorted(outcomes.items())),
        "source_verdicts": dict(sorted(verdicts.items())),
    }
    intervention = {
        "action_domains": action_domains,
        "transformation_families": transformation_families,
        "action_digests": action_digests,
        "controlled": evidence_rank(evidence_level) >= evidence_rank(
            "L2_CONTROLLED_INTERVENTION"),
    }
    mediated_effects = tuple({"primary_effect_key": effect} for effect in effects)
    expected_outcome = {
        "outcomes": dict(sorted(intervention_outcomes.items())),
        "verdicts": dict(sorted(intervention_verdicts.items())),
        "preferred_outcome": sorted(
            intervention_outcomes.items(), key=lambda item: (-item[1], item[0]))[0][0],
    }
    positive: list[dict] = [{"mechanism_family": family}]
    if profile is not None:
        positive.append({"compatibility_profile": profile})
    positive.extend({"transformation_family": value}
                    for value in transformation_families)
    negative = derive_negative_context(conn, row, source_ids)
    lineages = tuple(sorted({str(f.lineage_id) for f in facts if f.lineage_id}))
    claim_id = knowledge_identity(
        mechanism_family=family, compatibility_profile=profile,
        intervention=intervention, positive_applicability=tuple(positive))
    version = _existing_version(conn, claim_id) + 1
    requested_status = status or (
        "candidate" if evidence_rank(evidence_level) >= evidence_rank(
            "L2_CONTROLLED_INTERVENTION") else "shadow")
    if requested_status not in KNOWLEDGE_STATUSES:
        raise ValueError(f"invalid mechanism knowledge status: {requested_status!r}")
    if requested_status not in {"shadow", "candidate"}:
        raise ValueError("knowledge builder cannot grant validated/production status")
    if (requested_status == "candidate" and
            evidence_rank(evidence_level) < evidence_rank("L2_CONTROLLED_INTERVENTION")):
        raise ValueError("L0/L1 knowledge is shadow-only")
    return MechanismKnowledge(
        knowledge_id=claim_id, version=version, mechanism_family=family,
        compatibility_profile=profile, antecedent=antecedent,
        intervention=intervention, mediated_effects=mediated_effects,
        expected_outcome=expected_outcome,
        positive_applicability=tuple(positive),
        negative_applicability=negative,
        preserved_obligations=tuple(sorted(obligations)),
        known_failure_modes=tuple(sorted(failure_modes)),
        causal_path_ids=(path_id,), evidence_level=evidence_level,
        support_lineages=lineages, status=requested_status)


__all__ = ["build_knowledge_from_path"]
