"""Independent, read-only coverage witness for the P15 calibration oracle.

This is a coverage proof for the strict knowledge-grounded asset *evaluation*
universe, not an assertion that no possible repair exists. It does not import
the router or selector, accept their predictions, inspect outcomes, or grant
authority. Missing/corrupt source inventories and ambiguous state are not empty
coverage. A matching validated claim whose transfer support cannot be replayed
is uncertainty, not evidence of NO_MATCH.

The initial proof establishes absence of usable mechanism knowledge. Presence
of transferable knowledge does not establish a bound executable candidate;
source/flow binding coverage remains a separate obligation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, fields

from contracts import MemoryQuery
from tehm import config
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.schema import ASSET_TYPES, validate_independent_verifier
from tehm.causal.evidence_level import at_least
from tehm.causal.path_builder import validate_persisted_path_row
from tehm.ids import stable_dumps
from tehm.knowledge.applicability import evaluate_applicability
from tehm.knowledge.authority import evaluate_knowledge_authority
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.schema_contract import freeze_schema_contract
from tehm.state.relations import load_relations
from tehm.state.resolver import resolve_current_state
from tehm.state.validation import STATE_AFFECTING_RELATIONS

SOURCE_COVERAGE_VERSION = "no-skill-source-coverage-v1"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN = frozenset({
    "routing_decision", "router_prediction", "predicted_decision", "predicted_reason",
    "expected_decision", "expected_reason", "oracle_label", "should_use_memory",
    "no_skill_reason", "confidence", "outcome", "oracle_outcome", "gold_patch",
    "fix", "repaired_rtl", "heldout_answer", "shadow_after_state", "mutation_plan",
    "state_shift_receipt", "risk_receipt", "out_of_distribution", "memory_interference",
    "expected_utility", "memory_budget", "no_memory_budget", "routing_receipt_id",
})
_PROFILE = {
    "profile_id": "strict-knowledge-grounded-asset-evaluation-v1",
    "mode": "shadow", "compatibility_mode": False,
    "legacy_candidate_fallback": False, "knowledge_status": "validated",
    "production_runtime": False,
}


class SourceCoverageError(ValueError):
    """A supplied coverage witness cannot establish source-bound absence."""


def _json(value):
    def reject_constant(item):
        raise SourceCoverageError(f"coverage requires finite JSON values, not {item}")
    return json.loads(stable_dumps(value), parse_constant=reject_constant)


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _logical(conn):
    return _digest("\n".join(conn.iterdump()))


def _forbidden(value):
    if isinstance(value, Mapping):
        forbidden_keys = {re.sub(r"[_\s-]", "", key.lower()) for key in _FORBIDDEN}
        return any(re.sub(r"[_\s-]", "", str(key).lower()) in forbidden_keys or _forbidden(item)
                   for key, item in value.items())
    return isinstance(value, (list, tuple)) and any(_forbidden(item) for item in value)


def _query(query):
    if not isinstance(query, MemoryQuery) or _forbidden(query.to_dict()):
        raise SourceCoverageError("coverage requires a source-only typed query, not predictions/outcomes")
    value = _json(query.to_dict())
    if not isinstance(value["dominant_dimensions"], dict) or (
            value["context_ref"] is not None and type(value["context_ref"]) is not str):
        raise SourceCoverageError("coverage query requires typed dimensions/context reference")
    plan = value["query_plan"]
    if not isinstance(plan, dict):
        raise SourceCoverageError("coverage query plan must be an object")
    # No inferred aliases: the precise ex-ante mechanism/profile/scope must be
    # frozen by the caller, not supplied after observing a router refusal.
    scope = {}
    for key in ("mechanism_family", "compatibility_profile", "target_scope"):
        item = plan.get(key)
        if key == "compatibility_profile" and key in plan and item is None:
            # Explicitly unbounded coverage, not a profile inferred from a
            # router-selected asset. All profile-specific claims must then
            # remain in the independently enumerated candidate universe.
            scope[key] = None
            continue
        if type(item) is not str or not item.strip() or item != item.strip():
            raise SourceCoverageError(f"coverage query requires explicit {key}")
        scope[key] = item
    return value, scope


@dataclass(frozen=True)
class NoSkillSourceCoverageReceipt:
    campaign_id: str
    case_id: str
    source_memory_digest: str
    source_query: dict
    source_schema_digest: str
    inventory: dict
    resolution: dict
    audit: dict
    version: str = SOURCE_COVERAGE_VERSION
    split: str = "calibration"
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"
    training_support_update: str = "none"
    production_authority: bool = False

    def __post_init__(self):
        if (self.version != SOURCE_COVERAGE_VERSION or self.split != "calibration" or
                self.evaluation_only is not True or self.production_authority is not False or
                self.canonical_memory_mutation != "none" or self.training_support_update != "none"):
            raise SourceCoverageError("coverage is calibration-only, without learner/production authority")
        for name in ("campaign_id", "case_id"):
            item = getattr(self, name)
            if type(item) is not str or not item.strip() or item != item.strip():
                raise SourceCoverageError(f"coverage requires {name}")
        for name in ("source_memory_digest", "source_schema_digest"):
            if not isinstance(getattr(self, name), str) or not _SHA.fullmatch(getattr(self, name)):
                raise SourceCoverageError(f"coverage requires exact {name}")
        for name in ("source_query", "inventory", "resolution", "audit"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise SourceCoverageError(f"coverage requires object {name}")
            object.__setattr__(self, name, _json(dict(value)))
        _query(MemoryQuery(**self.source_query))
        if stable_dumps(self.audit.get("runtime_profile")) != stable_dumps(_PROFILE):
            raise SourceCoverageError("coverage cannot be used for compatibility or production profiles")

    def to_dict(self):
        return _json({field.name: getattr(self, field.name) for field in fields(self)})

    @property
    def receipt_digest(self):
        return _digest(self.to_dict())

    @property
    def receipt_id(self):
        return "no_skill_coverage_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise SourceCoverageError("coverage receipt must be an object")
        names = {field.name for field in fields(cls)}
        if not names <= value.keys() or set(value) - names - {"receipt_id", "receipt_digest"}:
            raise SourceCoverageError("coverage receipt has missing/extra fields")
        result = cls(**{name: value[name] for name in names})
        if (value.get("receipt_id", result.receipt_id) != result.receipt_id or
                value.get("receipt_digest", result.receipt_digest) != result.receipt_digest):
            raise SourceCoverageError("coverage receipt digest mismatch")
        return result


def _schema(conn):
    # The public shipped contract is introspected in an isolated RAM store.
    # Never repair the inspected source's schema to make it look complete.
    expected = freeze_schema_contract()["receipt"]["schema_objects"]
    observed = [{"type": r[0], "name": r[1], "table": r[2],
                 "sql": " ".join(r[3].split()) if r[3] is not None else None}
                for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','index','trigger','view') "
                    "ORDER BY type,name")]
    actual = {(row["type"], row["name"]): row for row in observed}
    problems = [f"schema_missing_or_altered:{row['name']}" for row in expected
                if actual.get((row["type"], row["name"])) != row]
    if not problems:
        version = conn.execute("SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != f"tehm-v{config.DB_SCHEMA_VERSION}":
            problems.append("schema_version_mismatch")
    return _digest({"expected": expected, "observed": observed}), problems


def derive_no_skill_source_coverage(conn: sqlite3.Connection, *, campaign_id: str,
        case_id: str, query: MemoryQuery, split: str = "calibration") -> NoSkillSourceCoverageReceipt:
    """Enumerate the actual source without router/selector or execution inputs.

    ``absence_established`` requires a complete valid inventory and resolved
    state with no applicable validated knowledge. Any matching claim with
    uncertain applicability/authority blocks the absence proof. Binding-level
    absence is not inferred from this knowledge-level witness.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise SourceCoverageError("coverage requires an actual SQLite source")
    if split != "calibration":
        raise SourceCoverageError("coverage is restricted to calibration split")
    source_query, scope = _query(query)
    before, prior = _logical(conn), conn.execute("PRAGMA query_only").fetchone()[0]
    inventory, resolution, support, uncertainties = {}, {}, [], []
    conn.execute("PRAGMA query_only=ON")
    try:
        schema_digest, errors = _schema(conn)
        if not errors:
            if any(row[0] != "ok" for row in conn.execute("PRAGMA integrity_check")):
                errors.append("sqlite_integrity_check_failed")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                errors.append("sqlite_foreign_key_check_failed")
            try:
                # Full registries, not only the router's selected IDs. Missing
                # status rows, orphan statuses and corrupt unrelated objects
                # must not disappear behind a family/profile filter.
                claims = {}
                for row in conn.execute("SELECT knowledge_id,version FROM tehm_mechanism_knowledge ORDER BY knowledge_id,version"):
                    identity = f"{row[0]}@{row[1]}"
                    statuses = conn.execute("SELECT target_scope FROM tehm_mechanism_knowledge_status "
                        "WHERE knowledge_id=? AND version=? ORDER BY target_scope", tuple(row)).fetchall()
                    if not statuses:
                        raise SourceCoverageError(f"knowledge_status_missing:{identity}")
                    if any(type(s[0]) is not str or not s[0].strip() or s[0] != s[0].strip() for s in statuses):
                        raise SourceCoverageError(f"knowledge_status_scope_invalid:{identity}")
                    variants = [get_knowledge_by_object_id(conn, identity, target_scope=s[0]) for s in statuses]
                    claims[identity] = {s[0]: claim for s, claim in zip(statuses, variants)}
                all_status_ids = {f"{r[0]}@{r[1]}" for r in conn.execute("SELECT knowledge_id,version FROM tehm_mechanism_knowledge_status")}
                if all_status_ids - claims.keys():
                    raise SourceCoverageError("orphan_knowledge_status")
                assets = {}
                for row in conn.execute("SELECT asset_id FROM tehm_assets ORDER BY asset_id"):
                    asset = get_asset(conn, row[0])
                    if asset is None:
                        raise SourceCoverageError(f"asset_content_invalid:{row[0]}")
                    if asset["asset_type"] not in ASSET_TYPES:
                        raise SourceCoverageError(f"asset_type_invalid:{row[0]}")
                    validate_independent_verifier(asset["verifier_contract"], asset["provenance"])
                    scopes = conn.execute("SELECT target_scope,status_version,provenance_json FROM tehm_asset_status WHERE asset_id=? ORDER BY target_scope", (row[0],)).fetchall()
                    if not scopes:
                        raise SourceCoverageError(f"asset_status_missing:{row[0]}")
                    if any(type(s[0]) is not str or not s[0].strip() or s[0] != s[0].strip() for s in scopes):
                        raise SourceCoverageError(f"asset_status_scope_invalid:{row[0]}")
                    if any(type(s[1]) is not int or s[1] < 1 or not isinstance(json.loads(s[2]), dict) for s in scopes):
                        raise SourceCoverageError(f"asset_status_metadata_invalid:{row[0]}")
                    statuses = [get_asset_status(conn, asset_id=row[0], target_scope=s[0]) for s in scopes]
                    if any(type(s["status_version"]) is not int or s["status_version"] < 1 or
                           type(s["updated_at"]) is not str or not s["updated_at"] for s in statuses):
                        raise SourceCoverageError(f"asset_status_metadata_invalid:{row[0]}")
                    assets[row[0]] = {"content_digest": asset["content_digest"], "statuses": statuses}
                if {r[0] for r in conn.execute("SELECT asset_id FROM tehm_asset_status")} - assets.keys():
                    raise SourceCoverageError("orphan_asset_status")
                state = resolve_current_state(conn, scope, mode="shadow", persist=False, commit=False)
                resolution = state.to_dict()
                if state.unresolved_conflicts:
                    errors.append("unresolved_state_conflicts")
                unverified_state_edges = [relation.relation_id for relation in load_relations(conn)
                    if relation.relation_id in state.shadow_relation_ids and
                    relation.relation_type in STATE_AFFECTING_RELATIONS]
                if unverified_state_edges:
                    # Unauthorised suppressions cannot prove that the source
                    # lacks knowledge in the independently owned universe.
                    errors.append("unverified_state_relations")
                knowledge_inventory = {}
                for identity, variants in claims.items():
                    claim = variants.get(scope["target_scope"], variants.get("global"))
                    entry = {"content_digest": next(iter(variants.values())).content_digest,
                             "statuses": {key: value.status for key, value in variants.items()}}
                    if claim is None or identity not in state.active_knowledge_claims:
                        entry["disposition"] = "not_active_in_scope"
                    elif claim.status != "validated":
                        entry["disposition"] = "not_validated"
                    elif (claim.mechanism_family != scope["mechanism_family"] or
                            (scope["compatibility_profile"] is not None and
                             claim.compatibility_profile not in {None, scope["compatibility_profile"]})):
                        entry["disposition"] = "mechanism_or_profile_mismatch"
                    else:
                        app = evaluate_applicability(claim, {**source_query["query_plan"], **scope})
                        entry["applicability"] = app.to_dict()
                        if not app.eligible:
                            entry["disposition"] = "transferability_not_established"
                            uncertainties.append(f"{identity}:{app.reason}")
                        else:
                            authority = evaluate_knowledge_authority(conn, claim)
                            entry["authority"] = authority.to_dict()
                            active_paths = []
                            for path_id in claim.causal_path_ids:
                                row = conn.execute("SELECT * FROM tehm_causal_paths WHERE path_id=?", (path_id,)).fetchone()
                                if row is not None:
                                    validate_persisted_path_row(row, conn)
                                    if path_id in state.active_causal_paths and at_least(row["evidence_level"], "L2_CONTROLLED_INTERVENTION"):
                                        active_paths.append(path_id)
                            if not authority.eligible or not active_paths:
                                entry["disposition"] = "transfer_support_not_established"
                                uncertainties.append(f"{identity}:authority_or_active_path_insufficient")
                            else:
                                entry["disposition"] = "transferable_knowledge_present"
                                entry["active_path_ids"] = sorted(active_paths)
                                support.append(identity)
                    knowledge_inventory[identity] = entry
                inventory = {"knowledge": knowledge_inventory, "assets": assets,
                             "knowledge_count": len(claims), "asset_count": len(assets)}
            except (ValueError, TypeError, sqlite3.DatabaseError, KeyError) as exc:
                errors.append(f"source_inventory_or_state_invalid:{type(exc).__name__}:{exc}")
        absence = not errors and not uncertainties and not support
        audit = {"runtime_profile": _PROFILE, "inventory_complete": not errors,
                 "absence_established": absence, "transferable_knowledge_ids": sorted(support),
                 "uncertainties": sorted(uncertainties), "errors": sorted(errors),
                 "proof_kind": "NO_USABLE_MECHANISM_KNOWLEDGE" if absence else None,
                 "candidate_binding_coverage": "NOT_ESTABLISHED",
                 "router_prediction_used": False, "execution_outcome_used": False}
        return NoSkillSourceCoverageReceipt(campaign_id, case_id, before, source_query,
            schema_digest, inventory, resolution, audit)
    finally:
        conn.execute(f"PRAGMA query_only={int(prior)}")
        if _logical(conn) != before:
            raise SourceCoverageError("source changed during read-only coverage derivation")


