"""Replication gate for upgrading controlled causal evidence to L3."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from tehm import db as tehm_db
from tehm.causal.evidence_level import CausalEvidenceLevel, evidence_rank
from tehm.causal.witness import learner_edge_transition_coverage


def _source_transition_ids(raw: object) -> tuple[tuple[str, ...] | None, str | None]:
    """Parse the path's derived source witness fail-closed."""
    try:
        values = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None, "malformed_source_transitions"
    if not isinstance(values, list) or not values:
        return None, "source_transitions_missing"
    ids = tuple(str(value).strip() for value in values)
    if any(not value for value in ids):
        return None, "malformed_source_transitions"
    if len(set(ids)) != len(ids):
        return None, "duplicate_source_transitions"
    return tuple(sorted(ids)), None


@dataclass(frozen=True)
class ReplicationReceipt:
    path_id: str
    eligible: bool
    evidence_level: str
    unique_lineages: tuple[str, ...]
    unique_designs: tuple[str, ...]
    unique_runs: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id, "eligible": self.eligible,
            "evidence_level": self.evidence_level,
            "unique_lineages": list(self.unique_lineages),
            "unique_designs": list(self.unique_designs),
            "unique_runs": list(self.unique_runs),
            "reason": self.reason,
        }


def evaluate_replicated_effect(
    conn: sqlite3.Connection,
    path_id: str,
    *,
    campaign_id: str = "live",
    min_lineages: int = 2,
    persist: bool = True,
    commit: bool = True,
) -> ReplicationReceipt:
    """Evaluate an L3 replication claim and optionally update its shadow path.

    The path update is derived evidence only. When the caller already owns a
    transaction, the update remains pending for that transaction even when
    commit=True; with no outer transaction, commit=True commits the
    helper-owned update. persist=False remains strictly read-only.
    """
    row = conn.execute("SELECT * FROM tehm_causal_paths WHERE path_id=?",
                       (path_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown causal path: {path_id}")
    transition_ids, source_error = _source_transition_ids(
        row["source_transitions_json"])
    if transition_ids is None:
        return ReplicationReceipt(path_id, False, row["evidence_level"], (), (), (),
                                  source_error or "malformed_source_transitions")
    placeholders = ",".join("?" for _ in transition_ids)
    rows = conn.execute(
        f"""SELECT t.transition_id, s.lineage_id, s.design_id,
                         t.provenance_json
              FROM tehm_transitions t JOIN tehm_states s ON s.state_id=t.source_state_id
             WHERE t.transition_id IN ({placeholders})
               AND EXISTS (SELECT 1 FROM tehm_dataset_membership dm
                            WHERE dm.transition_id=t.transition_id
                              AND dm.campaign_id=? AND dm.split='training'
                              AND dm.learner_eligible=1)""",
        (*transition_ids, campaign_id)).fetchall()
    lineages = tuple(sorted({row["lineage_id"] for row in rows if row["lineage_id"]}))
    designs = tuple(sorted({row["design_id"] for row in rows if row["design_id"]}))
    runs: set[str] = set()
    lineage_runs: dict[str, set[str]] = {}
    for source in rows:
        try:
            provenance = json.loads(source["provenance_json"])
        except (TypeError, json.JSONDecodeError):
            provenance = {}
        run = provenance.get("run_id") or provenance.get("run_tag")
        if run:
            run = str(run)
            runs.add(run)
            lineage = source["lineage_id"]
            if lineage:
                lineage_runs.setdefault(str(lineage), set()).add(run)
    covered_sources = learner_edge_transition_coverage(
        conn, transition_ids, campaign_id=campaign_id,
        required_level=CausalEvidenceLevel.L2_CONTROLLED_INTERVENTION.value)
    l2_support = set(covered_sources) == set(transition_ids)
    run_witness_complete = bool(
        len(runs) >= max(1, int(min_lineages)) and
        all(lineage_runs.get(lineage) for lineage in lineages))
    design_witness_complete = len(designs) >= max(1, int(min_lineages))
    eligible = bool(
        len(lineages) >= max(1, int(min_lineages)) and
        design_witness_complete and run_witness_complete and l2_support and
        len(rows) == len(transition_ids))
    if eligible:
        reason = "replicated_effect_supported"
    elif not l2_support or len(rows) != len(transition_ids):
        # Report campaign/control coverage first.  Missing witness metadata is
        # a secondary diagnosis only after the requested L2 sources are
        # actually present in this campaign.
        reason = "requires_controlled_pairs_and_disjoint_learner_lineages"
    elif not design_witness_complete:
        reason = "requires_distinct_design_witnesses"
    elif not run_witness_complete:
        reason = "requires_distinct_run_witnesses"
    else:
        reason = "requires_controlled_pairs_and_disjoint_learner_lineages"
    if (eligible and persist and
            evidence_rank(row["evidence_level"]) < evidence_rank(
                CausalEvidenceLevel.L3_REPLICATED_EFFECT.value)):
        support = json.loads(row["support_json"])
        support.update({"unique_lineages": list(lineages),
                        "unique_designs": list(designs),
                        "unique_runs": sorted(runs),
                        "replication_campaign": campaign_id})
        had_outer_transaction = conn.in_transaction
        conn.execute(
            """UPDATE tehm_causal_paths
                  SET evidence_level=?, support_json=?, updated_at=?
                WHERE path_id=?""",
            (CausalEvidenceLevel.L3_REPLICATED_EFFECT.value,
             json.dumps(support, sort_keys=True, separators=(",", ":")),
             tehm_db.now_local(), path_id))
        if commit and not had_outer_transaction:
            conn.commit()
    return ReplicationReceipt(
        path_id=path_id, eligible=eligible,
        evidence_level=(CausalEvidenceLevel.L3_REPLICATED_EFFECT.value
                        if eligible else row["evidence_level"]),
        unique_lineages=lineages, unique_designs=designs,
        unique_runs=tuple(sorted(runs)), reason=reason)


__all__ = ["ReplicationReceipt", "evaluate_replicated_effect"]
