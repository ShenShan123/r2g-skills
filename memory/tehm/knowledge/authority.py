"""Authority evaluation for Mechanism Knowledge (no automatic promotion)."""
from __future__ import annotations

import json
import sqlite3

from tehm.causal.evidence_level import at_least, validate_evidence_level
from tehm.causal.path_builder import validate_persisted_path_row

from .claims import MechanismKnowledge
from .receipts import KnowledgeAuthorityReceipt
from .schema import ensure_knowledge_schema


def evaluate_knowledge_authority(
    conn: sqlite3.Connection, knowledge: MechanismKnowledge, *,
    required_evidence_level: str = "L3_REPLICATED_EFFECT",
    min_support_lineages: int = 2,
) -> KnowledgeAuthorityReceipt:
    """Evaluate evidence gates and return a receipt without writing lifecycle."""
    if not isinstance(knowledge, MechanismKnowledge):
        raise TypeError("knowledge authority requires MechanismKnowledge")
    required = validate_evidence_level(required_evidence_level)
    if min_support_lineages < 1:
        raise ValueError("min_support_lineages must be positive")
    gates = {
        "claim_content_valid": True,
        "causal_paths_replay": True,
        "evidence_level_sufficient": at_least(knowledge.evidence_level, required),
        "lineage_diversity": False,
        "no_automatic_promotion": True,
    }
    lineages = set(knowledge.support_lineages)
    ensure_knowledge_schema(conn, commit=False)
    for path_id in knowledge.causal_path_ids:
        row = conn.execute(
            "SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)
        ).fetchone()
        if row is None:
            gates["causal_paths_replay"] = False
            continue
        try:
            validate_persisted_path_row(row, conn)
            if not at_least(row["evidence_level"], knowledge.evidence_level):
                gates["causal_paths_replay"] = False
            support = json.loads(row["support_json"])
            if isinstance(support, dict):
                values = support.get("unique_lineages", [])
                if isinstance(values, list):
                    lineages.update(str(value) for value in values if value)
        except (TypeError, ValueError, json.JSONDecodeError):
            gates["causal_paths_replay"] = False
    gates["lineage_diversity"] = len(lineages) >= min_support_lineages
    eligible = all(gates.values()) and knowledge.status in {"candidate", "validated"}
    reason = "eligible_for_authority_review" if eligible else \
        ";".join(name for name, passed in gates.items() if not passed)
    return KnowledgeAuthorityReceipt(
        object_id=knowledge.object_id, eligible=eligible,
        evidence_level=knowledge.evidence_level,
        required_evidence_level=required,
        support_lineages=tuple(sorted(lineages)), gates=gates, reason=reason)


__all__ = ["evaluate_knowledge_authority"]
