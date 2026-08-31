"""Current-valid-state resolver for the P1 shadow lane.

The resolver derives a deterministic view from immutable canonical/derived rows
and explicit relations.  It never changes lifecycle status, canonical
evidence, rule authority, asset authority, or production runtime state.

Relation orientation is explicit: ``SUPERSEDES`` points from replacement to
the object it supersedes; ``REPLACED_BY`` points from the old object to its
replacement.  Unbound relations may be evaluated in ``shadow`` mode only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from tehm import db as tehm_db
from tehm.assets.registry import get_asset
from tehm.capability.registry import validate_capability_row
from tehm.ids import stable_dumps

from .receipts import ResolvedMemoryState, SuppressionReceipt, StateResolutionReceipt
from .relations import MemoryRelation, load_relations
from .schema import ensure_state_schema
from .validation import normalize_scope, parse_json_array, parse_json_object


RESOLVER_VERSION = "state-resolver-v0.1"
_ACTIVE_RULE_STATUSES = frozenset({"shadow", "candidate", "promoted"})
_ACTIVE_PATH_STATUSES = frozenset({"shadow", "candidate", "validated"})
_ACTIVE_ASSET_STATUSES = frozenset({"shadow", "candidate", "promoted"})
_ACTIVE_CAPABILITY_STATUSES = frozenset({"candidate", "verified", "promoted"})
_SUPPRESSING = frozenset({"SUPERSEDES", "INVALIDATES", "RETIRES", "REPLACED_BY"})
_PRECEDENCE = frozenset({"SUPERSEDES", "INVALIDATES", "RETIRES", "REPLACED_BY"})


class StateResolutionError(ValueError):
    """A malformed, ambiguous, cyclic, or unauthorised state cannot resolve."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _scope_matches(relation_scope: Mapping, requested: Mapping) -> bool:
    return all(key in requested and requested[key] == value
               for key, value in relation_scope.items())


def _row_payload(row: sqlite3.Row) -> dict:
    result = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, bytes):
            value = value.hex()
        result[str(key)] = value
    return result


def _input_digest(conn: sqlite3.Connection, scope: dict,
                  relations: tuple[MemoryRelation, ...]) -> str:
    tables = (
        "tehm_states", "tehm_transitions", "tehm_episodes", "tehm_episode_steps",
        "tehm_rules", "tehm_rule_sources", "tehm_rule_status", "tehm_rule_revisions",
        "tehm_causal_paths", "tehm_assets", "tehm_asset_status", "tehm_capabilities",
        "tehm_mechanism_knowledge", "tehm_mechanism_knowledge_status",
        "tehm_mechanism_knowledge_evidence",
        "tehm_memory_relations",
    )
    payload = {"scope": scope, "relations": [item.to_dict() for item in relations]}
    for table in tables:
        if _table_exists(conn, table):
            rows = [_row_payload(row) for row in conn.execute(
                f'SELECT * FROM "{table}"')]
            rows.sort(key=stable_dumps)
            payload[table] = rows
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _authority_ref_valid(conn: sqlite3.Connection, relation: MemoryRelation,
                         requested_scope: dict) -> bool:
    if relation.authority_ref is None:
        return False
    candidates = []
    for table in ("tehm_rule_authority_receipts", "tehm_asset_authority_receipts"):
        if not _table_exists(conn, table):
            continue
        row = conn.execute(
            f"SELECT * FROM {table} WHERE authority_receipt_id=?",
            (relation.authority_ref,)).fetchone()
        if row is not None:
            candidates.append(row)
    if len(candidates) != 1:
        return False
    row = candidates[0]
    if type(row["eligible"]) is not int or row["eligible"] != 1:
        return False
    expected_type = "rule" if "rule_id" in row.keys() else "asset"
    if (relation.source_type != expected_type and
            relation.target_type != expected_type):
        return False
    object_id = relation.source_id if relation.source_type == expected_type else relation.target_id
    if row[f"{expected_type}_id"] != object_id:
        return False
    scope_value = requested_scope.get("target_scope")
    if scope_value is not None and row["target_scope"] != scope_value:
        return False
    try:
        receipt = json.loads(row["receipt_json"])
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(receipt, dict)


def _validate_authority(conn: sqlite3.Connection, relation: MemoryRelation,
                        requested_scope: dict, mode: str) -> bool:
    if relation.authority_ref is None:
        if mode == "production":
            raise StateResolutionError(
                f"UNRESOLVED_AUTHORITY: relation {relation.relation_id} has no authority")
        return False
    if not _authority_ref_valid(conn, relation, requested_scope):
        raise StateResolutionError(
            f"UNRESOLVED_AUTHORITY: relation {relation.relation_id} authority mismatch")
    return True


