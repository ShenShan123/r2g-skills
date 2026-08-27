"""Build and consolidate deterministic causal shadow fragments."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable

from tehm import db as tehm_db
from tehm.ids import stable_dumps

from .edges import CausalEdge, persist_edge
from .evidence_level import (
    CausalEvidenceLevel, evidence_rank, transition_evidence_level,
    validate_evidence_level,
)
from .mechanism import load_transition_facts, mechanism_signature
from .nodes import CausalNode, persist_node
from .receipts import CausalFragment, CausalPathCandidate
from .schema import validate_path_status

EXTRACTOR_VERSION = "rtl-causal-fragment-v0.1"
PATH_ORDER_VERSION = "causal-path-order-v1"

_NODE_ORDER = {
    "STATE_CONDITION": 0,
    "ACTION": 1,
    "INTERMEDIATE_EFFECT": 2,
    "FAILURE_MECHANISM": 3,
    "PHYSICAL_EFFECT": 4,
    "ORACLE_OUTCOME": 5,
    "OBLIGATION": 6,
    "REGRESSION": 7,
    "ASSET": 8,
    "CAPABILITY": 9,
}
_EDGE_ORDER = {
    "ENABLES": 0,
    "BLOCKS": 0,
    "INTERVENES_ON": 1,
    "CHANGES": 2,
    "MEDIATES": 3,
    "REMOVES": 4,
    "CREATES": 4,
    "PRESERVES": 4,
    "CONTRADICTS": 5,
    "SUPPORTS": 6,
    "SPECIALIZES": 7,
    "GENERALIZES": 7,
}


def _digest(value: object) -> str:
    return hashlib.sha1(stable_dumps(value).encode()).hexdigest()[:16]


def causal_path_digest(*, mechanism_family: str,
                       compatibility_profile: str | None,
                       evidence_level: str,
                       source_transition_ids: Iterable[str],
                       node_ids: Iterable[str], edge_ids: Iterable[str],
                       support: dict) -> str:
    """Derive the content digest used by a persisted causal path.

    Keep this in one place so creation, replay and evaluation cannot silently
    disagree about JSON/list ordering.  ``path_id`` remains the stable lookup
    key for a path lineage; ``path_digest`` is the versioned row-content
    digest and therefore changes when replication support is upgraded.
    """
    payload = {
        "mechanism_family": mechanism_family,
        "compatibility_profile": compatibility_profile,
        "evidence_level": evidence_level,
        "source_transitions": list(source_transition_ids),
        "nodes": list(node_ids),
        "edges": list(edge_ids),
        "support": support,
    }
    return "sha1:" + hashlib.sha1(
        stable_dumps(payload).encode()).hexdigest()


def _path_json(raw: object, expected_type, field: str):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"causal path {field} is malformed JSON") from exc
    if not isinstance(value, expected_type):
        raise ValueError(
            f"causal path {field} must decode to {expected_type.__name__}")
    return value


def _ordered_unique_ids(
    source: tuple[CausalFragment, ...], *, kind: str,
) -> tuple[str, ...]:
    """Return deterministic causal-topology order, independent of input order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for fragment in sorted(source, key=lambda item: item.transition_id):
        if kind == "nodes":
            values = sorted(
                fragment.nodes,
                key=lambda item: (_NODE_ORDER.get(item.node_type, 99),
                                  item.causal_node_id),
            )
            ids = (item.causal_node_id for item in values)
        else:
            values = sorted(
                fragment.edges,
                key=lambda item: (_EDGE_ORDER.get(item.relation_type, 99),
                                  item.source_node_id, item.target_node_id,
                                  item.causal_edge_id),
            )
            ids = (item.causal_edge_id for item in values)
        for value in ids:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
    return tuple(ordered)