def verify_no_skill_source_coverage(conn, receipt, *, query: MemoryQuery):
    """Re-derive from actual source; a rehashed absence flag is not evidence."""
    try:
        item = receipt if isinstance(receipt, NoSkillSourceCoverageReceipt) else NoSkillSourceCoverageReceipt.from_dict(receipt)
        actual = derive_no_skill_source_coverage(conn, campaign_id=item.campaign_id,
            case_id=item.case_id, query=query, split=item.split)
        verified = stable_dumps(actual.to_dict()) == stable_dumps(item.to_dict())
        return {"verified": verified,
                "absence_established": verified and actual.audit["absence_established"] is True,
                "receipt_id": item.receipt_id, "receipt_digest": item.receipt_digest,
                "reason": "actual_source_replay" if verified else "source_or_query_replay_mismatch"}
    except (ValueError, TypeError, sqlite3.DatabaseError, KeyError) as exc:
        return {"verified": False, "absence_established": False,
                "reason": f"invalid_source_coverage:{type(exc).__name__}:{exc}"}


def bind_no_skill_source_coverage_case(frozen_case: Mapping, *, query: MemoryQuery,
        receipt: NoSkillSourceCoverageReceipt) -> dict:
    """Freeze ex-ante coverage into the actual executor's case digest.

    Use the returned case *before* execute_paired_candidates. Retrofitting a
    coverage ID into an already executed case will not match its case digest.
    Callers still own genuine pre-outcome execution and split membership.
    """
    source_query, _ = _query(query)
    if (not isinstance(frozen_case, Mapping) or not isinstance(receipt, NoSkillSourceCoverageReceipt) or
            frozen_case.get("case_id") != receipt.case_id or
            stable_dumps(source_query) != stable_dumps(receipt.source_query) or
            frozen_case.get("split") != "calibration" or frozen_case.get("learner_eligible") is not False or
            frozen_case.get("campaign_id") != receipt.campaign_id):
        raise SourceCoverageError("coverage must bind the exact non-learner calibration case/query")
    case = _json(dict(frozen_case))
    binding = {"receipt_id": receipt.receipt_id, "receipt_digest": receipt.receipt_digest,
               "source_memory_digest": receipt.source_memory_digest,
               "source_query_digest": _digest(source_query), "runtime_profile": _PROFILE}
    if ("p15_source_coverage" in case and stable_dumps(case["p15_source_coverage"]) != stable_dumps(binding)) or (
            "memory_query" in case and stable_dumps(case["memory_query"]) != stable_dumps(source_query)):
        raise SourceCoverageError("cannot replace a consumed source coverage/query binding")
    case.update(p15_source_coverage=binding, memory_query=source_query)
    # The runtime profile is an internal contract. Never expose its mutable
    # dict through a returned case (or one caller could change future proofs).
    return _json(case)


__all__ = ["SOURCE_COVERAGE_VERSION", "SourceCoverageError", "NoSkillSourceCoverageReceipt",
           "derive_no_skill_source_coverage", "verify_no_skill_source_coverage",
           "bind_no_skill_source_coverage_case"]