def _object_key(object_type: str, object_id: str) -> str:
    return f"{object_type}:{object_id}"


def _status_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    return conn.execute(f'SELECT * FROM "{table}"').fetchall()


def _active_rules(conn: sqlite3.Connection, scope: dict) -> set[str]:
    if not _table_exists(conn, "tehm_rules"):
        return set()
    statuses: dict[str, list[sqlite3.Row]] = {}
    for row in _status_rows(conn, "tehm_rule_status"):
        status = row["status"]
        if status not in {"shadow", "candidate", "promoted", "demoted", "quarantined", "retired"}:
            raise StateResolutionError("invalid rule lifecycle status")
        statuses.setdefault(str(row["rule_id"]), []).append(row)
    active = set()
    for row in conn.execute("SELECT rule_id FROM tehm_rules"):
        choices = statuses.get(str(row["rule_id"]), [])
        if any(item["status"] in _ACTIVE_RULE_STATUSES and
               (item["target_scope"] == "global" or
                not scope.get("target_scope") or
                item["target_scope"] == scope.get("target_scope"))
               for item in choices):
            active.add(str(row["rule_id"]))
    return active


def _active_paths(conn: sqlite3.Connection, scope: dict) -> set[str]:
    active = set()
    for row in _status_rows(conn, "tehm_causal_paths"):
        if row["status"] not in _ACTIVE_PATH_STATUSES:
            if row["status"] != "retired":
                raise StateResolutionError("invalid causal path lifecycle status")
            continue
        if scope.get("mechanism_family") is not None and row["mechanism_family"] != scope["mechanism_family"]:
            continue
        if (scope.get("compatibility_profile") is not None and
                row["compatibility_profile"] != scope["compatibility_profile"]):
            continue
        active.add(str(row["path_id"]))
    return active


def _active_assets(conn: sqlite3.Connection, scope: dict) -> set[str]:
    active = set()
    for row in _status_rows(conn, "tehm_asset_status"):
        if row["status"] not in _ACTIVE_ASSET_STATUSES:
            if row["status"] not in {"draft", "demoted", "quarantined", "retired"}:
                raise StateResolutionError("invalid asset lifecycle status")
            continue
        asset = get_asset(conn, str(row["asset_id"]))
        if asset is None:
            raise StateResolutionError("asset registry content is invalid")
        compatibility = asset.get("compatibility") or {}
        allowed_scopes = {"global"}
        if scope.get("target_scope"):
            allowed_scopes.add(scope["target_scope"])
        # Asset registration defaults its lifecycle scope to the compatibility
        # profile.  Treat that profile as a valid scope dimension alongside
        # the query's concrete target check; do not make a route query blind
        # to a profile-scoped asset.
        if scope.get("compatibility_profile"):
            allowed_scopes.add(scope["compatibility_profile"])
        if row["target_scope"] not in allowed_scopes:
            continue
        if (scope.get("compatibility_profile") is not None and
                compatibility.get("compatibility_profile") != scope["compatibility_profile"]):
            continue
        active.add(str(row["asset_id"]))
    return active


def _active_capabilities(conn: sqlite3.Connection, scope: dict) -> set[str]:
    active = set()
    for row in _status_rows(conn, "tehm_capabilities"):
        if row["status"] not in _ACTIVE_CAPABILITY_STATUSES:
            if row["status"] not in {"observed_gap", "regressed", "retired"}:
                raise StateResolutionError("invalid capability lifecycle status")
            continue
        if scope.get("mechanism_family") is not None and row["mechanism_family"] != scope["mechanism_family"]:
            continue
        try:
            validate_capability_row(row)
        except ValueError as exc:
            raise StateResolutionError("capability registry content is invalid") from exc
        active.add(str(row["capability_id"]))
    return active


def _active_knowledge(conn: sqlite3.Connection, scope: dict) -> set[str]:
    table = "tehm_mechanism_knowledge_status"
    if not _table_exists(conn, table):
        return set()
    active = set()
    for row in conn.execute(f"SELECT * FROM {table}"):
        if row["status"] not in {"shadow", "candidate", "validated"}:
            if row["status"] not in {"superseded", "invalidated", "retired"}:
                raise StateResolutionError("invalid mechanism knowledge status")
            continue
        if scope.get("target_scope") and row["target_scope"] not in {"global", scope["target_scope"]}:
            continue
        active.add(f"{row['knowledge_id']}@{row['version']}")
    return active


