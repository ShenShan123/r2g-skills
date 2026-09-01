"""Isolated executor for localized online shadow updates (P13).

``local_revision`` deliberately stops at a plan.  This module is the next
boundary: it applies a typed plan only to an in-memory SQLite backup, resolves
the resulting shadow state, and discards that backup.  The source connection,
canonical evidence, lifecycle authority, and production runtime are never
mutated.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tehm import db as tehm_db
from tehm.assets.registry import register_asset, set_asset_status
from tehm.capability.registry import register_capability
from tehm.ids import stable_dumps
from tehm.state.relations import record_relation
from tehm.state.resolver import resolve_current_state
from tehm.state.schema import ensure_state_schema
from tehm.state.validation import RELATION_TYPES

from .anti_forgetting import raw_evidence_digest
from .incremental_crystallize import crystallize_affected_groups
from .local_revision import LocalizedUpdatePlan
from .p12_shadow_trigger import P12ShadowTriggerError, P12ShadowUpdateTriggerReceipt
from .verification import require_verified_transition


SHADOW_UPDATE_VERSION = "shadow-update-v0.1"
_MUTATING_TARGETS = frozenset({
    "UPDATE_STATE_RELATION", "UPDATE_CAUSAL_KNOWLEDGE", "UPDATE_RULE",
    "UPDATE_ASSET", "UPDATE_CAPABILITY",
})
_RELATION_FOR_OPERATION = {
    "SUPERSEDE": "SUPERSEDES",
    "INVALIDATE": "INVALIDATES",
}


class ShadowUpdateError(RuntimeError):
    """A localized update cannot be applied inside the shadow boundary."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _connection_digest(conn: sqlite3.Connection) -> str:
    """Hash the logical SQLite state, including derived shadow rows."""
    return _digest("\n".join(conn.iterdump()))


def _staging_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    staging = sqlite3.connect(":memory:")
    staging.row_factory = sqlite3.Row
    staging.execute("PRAGMA foreign_keys=ON")
    conn.backup(staging)
    return staging


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _inventory(conn: sqlite3.Connection) -> set[str]:
    tables = {
        "state": ("tehm_states", "state_id"),
        "transition": ("tehm_transitions", "transition_id"),
        "episode": ("tehm_episodes", "episode_id"),
        "rule": ("tehm_rules", "rule_id"),
        "rule_revision": ("tehm_rule_revisions", "revision_id"),
        "causal_path": ("tehm_causal_paths", "path_id"),
        "knowledge": ("tehm_mechanism_knowledge", "knowledge_id"),
        "asset": ("tehm_assets", "asset_id"),
        "capability": ("tehm_capabilities", "capability_id"),
    }
    found: set[str] = set()
    for object_type, (table, column) in tables.items():
        if not _table_exists(conn, table):
            continue
        found.update(
            f"{object_type}:{row[0]}"
            for row in conn.execute(f"SELECT {column} FROM {table}")
        )
    return found


def _relation_inventory(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "tehm_memory_relations"):
        return set()
    return {str(row[0]) for row in conn.execute(
        "SELECT relation_id FROM tehm_memory_relations")}