def _validate_path_topology(conn: sqlite3.Connection, node_ids: list[str],
                            edge_ids: list[str]) -> None:
    """Validate persisted endpoints and canonical causal order when possible."""
    node_placeholders = ",".join("?" for _ in node_ids)
    node_rows = conn.execute(
        "SELECT causal_node_id, node_type, owner_id FROM tehm_causal_nodes "
        f"WHERE causal_node_id IN ({node_placeholders})", node_ids).fetchall()
    if len(node_rows) != len(node_ids):
        raise ValueError("causal path references a missing node")
    by_node = {str(row["causal_node_id"]): row for row in node_rows}
    expected_nodes = sorted(
        node_ids,
        key=lambda node_id: (
            str(by_node[node_id]["owner_id"] or ""),
            _NODE_ORDER.get(str(by_node[node_id]["node_type"]), 99),
            node_id,
        ),
    )
    if node_ids != expected_nodes:
        raise ValueError("causal path ordered_nodes are not in causal topology order")

    edge_placeholders = ",".join("?" for _ in edge_ids)
    edge_rows = conn.execute(
        "SELECT causal_edge_id, source_node_id, relation_type, target_node_id "
        f"FROM tehm_causal_edges WHERE causal_edge_id IN ({edge_placeholders})",
        edge_ids).fetchall()
    if len(edge_rows) != len(edge_ids):
        raise ValueError("causal path references a missing edge")
    by_edge = {str(row["causal_edge_id"]): row for row in edge_rows}
    node_set = set(node_ids)
    if any(str(row["source_node_id"]) not in node_set or
           str(row["target_node_id"]) not in node_set for row in edge_rows):
        raise ValueError("causal path edge endpoint is outside ordered_nodes")

    def edge_key(edge_id: str) -> tuple:
        edge = by_edge[edge_id]
        source = str(edge["source_node_id"])
        return (
            str(by_node[source]["owner_id"] or ""),
            _EDGE_ORDER.get(str(edge["relation_type"]), 99),
            source, str(edge["target_node_id"]), edge_id,
        )

    expected_edges = sorted(edge_ids, key=edge_key)
    if edge_ids != expected_edges:
        raise ValueError("causal path ordered_edges are not in causal topology order")


def validate_persisted_path_row(row: sqlite3.Row,
                                conn: sqlite3.Connection | None = None) -> None:
    """Raise when a derived causal-path row is malformed or tampered.

    This is intentionally independent of production authority.  Evaluation
    retrieval uses it to skip corrupted shadow rows, while mutation paths use
    it to reject conflicting replays before accepting an existing ID.
    """
    if not row["path_id"] or not row["mechanism_family"]:
        raise ValueError("causal path identity is incomplete")
    try:
        validate_evidence_level(row["evidence_level"])
        validate_path_status(row["status"])
    except ValueError as exc:
        raise ValueError(f"causal path enum is invalid: {exc}") from exc
    nodes = _path_json(row["ordered_nodes_json"], list, "ordered_nodes")
    edges = _path_json(row["ordered_edges_json"], list, "ordered_edges")
    sources = _path_json(row["source_transitions_json"], list,
                         "source_transitions")
    support = _path_json(row["support_json"], dict, "support")
    for field, values in (("ordered_nodes", nodes), ("ordered_edges", edges),
                          ("source_transitions", sources)):
        if not values:
            raise ValueError(f"causal path {field} is empty")
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"causal path {field} contains an invalid ID")
        if len(set(values)) != len(values):
            raise ValueError(f"causal path {field} contains duplicate IDs")
    expected = causal_path_digest(
        mechanism_family=row["mechanism_family"],
        compatibility_profile=row["compatibility_profile"],
        evidence_level=row["evidence_level"],
        source_transition_ids=sources, node_ids=nodes, edge_ids=edges,
        support=support)
    if row["path_digest"] != expected:
        raise ValueError("causal path content digest mismatch")
    if conn is not None:
        _validate_path_topology(conn, nodes, edges)