def _existing_objects(conn: sqlite3.Connection) -> set[str]:
    result = set()
    mappings = {
        "state": "tehm_states", "transition": "tehm_transitions",
        "episode": "tehm_episodes", "rule": "tehm_rules",
        "causal_path": "tehm_causal_paths", "asset": "tehm_assets",
        "capability": "tehm_capabilities", "activation": "tehm_activations",
    }
    id_columns = {
        "state": "state_id", "transition": "transition_id", "episode": "episode_id",
        "rule": "rule_id", "causal_path": "path_id", "asset": "asset_id",
        "capability": "capability_id", "activation": "activation_id",
    }
    for object_type, table in mappings.items():
        if not _table_exists(conn, table):
            continue
        result.update(_object_key(object_type, str(row[0])) for row in conn.execute(
            f"SELECT {id_columns[object_type]} FROM {table}"))
    if _table_exists(conn, "tehm_mechanism_knowledge_status"):
        result.update(_object_key("knowledge", f"{row['knowledge_id']}@{row['version']}")
                      for row in conn.execute("SELECT knowledge_id, version FROM tehm_mechanism_knowledge_status"))
    return result


def _detect_cycles(relations: tuple[MemoryRelation, ...]) -> None:
    graph: dict[str, set[str]] = {}
    for relation in relations:
        if relation.relation_type not in _PRECEDENCE:
            continue
        source = _object_key(relation.source_type, relation.source_id)
        target = _object_key(relation.target_type, relation.target_id)
        graph.setdefault(source, set()).add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise StateResolutionError(f"CYCLE_CONFLICT: relation precedence cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for child in sorted(graph.get(node, ())):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)


def _resolution_digest(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()


def _persist_snapshot(conn: sqlite3.Connection, state: ResolvedMemoryState,
                      *, commit: bool) -> None:
    had_outer_transaction = conn.in_transaction
    conn.execute(
        """INSERT OR IGNORE INTO tehm_state_resolution_snapshots
           (resolution_id, input_memory_digest, scope_json, active_rules_json,
            active_paths_json, active_knowledge_json, active_assets_json,
            active_capabilities_json, suppressed_json, unresolved_conflicts_json,
            relation_ids_json, shadow_relation_ids_json, resolution_digest,
            resolver_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (state.resolution_id, state.input_memory_digest, stable_dumps(state.scope),
         stable_dumps(list(state.active_rules)), stable_dumps(list(state.active_causal_paths)),
         stable_dumps(list(state.active_knowledge_claims)), stable_dumps(list(state.active_assets)),
         stable_dumps(list(state.active_capabilities)),
         stable_dumps([item.to_dict() for item in state.suppressed]),
         stable_dumps(list(state.unresolved_conflicts)), stable_dumps(list(state.relation_ids)),
         stable_dumps(list(state.shadow_relation_ids)), state.resolution_digest,
         state.resolver_version, tehm_db.now_local()))
    row = conn.execute(
        "SELECT * FROM tehm_state_resolution_snapshots WHERE resolution_id=?",
        (state.resolution_id,)).fetchone()
    if row is None or row["resolution_digest"] != state.resolution_digest:
        raise StateResolutionError("state resolution snapshot replay conflicts")
    if commit and not had_outer_transaction:
        conn.commit()


def resolve_current_state(
    conn: sqlite3.Connection, scope: Mapping | None = None, *,
    mode: str = "shadow", persist: bool = True, commit: bool = True,
) -> ResolvedMemoryState:
    """Resolve one deterministic scope; ``mode='production'`` is strict."""
    if mode not in {"shadow", "production"}:
        raise ValueError("state resolver mode must be 'shadow' or 'production'")
    ensure_state_schema(conn, commit=False)
    requested = normalize_scope(scope)
    all_relations = load_relations(conn)
    applicable = tuple(item for item in all_relations
                       if _scope_matches(item.scope, requested))
    relation_ids = tuple(item.relation_id for item in applicable)
    shadow_ids = tuple(item.relation_id for item in applicable
                       if not _validate_authority(conn, item, requested, mode))
    authority_ids = set(relation_ids) - set(shadow_ids)
    _detect_cycles(applicable)

    existing = _existing_objects(conn)
    for relation in applicable:
        source = _object_key(relation.source_type, relation.source_id)
        target = _object_key(relation.target_type, relation.target_id)
        if source not in existing and relation.source_type != "relation":
            raise StateResolutionError(
                f"MISSING_OBJECT: relation {relation.relation_id} source {source}")
        if target not in existing and relation.target_type != "relation":
            raise StateResolutionError(
                f"MISSING_OBJECT: relation {relation.relation_id} target {target}")

    active = {
        "rule": _active_rules(conn, requested),
        "causal_path": _active_paths(conn, requested),
        "knowledge": _active_knowledge(conn, requested),
        "asset": _active_assets(conn, requested),
        "capability": _active_capabilities(conn, requested),
    }
    suppressed: list[SuppressionReceipt] = []
    unresolved: list[str] = []
    for relation in applicable:
        if relation.relation_type in _SUPPRESSING:
            target_set = active.get(relation.target_type)
            if target_set is None or relation.target_id not in target_set:
                continue
            source_set = active.get(relation.source_type)
            source_active = (source_set is None or relation.source_id in source_set or
                             relation.source_type in {"transition", "relation"})
            if not source_active:
                unresolved.append(f"SOURCE_INACTIVE:{relation.relation_id}")
                continue
            replacement = None
            reason = relation.relation_type
            if relation.relation_type == "REPLACED_BY":
                replacement = relation.target_id
                reason = "REPLACED_BY"
                target_set = active.get(relation.source_type)
                if target_set is not None and relation.source_id in target_set:
                    target_set.remove(relation.source_id)
                    suppressed.append(SuppressionReceipt(
                        relation.source_type, relation.source_id, reason,
                        relation.relation_id, relation.target_id))
                continue
            target_set.remove(relation.target_id)
            suppressed.append(SuppressionReceipt(
                relation.target_type, relation.target_id, reason,
                relation.relation_id, replacement))
        elif relation.relation_type == "CONTRADICTS":
            left = active.get(relation.source_type)
            right = active.get(relation.target_type)
            if left is not None and right is not None and relation.source_id in left and relation.target_id in right:
                left.remove(relation.source_id)
                right.remove(relation.target_id)
                unresolved.append(f"AMBIGUOUS_CURRENT_STATE:{relation.relation_id}")

    input_digest = _input_digest(conn, requested, all_relations)
    payload = {
        "input_memory_digest": input_digest, "scope": requested,
        "active_rules": sorted(active["rule"]),
        "active_paths": sorted(active["causal_path"]),
        "active_knowledge": sorted(active["knowledge"]),
        "active_assets": sorted(active["asset"]),
        "active_capabilities": sorted(active["capability"]),
        "suppressed": [item.to_dict() for item in sorted(
            suppressed, key=lambda item: (item.object_type, item.object_id, item.relation_id or ""))],
        "unresolved_conflicts": sorted(set(unresolved)),
        "relation_ids": list(relation_ids), "shadow_relation_ids": list(shadow_ids),
        "resolver_version": RESOLVER_VERSION,
    }
    digest = _resolution_digest(payload)
    resolution_id = "resolution_" + digest.split(":", 1)[1][:24]
    state = ResolvedMemoryState(
        resolution_id=resolution_id, input_memory_digest=input_digest,
        scope=requested, active_rules=tuple(sorted(active["rule"])),
        active_causal_paths=tuple(sorted(active["causal_path"])),
        active_knowledge_claims=tuple(sorted(active["knowledge"])),
        active_assets=tuple(sorted(active["asset"])),
        active_capabilities=tuple(sorted(active["capability"])),
        suppressed=tuple(sorted(suppressed, key=lambda item: (
            item.object_type, item.object_id, item.relation_id or ""))),
        unresolved_conflicts=tuple(sorted(set(unresolved))),
        relation_ids=relation_ids, shadow_relation_ids=shadow_ids,
        resolution_digest=digest, resolver_version=RESOLVER_VERSION)
    if persist:
        _persist_snapshot(conn, state, commit=commit)
    return state


def _state_from_row(row: sqlite3.Row) -> ResolvedMemoryState:
    try:
        scope = parse_json_object(row["scope_json"], "scope_json")
        active_rules = parse_json_array(row["active_rules_json"], "active_rules_json")
        active_paths = parse_json_array(row["active_paths_json"], "active_paths_json")
        active_knowledge = parse_json_array(row["active_knowledge_json"], "active_knowledge_json")
        active_assets = parse_json_array(row["active_assets_json"], "active_assets_json")
        active_capabilities = parse_json_array(row["active_capabilities_json"], "active_capabilities_json")
        suppressed_raw = parse_json_array(row["suppressed_json"], "suppressed_json")
        unresolved = parse_json_array(row["unresolved_conflicts_json"], "unresolved_conflicts_json")
        relation_ids = parse_json_array(row["relation_ids_json"], "relation_ids_json")
        shadow_ids = parse_json_array(row["shadow_relation_ids_json"], "shadow_relation_ids_json")
    except (KeyError, TypeError, ValueError) as exc:
        raise StateResolutionError("state resolution snapshot is malformed") from exc
    if any(type(value) is not str for value in (
            *active_rules, *active_paths, *active_knowledge, *active_assets,
            *active_capabilities, *unresolved, *relation_ids, *shadow_ids)):
        raise StateResolutionError("state resolution snapshot contains non-string IDs")
    suppressed = []
    for raw in suppressed_raw:
        if not isinstance(raw, dict):
            raise StateResolutionError("state resolution suppression is malformed")
        for field in ("object_type", "object_id", "reason"):
            if type(raw.get(field)) is not str or not raw[field]:
                raise StateResolutionError(
                    "state resolution suppression has invalid fields")
        for field in ("relation_id", "replacement_id"):
            if raw.get(field) is not None and type(raw[field]) is not str:
                raise StateResolutionError(
                    "state resolution suppression has invalid references")
        suppressed.append(SuppressionReceipt(
            object_type=raw.get("object_type"), object_id=raw.get("object_id"),
            reason=raw.get("reason"), relation_id=raw.get("relation_id"),
            replacement_id=raw.get("replacement_id")))
    payload = {
        "input_memory_digest": row["input_memory_digest"], "scope": scope,
        "active_rules": active_rules, "active_paths": active_paths,
        "active_knowledge": active_knowledge, "active_assets": active_assets,
        "active_capabilities": active_capabilities,
        "suppressed": suppressed_raw, "unresolved_conflicts": sorted(set(unresolved)),
        "relation_ids": relation_ids, "shadow_relation_ids": shadow_ids,
        "resolver_version": row["resolver_version"],
    }
    digest = _resolution_digest(payload)
    if digest != row["resolution_digest"]:
        raise StateResolutionError("state resolution snapshot digest mismatch")
    expected_id = "resolution_" + digest.split(":", 1)[1][:24]
    if row["resolution_id"] != expected_id:
        raise StateResolutionError("state resolution id is not content-addressed")
    if row["resolver_version"] != RESOLVER_VERSION:
        raise StateResolutionError("unsupported state resolver version")
    return ResolvedMemoryState(
        resolution_id=row["resolution_id"], input_memory_digest=row["input_memory_digest"],
        scope=scope, active_rules=tuple(active_rules), active_causal_paths=tuple(active_paths),
        active_knowledge_claims=tuple(active_knowledge), active_assets=tuple(active_assets),
        active_capabilities=tuple(active_capabilities), suppressed=tuple(suppressed),
        unresolved_conflicts=tuple(unresolved), relation_ids=tuple(relation_ids),
        shadow_relation_ids=tuple(shadow_ids), resolution_digest=digest,
        resolver_version=row["resolver_version"])


def load_resolution_snapshot(conn: sqlite3.Connection, resolution_id: str) -> ResolvedMemoryState:
    ensure_state_schema(conn, commit=False)
    row = conn.execute(
        "SELECT * FROM tehm_state_resolution_snapshots WHERE resolution_id=?",
        (resolution_id,)).fetchone()
    if row is None:
        raise StateResolutionError("state resolution snapshot not found")
    return _state_from_row(row)


def verify_resolution_snapshot(conn: sqlite3.Connection, resolution_id: str) -> StateResolutionReceipt:
    """Replay a snapshot against current inputs without writing any row."""
    stored = load_resolution_snapshot(conn, resolution_id)
    current = resolve_current_state(conn, stored.scope, mode="shadow", persist=False)
    if current.resolution_digest != stored.resolution_digest:
        raise StateResolutionError("state resolution replay differs from snapshot")
    return StateResolutionReceipt(
        resolution_id=stored.resolution_id,
        input_memory_digest=stored.input_memory_digest,
        resolution_digest=stored.resolution_digest,
        relation_count=len(stored.relation_ids),
        unresolved_conflicts=stored.unresolved_conflicts)


resolve_state = resolve_current_state


__all__ = [
    "RESOLVER_VERSION", "StateResolutionError", "load_resolution_snapshot",
    "resolve_current_state", "resolve_state", "verify_resolution_snapshot",
]