def _strings(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ShadowUpdateError(f"shadow update {name} must be a sequence")
    values = tuple(value)
    if any(type(item) is not str or not item.strip() for item in values):
        raise ShadowUpdateError(f"shadow update {name} contains invalid IDs")
    values = tuple(sorted(item.strip() for item in values))
    if not allow_empty and not values:
        raise ShadowUpdateError(f"shadow update {name} must not be empty")
    if len(set(values)) != len(values):
        raise ShadowUpdateError(f"shadow update {name} contains duplicates")
    return values


def _transition_ids(plan: LocalizedUpdatePlan, evidence: Mapping) -> tuple[str, ...]:
    raw = evidence.get("transition_ids")
    if raw is None:
        raw = plan.evidence_refs
    ids = _strings(raw, "transition_ids")
    if plan.evidence_refs and not set(ids) <= set(plan.evidence_refs):
        raise ShadowUpdateError(
            "shadow update transition evidence is outside the plan witness")
    return ids


def _verify_training_transitions(conn: sqlite3.Connection,
                                 transition_ids: Sequence[str],
                                 campaign_id: str) -> None:
    placeholders = ",".join("?" for _ in transition_ids)
    rows = conn.execute(
        f"""SELECT transition_id, split, learner_eligible
              FROM tehm_dataset_membership
             WHERE campaign_id=? AND transition_id IN ({placeholders})""",
        (campaign_id, *transition_ids)).fetchall()
    by_id = {str(row["transition_id"]): row for row in rows}
    if len(by_id) != len(set(transition_ids)):
        raise ShadowUpdateError(
            "shadow update evidence lacks campaign membership")
    for transition_id in transition_ids:
        row = by_id[transition_id]
        if row["split"] != "training" or row["learner_eligible"] != 1:
            raise ShadowUpdateError(
                "shadow update evidence must be learner-eligible training data")
        try:
            require_verified_transition(conn, transition_id)
        except ValueError as exc:
            raise ShadowUpdateError(str(exc)) from exc


def _scope(evidence: Mapping) -> dict:
    value = evidence.get("scope") or {}
    if not isinstance(value, Mapping):
        raise ShadowUpdateError("shadow update scope must be an object")
    try:
        decoded = json.loads(stable_dumps(dict(value)))
    except (TypeError, ValueError) as exc:
        raise ShadowUpdateError("shadow update scope is not JSON-serializable") from exc
    if not isinstance(decoded, dict):  # pragma: no cover
        raise ShadowUpdateError("shadow update scope must be an object")
    return decoded


def _p12_shadow_trigger(plan: LocalizedUpdatePlan,
                        evidence: Mapping) -> P12ShadowUpdateTriggerReceipt | None:
    """Validate the explicit P12 witness before applying a P13 shadow plan."""
    raw = evidence.get("p12_shadow_trigger")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ShadowUpdateError("shadow update P12 trigger must be an object")
    try:
        trigger = P12ShadowUpdateTriggerReceipt.from_dict(raw)
    except P12ShadowTriggerError as exc:
        raise ShadowUpdateError(str(exc)) from exc
    if trigger.triggered is not True:
        raise ShadowUpdateError(
            "shadow update cannot consume a non-triggering P12 receipt")
    if trigger.campaign_id != plan.campaign_id:
        raise ShadowUpdateError(
            "shadow update P12 trigger campaign does not match plan")
    if trigger.learner_eligible is not True or plan.learner_eligible is not True:
        raise ShadowUpdateError(
            "shadow update P12 trigger requires learner-eligible plan evidence")
    if trigger.receipt_digest not in plan.evidence_refs:
        raise ShadowUpdateError(
            "shadow update plan must explicitly witness the P12 trigger digest")
    return trigger


def _relation_payload(plan: LocalizedUpdatePlan, evidence: Mapping) -> dict:
    raw = evidence.get("relation")
    if not isinstance(raw, Mapping):
        raise ShadowUpdateError(
            "shadow update relation evidence is required for this target")
    relation = dict(raw)
    required = {"source_type", "source_id", "relation_type", "target_type", "target_id"}
    if not required <= set(relation):
        raise ShadowUpdateError("shadow update relation evidence is incomplete")
    relation_type = relation["relation_type"]
    if relation_type not in RELATION_TYPES:
        raise ShadowUpdateError("shadow update relation type is invalid")
    expected = _RELATION_FOR_OPERATION.get(plan.operation)
    if expected is not None and relation_type != expected:
        raise ShadowUpdateError(
            f"shadow update {plan.operation} requires relation {expected}")
    refs = relation.get("evidence_refs", plan.evidence_refs)
    relation["evidence_refs"] = list(_strings(refs, "relation evidence_refs"))
    relation["scope"] = relation.get("scope", evidence.get("scope") or {})
    relation["authority_ref"] = None
    return relation


def _apply_relation(staging: sqlite3.Connection, plan: LocalizedUpdatePlan,
                    evidence: Mapping) -> str:
    relation = _relation_payload(plan, evidence)
    try:
        receipt = record_relation(
            staging, source_type=relation["source_type"],
            source_id=relation["source_id"], relation_type=relation["relation_type"],
            target_type=relation["target_type"], target_id=relation["target_id"],
            scope=relation["scope"], evidence_refs=relation["evidence_refs"],
            authority_ref=None, created_at=evidence.get("created_at"), commit=False)
    except (TypeError, ValueError, KeyError) as exc:
        raise ShadowUpdateError(str(exc)) from exc
    return receipt.relation_id


def _apply_asset(staging: sqlite3.Connection, plan: LocalizedUpdatePlan,
                 evidence: Mapping) -> tuple[str, ...]:
    if plan.operation in {"ADD", "REVISE"}:
        raw = evidence.get("asset")
        if not isinstance(raw, Mapping):
            raise ShadowUpdateError("shadow asset update requires asset evidence")
        required = {
            "asset_type", "name", "version", "definition", "input_contract",
            "output_contract", "verifier_contract", "compatibility",
        }
        if not required <= set(raw):
            raise ShadowUpdateError("shadow asset evidence is incomplete")
        data = dict(raw)
        try:
            receipt = register_asset(
                staging, asset_type=data["asset_type"], name=data["name"],
                version=data["version"], definition=dict(data["definition"]),
                input_contract=dict(data["input_contract"]),
                output_contract=dict(data["output_contract"]),
                verifier_contract=dict(data["verifier_contract"]),
                compatibility=dict(data["compatibility"]),
                provenance={**dict(data.get("provenance") or {}),
                            "shadow_update": plan.plan_digest},
                target_scope=data.get("target_scope"), commit=False)
            set_asset_status(
                staging, asset_id=receipt.asset_id, target_scope=receipt.target_scope,
                status="shadow", provenance={
                    "authority": "shadow_update", "plan_digest": plan.plan_digest,
                    "campaign_id": plan.campaign_id,
                }, commit=False)
        except (TypeError, ValueError, KeyError) as exc:
            raise ShadowUpdateError(str(exc)) from exc
        return (f"asset:{receipt.asset_id}",)
    if plan.operation in {"SUPERSEDE", "INVALIDATE"}:
        asset_id = evidence.get("asset_id")
        if asset_id is None and plan.asset_refs:
            asset_id = plan.asset_refs[0]
        if type(asset_id) is not str or not asset_id:
            raise ShadowUpdateError("shadow asset invalidation requires asset_id")
        return (_apply_relation(staging, plan, {
            **dict(evidence),
            "relation": {
                "source_type": "transition", "source_id": plan.transition_id,
                "relation_type": "INVALIDATES", "target_type": "asset",
                "target_id": asset_id, "evidence_refs": list(plan.evidence_refs),
            },
        }),)
    if plan.operation == "REACTIVATE":
        asset_id = evidence.get("asset_id")
        if asset_id is None and plan.asset_refs:
            asset_id = plan.asset_refs[0]
        target_scope = evidence.get("target_scope") or "global"
        if type(asset_id) is not str or not asset_id:
            raise ShadowUpdateError("shadow asset reactivation requires asset_id")
        try:
            set_asset_status(
                staging, asset_id=asset_id, target_scope=target_scope, status="shadow",
                provenance={"authority": "shadow_update", "plan_digest": plan.plan_digest},
                commit=False)
        except (TypeError, ValueError, KeyError) as exc:
            raise ShadowUpdateError(str(exc)) from exc
        return (f"asset:{asset_id}",)
    raise ShadowUpdateError(
        f"shadow asset operation is unsupported: {plan.operation}")


def _apply_capability(staging: sqlite3.Connection, plan: LocalizedUpdatePlan,
                      evidence: Mapping) -> tuple[str, ...]:
    if plan.operation in {"ADD", "REVISE"}:
        raw = evidence.get("capability")
        if not isinstance(raw, Mapping):
            raise ShadowUpdateError(
                "shadow capability update requires capability evidence")
        required = {"mechanism_family", "applicability"}
        if not required <= set(raw):
            raise ShadowUpdateError("shadow capability evidence is incomplete")
        try:
            receipt = register_capability(
                staging, mechanism_family=raw["mechanism_family"],
                applicability=raw["applicability"],
                required_rules=list(raw.get("required_rules") or ()),
                required_assets=list(raw.get("required_assets") or ()),
                obligations=dict(raw.get("obligations") or {}),
                budget=dict(raw.get("budget") or {}), status="candidate",
                version=raw.get("version", 1),
                provenance={**dict(raw.get("provenance") or {}),
                            "shadow_update": plan.plan_digest}, commit=False)
        except (TypeError, ValueError, KeyError) as exc:
            raise ShadowUpdateError(str(exc)) from exc
        return (f"capability:{receipt.capability_id}",)
    if plan.operation in {"SUPERSEDE", "INVALIDATE"}:
        capability_id = evidence.get("capability_id")
        if capability_id is None and plan.capability_refs:
            capability_id = plan.capability_refs[0]
        if type(capability_id) is not str or not capability_id:
            raise ShadowUpdateError("shadow capability invalidation requires capability_id")
        return (_apply_relation(staging, plan, {
            **dict(evidence),
            "relation": {
                "source_type": "transition", "source_id": plan.transition_id,
                "relation_type": "INVALIDATES", "target_type": "capability",
                "target_id": capability_id, "evidence_refs": list(plan.evidence_refs),
            },
        }),)
    raise ShadowUpdateError(
        f"shadow capability operation is unsupported: {plan.operation}")


@dataclass(frozen=True)
class AppliedShadowUpdateReceipt:
    """Content-addressed evidence that a plan was applied and discarded in staging."""

    plan_digest: str
    transition_id: str
    campaign_id: str
    update_target: str
    operation: str
    created_object_ids: tuple[str, ...]
    created_relation_ids: tuple[str, ...]
    before_resolution_id: str
    after_resolution_id: str
    canonical_rows_changed: bool
    production_authority_changed: bool
    replay_digest: str
    source_digest_before: str
    source_digest_after: str
    staging_digest_before: str
    staging_digest_after: str
    raw_evidence_before_digest: str
    raw_evidence_after_digest: str
    raw_evidence_preserved: bool
    staging_discarded: bool = True
    canonical_memory_mutation: str = "none"
    lifecycle_mutation: str = "isolated_staging_only"
    production_runtime_imported: bool = False
    version: str = SHADOW_UPDATE_VERSION
    metadata: dict = field(default_factory=dict)

    def _payload(self) -> dict:
        return {
            "version": self.version, "plan_digest": self.plan_digest,
            "transition_id": self.transition_id, "campaign_id": self.campaign_id,
            "update_target": self.update_target, "operation": self.operation,
            "created_object_ids": list(self.created_object_ids),
            "created_relation_ids": list(self.created_relation_ids),
            "before_resolution_id": self.before_resolution_id,
            "after_resolution_id": self.after_resolution_id,
            "canonical_rows_changed": self.canonical_rows_changed,
            "production_authority_changed": self.production_authority_changed,
            "source_digest_before": self.source_digest_before,
            "source_digest_after": self.source_digest_after,
            "staging_digest_before": self.staging_digest_before,
            "staging_digest_after": self.staging_digest_after,
            "raw_evidence_before_digest": self.raw_evidence_before_digest,
            "raw_evidence_after_digest": self.raw_evidence_after_digest,
            "raw_evidence_preserved": self.raw_evidence_preserved,
            "staging_discarded": self.staging_discarded,
            "canonical_memory_mutation": self.canonical_memory_mutation,
            "lifecycle_mutation": self.lifecycle_mutation,
            "production_runtime_imported": self.production_runtime_imported,
            "metadata": self.metadata,
        }

    def __post_init__(self) -> None:
        for name in ("plan_digest", "source_digest_before", "source_digest_after",
                     "staging_digest_before", "staging_digest_after",
                     "raw_evidence_before_digest", "raw_evidence_after_digest",
                     "before_resolution_id", "after_resolution_id",
                     "transition_id", "campaign_id", "update_target", "operation"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ShadowUpdateError(f"shadow update receipt {name} is invalid")
        if self.update_target not in _MUTATING_TARGETS | {"UPDATE_NONE"}:
            raise ShadowUpdateError("shadow update receipt target is invalid")
        if self.canonical_rows_changed or self.production_authority_changed:
            raise ShadowUpdateError("shadow update receipt crosses authority boundary")
        if self.staging_discarded is not True or self.production_runtime_imported is not False:
            raise ShadowUpdateError("shadow update staging safety flags are invalid")
        if self.canonical_memory_mutation != "none" or self.lifecycle_mutation != "isolated_staging_only":
            raise ShadowUpdateError("shadow update mutation scope is invalid")
        if self.raw_evidence_preserved is not True:
            raise ShadowUpdateError("shadow update did not preserve canonical evidence")
        if not isinstance(self.created_object_ids, tuple) or not isinstance(self.created_relation_ids, tuple):
            raise ShadowUpdateError("shadow update receipt IDs are invalid")
        if not isinstance(self.metadata, dict):
            raise ShadowUpdateError("shadow update receipt metadata is invalid")
        expected = _digest(self._payload())
        if self.replay_digest != expected:
            raise ShadowUpdateError("shadow update receipt replay digest mismatch")

    @property
    def receipt_digest(self) -> str:
        # Do not hash ``to_dict()`` here: that method includes this property.
        return _digest({**self._payload(), "replay_digest": self.replay_digest})

    def to_dict(self) -> dict:
        return {**self._payload(), "replay_digest": self.replay_digest,
                "receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, payload: object) -> "AppliedShadowUpdateReceipt":
        if not isinstance(payload, Mapping):
            raise ShadowUpdateError("shadow update receipt must be an object")
        required = set(cls.__dataclass_fields__) - {"version", "metadata"}
        if not required <= set(payload):
            raise ShadowUpdateError("shadow update receipt is missing fields")
        receipt = cls(
            plan_digest=payload["plan_digest"], transition_id=payload["transition_id"],
            campaign_id=payload["campaign_id"], update_target=payload["update_target"],
            operation=payload["operation"],
            created_object_ids=tuple(payload["created_object_ids"]),
            created_relation_ids=tuple(payload["created_relation_ids"]),
            before_resolution_id=payload["before_resolution_id"],
            after_resolution_id=payload["after_resolution_id"],
            canonical_rows_changed=payload["canonical_rows_changed"],
            production_authority_changed=payload["production_authority_changed"],
            replay_digest=payload["replay_digest"],
            source_digest_before=payload["source_digest_before"],
            source_digest_after=payload["source_digest_after"],
            staging_digest_before=payload["staging_digest_before"],
            staging_digest_after=payload["staging_digest_after"],
            raw_evidence_before_digest=payload["raw_evidence_before_digest"],
            raw_evidence_after_digest=payload["raw_evidence_after_digest"],
            raw_evidence_preserved=payload["raw_evidence_preserved"],
            staging_discarded=payload["staging_discarded"],
            canonical_memory_mutation=payload["canonical_memory_mutation"],
            lifecycle_mutation=payload["lifecycle_mutation"],
            production_runtime_imported=payload["production_runtime_imported"],
            version=payload.get("version", SHADOW_UPDATE_VERSION),
            metadata=dict(payload.get("metadata") or {}),
        )
        supplied = payload.get("receipt_digest")
        if supplied is not None and supplied != receipt.receipt_digest:
            raise ShadowUpdateError("shadow update receipt digest mismatch")
        return receipt


def _apply_plan(staging: sqlite3.Connection, plan: LocalizedUpdatePlan,
                evidence: Mapping) -> None:
    if plan.update_target == "UPDATE_NONE":
        if plan.operation != "RETAIN":
            raise ShadowUpdateError("UPDATE_NONE must remain RETAIN")
        return
    if plan.update_target not in _MUTATING_TARGETS:
        raise ShadowUpdateError("shadow update target is invalid")
    if plan.operation == "RETAIN":
        raise ShadowUpdateError("mutating shadow target cannot use RETAIN")
    if plan.update_target in {"UPDATE_CAUSAL_KNOWLEDGE", "UPDATE_RULE"}:
        ids = _transition_ids(plan, evidence)
        _verify_training_transitions(staging, ids, plan.campaign_id)
        if plan.operation not in {"ADD", "REVISE"}:
            raise ShadowUpdateError(
                "causal/rule shadow crystallization supports ADD or REVISE only")
        try:
            report = crystallize_affected_groups(
                staging, ids, campaign_id=plan.campaign_id, created_at=evidence.get("created_at"))
        except (TypeError, ValueError, RuntimeError) as exc:
            raise ShadowUpdateError(str(exc)) from exc
        if not report.rules:
            raise ShadowUpdateError("shadow crystallization produced no rule")
        return
    if plan.update_target == "UPDATE_STATE_RELATION":
        _apply_relation(staging, plan, evidence)
        return
    if plan.update_target == "UPDATE_ASSET":
        _apply_asset(staging, plan, evidence)
        return
    if plan.update_target == "UPDATE_CAPABILITY":
        _apply_capability(staging, plan, evidence)
        return
    raise ShadowUpdateError("unsupported shadow update target")


def apply_localized_update_shadow(
        plan: LocalizedUpdatePlan,
        current_state: sqlite3.Connection,
        evidence: Mapping | None = None) -> AppliedShadowUpdateReceipt:
    """Apply one localized plan to staging and discard it.

    ``evidence`` is intentionally explicit.  Causal/rule updates require
    learner-eligible verified ``transition_ids``; relation/asset/capability
    updates require typed payloads.  Caller booleans are never interpreted as
    authority, and the source connection is checked byte-for-byte by logical
    SQLite digest before returning.
    """
    if not isinstance(plan, LocalizedUpdatePlan):
        raise TypeError("shadow update requires LocalizedUpdatePlan")
    if plan.shadow_only is not True:
        raise ShadowUpdateError("shadow update plan must be shadow-only")
    if plan.update_target != "UPDATE_NONE" and plan.learner_eligible is not True:
        raise ShadowUpdateError(
            "mutating shadow update requires learner-eligible evidence")
    if not isinstance(current_state, sqlite3.Connection):
        raise TypeError("shadow update current_state must be sqlite3.Connection")
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, Mapping):
        raise ShadowUpdateError("shadow update evidence must be an object")
    p12_trigger = _p12_shadow_trigger(plan, evidence)
    source_before = _connection_digest(current_state)
    raw_before = raw_evidence_digest(current_state)
    staging = _staging_copy(current_state)
    ensure_state_schema(staging, commit=False)
    staging_before = _connection_digest(staging)
    inventory_before = _inventory(staging)
    relation_before = _relation_inventory(staging)
    scope = _scope(evidence)
    savepoint = "tehm_shadow_update_v1"
    staging.execute(f"SAVEPOINT {savepoint}")
    try:
        before = resolve_current_state(staging, scope, mode="shadow", persist=False)
        if (plan.state_resolution_id is not None and
                plan.state_resolution_id != before.resolution_id):
            raise ShadowUpdateError(
                "shadow update plan state resolution does not match current state")
        _apply_plan(staging, plan, evidence)
        after = resolve_current_state(staging, scope, mode="shadow", persist=False)
        raw_after = raw_evidence_digest(staging)
        if raw_after != raw_before:
            raise ShadowUpdateError("shadow update changed canonical evidence in staging")
        staging.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        staging.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        staging.execute(f"RELEASE SAVEPOINT {savepoint}")
        staging.close()
        raise
    staging_after = _connection_digest(staging)
    created_objects = tuple(sorted(_inventory(staging) - inventory_before))
    created_relations = tuple(sorted(_relation_inventory(staging) - relation_before))
    source_after = _connection_digest(current_state)
    raw_source_after = raw_evidence_digest(current_state)
    try:
        if source_after != source_before:
            raise ShadowUpdateError("source TEHM connection changed during shadow update")
        if raw_source_after != raw_before:
            raise ShadowUpdateError("source canonical evidence changed during shadow update")
        payload = {
            "version": SHADOW_UPDATE_VERSION, "plan_digest": plan.plan_digest,
            "transition_id": plan.transition_id, "campaign_id": plan.campaign_id,
            "update_target": plan.update_target, "operation": plan.operation,
            "created_object_ids": tuple(created_objects),
            "created_relation_ids": tuple(created_relations),
            "before_resolution_id": before.resolution_id,
            "after_resolution_id": after.resolution_id,
            "canonical_rows_changed": False, "production_authority_changed": False,
            "source_digest_before": source_before, "source_digest_after": source_after,
            "staging_digest_before": staging_before, "staging_digest_after": staging_after,
            "raw_evidence_before_digest": raw_before,
            "raw_evidence_after_digest": raw_after,
            "raw_evidence_preserved": True,
            "staging_discarded": True, "canonical_memory_mutation": "none",
            "lifecycle_mutation": "isolated_staging_only",
            "production_runtime_imported": False,
            "metadata": {
                "scope": scope,
                "shadow_update_version": SHADOW_UPDATE_VERSION,
                **({"p12_shadow_trigger_digest": p12_trigger.receipt_digest}
                   if p12_trigger is not None else {}),
            },
        }
        receipt = AppliedShadowUpdateReceipt(
            **payload, replay_digest=_digest(payload))
    finally:
        staging.close()
    return receipt


__all__ = [
    "SHADOW_UPDATE_VERSION", "ShadowUpdateError",
    "AppliedShadowUpdateReceipt", "apply_localized_update_shadow",
]