def _validate_persisted_fragments(
    conn: sqlite3.Connection,
    fragments: tuple[CausalFragment, ...],
    *,
    campaign_id: str | None,
) -> str:
    """Validate the evidence firewall before persisting a causal path.

    ``CausalFragment`` is a public dataclass and can be constructed by a
    caller, so its ``learner_eligible`` bit is not authority by itself.  A
    persisted path must resolve every source transition, membership row, node,
    and edge back to the same training campaign.  This is deliberately done at
    the path boundary rather than relying on the fragment builder having been
    used by the caller.
    """
    campaigns = {str(fragment.campaign_id) for fragment in fragments
                 if fragment.campaign_id}
    if campaign_id is not None:
        selected = str(campaign_id)
        if any(fragment.campaign_id != selected for fragment in fragments):
            raise ValueError("causal fragments do not belong to the requested campaign")
    elif len(campaigns) != 1:
        raise ValueError(
            "causal path requires one explicit learner campaign")
    else:
        selected = next(iter(campaigns))
    if not selected:
        raise ValueError("causal path requires one explicit learner campaign")

    transition_ids = tuple(sorted({fragment.transition_id for fragment in fragments}))
    placeholders = ",".join("?" for _ in transition_ids)
    memberships = conn.execute(
        f"""SELECT transition_id, split, learner_eligible
               FROM tehm_dataset_membership
              WHERE campaign_id=? AND transition_id IN ({placeholders})""",
        (selected, *transition_ids)).fetchall()
    by_transition = {str(row["transition_id"]): row for row in memberships}
    if len(by_transition) != len(transition_ids) or any(
            str(by_transition[transition_id]["split"]) != "training" or
            not bool(by_transition[transition_id]["learner_eligible"])
            for transition_id in transition_ids):
        raise ValueError(
            "causal path sources must be training learner evidence")

    for fragment in fragments:
        for node in fragment.nodes:
            row = conn.execute(
                "SELECT node_type, owner_type, owner_id, payload_json, "
                "payload_digest, extractor_version "
                "FROM tehm_causal_nodes WHERE causal_node_id=?",
                (node.causal_node_id,)).fetchone()
            if row is None:
                raise ValueError("causal fragment node is not persisted")
            expected = node.to_row(created_at="ignored")
            if any(row[field] != expected[field] for field in (
                    "node_type", "owner_type", "owner_id", "payload_json",
                    "payload_digest", "extractor_version")):
                raise ValueError("causal fragment node witness conflicts")
        for edge in fragment.edges:
            row = conn.execute(
                "SELECT source_node_id, relation_type, target_node_id, "
                "evidence_level, support_json, confidence_json, "
                "evidence_refs_json, campaign_id, learner_eligible "
                "FROM tehm_causal_edges WHERE causal_edge_id=?",
                (edge.causal_edge_id,)).fetchone()
            if row is None:
                raise ValueError("causal fragment edge is not persisted")
            expected = edge.to_row(created_at="ignored")
            if any(row[field] != expected[field] for field in (
                    "source_node_id", "relation_type", "target_node_id",
                    "evidence_level", "support_json", "confidence_json",
                    "evidence_refs_json", "campaign_id", "learner_eligible")):
                raise ValueError("causal fragment edge witness conflicts")
            try:
                refs = json.loads(row["evidence_refs_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                refs = []
            if fragment.transition_id not in refs:
                raise ValueError("causal fragment edge lacks transition witness")
    return selected


def _membership(conn: sqlite3.Connection, transition_id: str,
                campaign_id: str | None) -> tuple[str | None, bool, str | None]:
    if campaign_id:
        row = conn.execute(
            """SELECT campaign_id, learner_eligible, split
                 FROM tehm_dataset_membership
                WHERE transition_id=? AND campaign_id=?""",
            (transition_id, campaign_id)).fetchone()
    else:
        row = conn.execute(
            """SELECT campaign_id, learner_eligible, split
                 FROM tehm_dataset_membership
                WHERE transition_id=?
                ORDER BY CASE WHEN campaign_id='live' THEN 0 ELSE 1 END,
                         campaign_id""", (transition_id,)).fetchone()
    if row is None:
        # Missing membership is not silently treated as training evidence.
        return campaign_id, False, None
    split = str(row["split"])
    # Keep the semantic split in the authority predicate even if a legacy or
    # direct-SQL row contradicts the API invariant.
    return str(row["campaign_id"]), bool(row["learner_eligible"] and
                                          split == "training"), split


def build_transition_causal_fragment(
    conn: sqlite3.Connection,
    transition_id: str,
    *,
    campaign_id: str | None = None,
    commit: bool = True,
) -> CausalFragment:
    """Create one L1 shadow fragment from an executed canonical transition.

    The only writes are ``tehm_causal_*`` rows.  Canonical states, transitions,
    episodes, and memberships are read-only inputs and are never rewritten.
    ``commit=False`` is reserved for an enclosing online savepoint so a causal
    fragment cannot be left behind when a later event/proposal fails.  When
    ``commit=True``, an already-active caller transaction is left open; this
    helper commits only when it owns the transaction.
    """
    facts = load_transition_facts(conn, transition_id)
    selected_campaign, learner_eligible, split = _membership(
        conn, transition_id, campaign_id)
    level = transition_evidence_level(
        transition_present=True,
        verifier_present=(bool(facts.verifier) and
                          facts.verifier.get("verdict") not in {None, "UNKNOWN"} and
                          facts.verifier.get("oracle_type") not in {None, "UNKNOWN"}))
    signature = mechanism_signature(facts)
    state_node = CausalNode(
        "STATE_CONDITION",
        payload={
            "transition_id": transition_id,
            "state_id": facts.source_state["state_id"],
            "domain": facts.source_state["domain"],
            "context_graph_digest": facts.failure_graph_digest,
            "compatibility_profile": facts.compatibility_profile,
            "mechanism_signature": signature,
        },
        owner_type="transition", owner_id=transition_id,
        extractor_version=EXTRACTOR_VERSION)
    action_node = CausalNode(
        "ACTION",
        payload={
            "transition_id": transition_id,
            "action": facts.action,
            "action_digest": facts.action_digest,
        }, owner_type="transition", owner_id=transition_id,
        extractor_version=EXTRACTOR_VERSION)
    effect_node = CausalNode(
        "INTERMEDIATE_EFFECT",
        payload={
            "transition_id": transition_id,
            "primary_effect_key": facts.primary_effect_key,
            "source_graph_digest": facts.source_state.get("context_graph_digest"),
            "target_graph_digest": facts.target_state.get("context_graph_digest"),
            "observation_delta": facts.delta,
        }, owner_type="transition", owner_id=transition_id,
        extractor_version=EXTRACTOR_VERSION)
    outcome_node = CausalNode(
        "ORACLE_OUTCOME",
        payload={
            "transition_id": transition_id,
            "outcome": facts.outcome,
            "verifier": facts.verifier,
            "created_regressions": facts.delta.get("created_regressions", []),
            "newly_observed_failures": facts.delta.get("newly_observed_failures", []),
        }, owner_type="transition", owner_id=transition_id,
        extractor_version=EXTRACTOR_VERSION)
    nodes = (state_node, action_node, effect_node, outcome_node)
    support = {
        "transition_id": transition_id,
        "split": split,
        "action_digest": facts.action_digest,
        "oracle_type": facts.verifier.get("oracle_type"),
        "verdict": facts.verifier.get("verdict"),
    }
    if facts.outcome in {"PASS", "PARTIAL"}:
        relation = "REMOVES"
    elif facts.outcome in {"FAIL", "REGRESSION"}:
        relation = "CREATES"
    else:
        relation = "SUPPORTS"
    refs = tuple([transition_id] + [
        f"oracle:{ref}" for ref in facts.verifier.get("evidence_refs", [])
    ])
    edges = (
        CausalEdge(state_node.causal_node_id, "INTERVENES_ON",
                   action_node.causal_node_id, level, support,
                   {"level": level}, refs, selected_campaign, learner_eligible),
        CausalEdge(action_node.causal_node_id, "CHANGES",
                   effect_node.causal_node_id, level, support,
                   {"level": level}, refs, selected_campaign, learner_eligible),
        CausalEdge(effect_node.causal_node_id, relation,
                   outcome_node.causal_node_id, level, support,
                   {"level": level}, refs, selected_campaign, learner_eligible),
    )
    had_outer_transaction = conn.in_transaction
    created_at = tehm_db.now_local()
    for node in nodes:
        persist_node(conn, node, created_at=created_at)
    for edge in edges:
        persist_edge(conn, edge, created_at=created_at)
    if commit and not had_outer_transaction:
        conn.commit()
    return CausalFragment(
        transition_id=transition_id,
        mechanism_family=facts.mechanism_family,
        compatibility_profile=facts.compatibility_profile,
        evidence_level=level,
        learner_eligible=learner_eligible,
        campaign_id=selected_campaign,
        lineage_id=facts.lineage_id,
        failure_graph_digest=facts.failure_graph_digest,
        nodes=nodes, edges=edges)


def consolidate_causal_path(
    fragments_or_conn,
    fragments: Iterable[CausalFragment] | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    campaign_id: str | None = None,
    status: str = "shadow",
) -> CausalPathCandidate:
    """Consolidate learner-eligible fragments into a deterministic shadow path.

    Both ``consolidate_causal_path(fragments)`` and the convenient
    ``consolidate_causal_path(conn, fragments)`` form are accepted.  A
    held-out/calibration fragment is rejected rather than quietly becoming
    learner support.  Persistence, when requested, is limited to the causal
    shadow table and never changes rule lifecycle.  An already-active caller
    transaction remains open; ``commit=True`` commits only when this helper
    owns the transaction.
    """
    if isinstance(fragments_or_conn, sqlite3.Connection):
        conn = fragments_or_conn
        source = tuple(fragments or ())
    else:
        source = tuple(fragments_or_conn or ())
    if not source:
        raise ValueError("at least one causal fragment is required")
    validate_path_status(status)
    if any(not fragment.learner_eligible for fragment in source):
        raise ValueError("learner-ineligible causal evidence cannot be consolidated")
    if conn is not None:
        _validate_persisted_fragments(conn, source, campaign_id=campaign_id)
    elif campaign_id and any(fragment.campaign_id != campaign_id for fragment in source):
        raise ValueError("causal fragments do not belong to the requested campaign")
    families = {fragment.mechanism_family for fragment in source}
    profiles = {fragment.compatibility_profile for fragment in source}
    if len(families) != 1 or len(profiles) != 1:
        raise ValueError("causal path requires one mechanism family and profile")
    level = min(source, key=lambda fragment: evidence_rank(fragment.evidence_level)).evidence_level
    source_ids = tuple(sorted({fragment.transition_id for fragment in source}))
    node_ids = _ordered_unique_ids(source, kind="nodes")
    edge_ids = _ordered_unique_ids(source, kind="edges")
    support = {
        "fragment_count": len(source),
        "unique_lineages": sorted({fragment.lineage_id for fragment in source
                                     if fragment.lineage_id}),
        "source_campaigns": sorted({fragment.campaign_id for fragment in source
                                     if fragment.campaign_id}),
        "evidence_levels": sorted({fragment.evidence_level for fragment in source}),
    }
    # Persist the typed mechanism/effect witnesses used by the evaluation-only
    # causal matcher.  A path may consolidate several fragments from one
    # family/profile, so retain all signatures rather than collapsing distinct
    # guards or structural states into one lossy representative.
    signatures = []
    effects = set()
    action_digests = set()
    graph_digests = set()
    for fragment in source:
        for node in fragment.nodes:
            if node.node_type == "STATE_CONDITION":
                signature = node.payload.get("mechanism_signature")
                if isinstance(signature, dict):
                    signatures.append(signature)
                digest = node.payload.get("context_graph_digest")
                if digest:
                    graph_digests.add(str(digest))
            elif node.node_type == "INTERMEDIATE_EFFECT":
                effect = node.payload.get("primary_effect_key")
                if effect:
                    effects.add(str(effect))
            elif node.node_type == "ACTION":
                digest = node.payload.get("action_digest")
                if digest:
                    action_digests.add(str(digest))
    # JSON/digest stability matters for frozen evidence; sort by canonical JSON
    # instead of relying on input fragment order.
    signatures = sorted(signatures, key=stable_dumps)
    if signatures:
        support["mechanism_signatures"] = signatures
    support["primary_effect_keys"] = sorted(effects)
    support["action_digests"] = sorted(action_digests)
    support["failure_graph_digests"] = sorted(graph_digests)
    support["path_order_version"] = PATH_ORDER_VERSION
    path_digest = causal_path_digest(
        mechanism_family=next(iter(families)),
        compatibility_profile=next(iter(profiles)),
        evidence_level=level, source_transition_ids=source_ids,
        node_ids=node_ids, edge_ids=edge_ids, support=support)
    candidate = CausalPathCandidate(
        path_id="causal_path_" + path_digest.split(":", 1)[1][:16],
        path_digest=path_digest,
        mechanism_family=next(iter(families)),
        compatibility_profile=next(iter(profiles)),
        evidence_level=level,
        source_transition_ids=source_ids,
        node_ids=node_ids,
        edge_ids=edge_ids,
        support=support,
        status=status)
    if conn is not None:
        had_outer_transaction = conn.in_transaction
        now = tehm_db.now_local()
        expected_row = {
            "path_id": candidate.path_id,
            "mechanism_family": candidate.mechanism_family,
            "compatibility_profile": candidate.compatibility_profile,
            "ordered_nodes_json": stable_dumps(list(node_ids)),
            "ordered_edges_json": stable_dumps(list(edge_ids)),
            "evidence_level": candidate.evidence_level,
            "support_json": stable_dumps(candidate.support),
            "source_transitions_json": stable_dumps(list(source_ids)),
            "path_digest": candidate.path_digest,
            "status": candidate.status,
        }
        existing = conn.execute(
            "SELECT path_id, mechanism_family, compatibility_profile, "
            "ordered_nodes_json, ordered_edges_json, evidence_level, "
            "support_json, source_transitions_json, path_digest, status "
            "FROM tehm_causal_paths WHERE path_id=?",
            (candidate.path_id,)).fetchone()
        if existing is not None:
            validate_persisted_path_row(existing, conn)
            mismatches = [field for field, value in expected_row.items()
                          if existing[field] != value]
            if mismatches:
                raise ValueError(
                    "causal path replay conflicts with immutable path "
                    f"{candidate.path_id}: {', '.join(mismatches)}")
        else:
            conn.execute(
                """INSERT INTO tehm_causal_paths
                   (path_id, mechanism_family, compatibility_profile,
                    ordered_nodes_json, ordered_edges_json, evidence_level,
                    support_json, source_transitions_json, path_digest, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.path_id, candidate.mechanism_family,
                 candidate.compatibility_profile, expected_row["ordered_nodes_json"],
                 expected_row["ordered_edges_json"], candidate.evidence_level,
                 expected_row["support_json"], expected_row["source_transitions_json"],
                 candidate.path_digest, candidate.status, now, now))
        if not had_outer_transaction:
            conn.commit()
    return candidate


__all__ = ["CausalFragment", "CausalPathCandidate", "PATH_ORDER_VERSION",
           "causal_path_digest",
           "validate_persisted_path_row", "build_transition_causal_fragment",
           "consolidate_causal_path"]
