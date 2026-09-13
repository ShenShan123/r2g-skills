"""Actual P13 ADD -> separately verified disposable evaluation preparation.

S0 is immutable source memory; S1 is the actual P13 candidate/shadow artifact;
S2 admits only those new objects to isolated evaluation. S1 and S2 are distinct
full SQL states. This seam grants neither production admission nor promotion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import sqlite3

from tehm.ids import stable_dumps
from tehm.assets.registry import get_asset, get_asset_status, asset_content_digest, set_asset_status
from tehm.assets.synthesis import build_rtl_asset_proposal
from tehm.assets.receipts import CapabilityGapReceipt
from tehm.assets.guard_binding import CONTRACT, SPEC
from tehm.causal.mechanism import load_transition_facts
from tehm.knowledge.builder import build_knowledge_from_path
from tehm.knowledge.registry import get_knowledge_by_object_id
from tehm.knowledge.authority import record_knowledge_authority, verify_knowledge_authority
from tehm.knowledge.lifecycle import get_knowledge_status, set_knowledge_status
from tehm.capability.delta import memory_delta_from_shadow_update, evaluate_memory_delta
from tehm.state.resolver import resolve_current_state

from .apply_update import AppliedShadowUpdateReceipt, _staging_copy, _staging_snapshot_bytes, _verify_training_transitions
from .anti_forgetting import raw_evidence_digest
from .gap_source import CapabilityGapSourceReceipt, verify_capability_gap_source

VERSION = "capability-gap-disposable-evaluation-admission-v1"
_ADD_TABLES = frozenset({"tehm_mechanism_knowledge", "tehm_mechanism_knowledge_status",
    "tehm_mechanism_knowledge_evidence", "tehm_assets", "tehm_asset_status"})
_EVAL_TABLES = frozenset({"tehm_mechanism_knowledge_status", "tehm_asset_status",
    "tehm_knowledge_authority_receipts", "tehm_knowledge_authority_evidence"})


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _logical(conn):
    return _digest("\n".join(conn.iterdump()))


def _tables(conn):
    names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: [dict(row) for row in conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
            for name in names}


def _schema(conn):
    return sorted(tuple(row) for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))


def _encoded(rows):
    return sorted(stable_dumps(row) for row in rows)


def _owner(table, row, object_id, asset_id, scope):
    kid, version = object_id.rsplit("@", 1)
    if table in {"tehm_assets", "tehm_asset_status"}:
        owned = row.get("asset_id") == asset_id
    else:
        owned = row.get("knowledge_id") == kid and row.get("version") == int(version)
    if "target_scope" in row:
        owned = owned and row["target_scope"] == scope
    return owned


def _isolated_difference(before, after, allowed, object_id, asset_id, scope, *, status_updates=False):
    if before.keys() != after.keys():
        raise ValueError("projection changed table inventory")
    for table in before:
        left, right = _encoded(before[table]), _encoded(after[table])
        if table not in allowed:
            if left != right: raise ValueError("unrelated table changed: " + table)
            continue
        old = [r for r in before[table] if not (status_updates and table.endswith('_status') and
               _owner(table, r, object_id, asset_id, scope))]
        if not set(_encoded(old)) <= set(right):
            raise ValueError("existing immutable row changed: " + table)
        old_rows = set(left)
        if any(stable_dumps(r) not in old_rows and not _owner(table, r, object_id, asset_id, scope)
               for r in after[table]):
            raise ValueError("projection changed another object: " + table)


def _check_added_source(source, added, p13):
    if not isinstance(p13, AppliedShadowUpdateReceipt):
        raise TypeError("evaluation preparation requires an actual typed P13 receipt")
    p13 = AppliedShadowUpdateReceipt.from_dict(p13.to_dict())
    delta = memory_delta_from_shadow_update(p13)
    if (p13.update_target != "UPDATE_CAUSAL_KNOWLEDGE" or p13.operation != "ADD" or
            len(p13.created_object_ids) != 2 or p13.created_relation_ids or not delta.eligible):
        raise ValueError("evaluation preparation requires exactly new Knowledge/Asset ADD")
    know = delta.delta["added_knowledge_ids"]
    assets = delta.delta["added_asset_ids"]
    if len(know) != 1 or len(assets) != 1:
        raise ValueError("P13 ADD did not create one Knowledge and one Asset")
    object_id, asset_id = know[0].split(":", 1)[1], assets[0].split(":", 1)[1]
    witness = CapabilityGapSourceReceipt.from_dict(p13.metadata.get("capability_gap_source_receipt"))
    replay = verify_capability_gap_source(source, witness)
    if not replay.get("verified") or not replay.get("eligible"):
        raise ValueError("pre-ADD capability-gap source does not independently replay")
    scope = p13.metadata["scope"].get("target_scope") or "global"
    if (_logical(source) != p13.source_digest_before or _logical(added) != p13.staging_digest_after or
            witness.source_memory_digest != p13.source_digest_before or
            raw_evidence_digest(source) != raw_evidence_digest(added) or
            raw_evidence_digest(source) != p13.raw_evidence_before_digest or _schema(source) != _schema(added)):
        raise ValueError("actual S0/S1 do not bind the P13 full-state receipt")
    _isolated_difference(_tables(source), _tables(added), _ADD_TABLES, object_id, asset_id, scope)
    knowledge = get_knowledge_by_object_id(added, object_id, target_scope=scope)
    if len(knowledge.causal_path_ids) != 1:
        raise ValueError("gap ADD must refer to its one verified replicated path")
    expected = build_knowledge_from_path(source, knowledge.causal_path_ids[0], status="candidate")
    if knowledge.to_dict() != expected.to_dict() or knowledge.support_lineages != tuple(witness.gap["evidence_lineages"]):
        raise ValueError("new Knowledge does not replay source-derived replicated content")
    path = source.execute("SELECT source_transitions_json FROM tehm_causal_paths WHERE path_id=?",
                          (knowledge.causal_path_ids[0],)).fetchone()
    ids = tuple(json.loads(path[0]))
    if not set(witness.transition_ids) <= set(ids):
        raise ValueError("replicated path excludes witnessed independent failures")
    _verify_training_transitions(source, ids, witness.campaign_id)
    asset = get_asset(added, asset_id)
    if asset is None: raise ValueError("new Asset registry content failed integrity")
    fact = load_transition_facts(source, witness.transition_ids[0])
    proposal = build_rtl_asset_proposal(CapabilityGapReceipt.from_dict(witness.gap), name=asset['name'],
        transformation_family=fact.action['transformation_family'], action_payload_template=fact.action['payload'],
        compatibility_profile=knowledge.compatibility_profile,
        verifier_obligations=("RTL_COMPILE_PASS", "RTL_TARGET_TEST_PASS", "RTL_FROZEN_REGRESSION_PASS"),
        mechanism_knowledge_ids=(object_id,))
    if asset['definition'].get('binding_template') is not None:
        proposal = replace(proposal, definition={**proposal.definition, 'binding_template': {
            'contract': CONTRACT, 'spec': dict(SPEC), 'spec_digest': _digest(SPEC)}})
    if asset_content_digest(proposal.to_dict()) != asset['content_digest']:
        raise ValueError("new Asset does not replay the core training-action template")
    status = get_knowledge_status(added, knowledge_id=knowledge.knowledge_id, version=knowledge.version, target_scope=scope)
    astat = get_asset_status(added, asset_id=asset_id, target_scope=scope)
    if status['status'] != 'candidate' or status['status_version'] != 1 or astat is None or astat['status'] != 'shadow' or astat['status_version'] != 2:
        raise ValueError("S1 already crossed the P13 candidate/shadow boundary")
    if (status['provenance'].get('source_witness_digest') != witness.receipt_digest or
            status['provenance'].get('plan_digest') != p13.plan_digest or
            asset['provenance'].get('source_witness_digest') != witness.receipt_digest or
            asset['provenance'].get('plan_digest') != p13.plan_digest):
        raise ValueError("new objects lost actual P13/source provenance")
    if (resolve_current_state(source, p13.metadata['scope'], mode='shadow', persist=False).resolution_id != p13.before_resolution_id or
            resolve_current_state(added, p13.metadata['scope'], mode='shadow', persist=False).resolution_id != p13.after_resolution_id):
        raise ValueError("P13 source/added state resolutions do not replay")
    return object_id, asset_id, scope, witness, delta


def _admit(staging, object_id, asset_id, scope):
    knowledge = get_knowledge_by_object_id(staging, object_id, target_scope=scope)
    first = record_knowledge_authority(staging, knowledge, target_scope=scope)
    if not verify_knowledge_authority(staging, first).get('eligible'):
        raise ValueError("actual pre-validation Knowledge authority rejected")
    set_knowledge_status(staging, knowledge_id=knowledge.knowledge_id, version=knowledge.version,
        target_scope=scope, status='validated', authority_receipt=first, commit=False)
    set_asset_status(staging, asset_id=asset_id, target_scope=scope, status='candidate',
        provenance={'authority': 'disposable-gap-evaluation', 'production_admission': False}, commit=False)
    # The consumed status-v1 receipt is historical after validation. Record
    # and replay a separate current status-v2 authority; never reuse stale v1.
    current = record_knowledge_authority(staging, get_knowledge_by_object_id(staging, object_id, target_scope=scope), target_scope=scope)
    if not verify_knowledge_authority(staging, current).get('eligible'):
        raise ValueError("current evaluated Knowledge authority did not replay")
    return first.to_dict(), current.to_dict()


@dataclass(frozen=True)
class GapEvaluationAdmissionReceipt:
    payload: dict

    def __post_init__(self):
        object.__setattr__(self, 'payload', json.loads(stable_dumps(self.payload)))
        if (self.payload.get('version') != VERSION or self.payload.get('evaluation_only') is not True or
                self.payload.get('production_admission') is not False or self.payload.get('promotion_attempted') is not False or
                self.payload.get('canonical_memory_mutation') != 'none' or self.payload.get('staging_discarded') is not True):
            raise ValueError("evaluation preparation receipt crosses an authority boundary")

    def to_dict(self):
        return {**json.loads(stable_dumps(self.payload)), 'receipt_digest': self.receipt_digest}

    @property
    def receipt_digest(self):
        return _digest(self.payload)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict): raise TypeError("evaluation preparation receipt must be an object")
        result = cls({k: v for k, v in value.items() if k != 'receipt_digest'})
        if value.get('receipt_digest') != result.receipt_digest:
            raise ValueError("evaluation preparation receipt digest mismatch")
        return result


def _receipt(source, added, evaluated, p13, identity, first, current):
    object_id, asset_id, scope, witness, delta = identity
    composed = evaluate_memory_delta(_logical(source), _logical(evaluated), {
        'version': 'memory-delta-v1', 'baseline_memory_digest': _logical(source),
        'candidate_memory_digest': _logical(evaluated), **delta.delta})
    if not composed.eligible: raise ValueError("actual S0->S2 composed memory delta rejected")
    return GapEvaluationAdmissionReceipt({'version': VERSION, 'p13_receipt_digest': p13.receipt_digest,
        'source_witness_digest': witness.receipt_digest, 'baseline_memory_digest': _logical(source),
        'added_shadow_memory_digest': _logical(added), 'evaluation_memory_digest': _logical(evaluated),
        'knowledge_object_id': object_id, 'asset_id': asset_id, 'target_scope': scope,
        'consumed_status_v1_authority': first, 'current_status_v2_authority': current,
        'memory_delta': composed.to_dict(), 'canonical_raw_digest': raw_evidence_digest(source),
        'evaluation_only': True, 'production_admission': False, 'promotion_attempted': False,
        'canonical_memory_mutation': 'none', 'staging_discarded': True})


def prepare_gap_evaluation_shadow(source, added, p13, *, staging_artifact_sink):
    """Prepare and close an isolated S2; emit bytes, never a mutable connection."""
    if not callable(staging_artifact_sink): raise TypeError("evaluation artifact sink must be callable")
    before = (_logical(source), _logical(added))
    identity = _check_added_source(source, added, p13)
    staging = _staging_copy(added)
    try:
        first, current = _admit(staging, *identity[:3])
        if _schema(added) != _schema(staging): raise ValueError("evaluation preparation changed schema")
        _isolated_difference(_tables(added), _tables(staging), _EVAL_TABLES, *identity[:3], status_updates=True)
        if raw_evidence_digest(staging) != raw_evidence_digest(added): raise ValueError("evaluation changed canonical raw rows")
        receipt = _receipt(source, added, staging, p13, identity, first, current)
        staging_artifact_sink(_staging_snapshot_bytes(staging))
        if before != (_logical(source), _logical(added)):
            raise ValueError("evaluation artifact sink changed immutable source state")
        return receipt
    finally:
        staging.close()


def verify_gap_evaluation_admission(source, added, evaluated, p13, receipt):
    """Replay actual core admission and S0/S1/S2, not caller digest labels."""
    staging = None
    try:
        receipt = GapEvaluationAdmissionReceipt.from_dict(receipt.to_dict() if isinstance(receipt, GapEvaluationAdmissionReceipt) else receipt)
        identity = _check_added_source(source, added, p13)
        staging = _staging_copy(added)
        first, current = _admit(staging, *identity[:3])
        if _schema(added) != _schema(evaluated): raise ValueError("evaluated schema differs")
        _isolated_difference(_tables(added), _tables(evaluated), _EVAL_TABLES, *identity[:3], status_updates=True)
        # Full digests bind actual S2 bytes/SQL. Replay comparison ignores only
        # newly generated projection timestamps, never outcomes or old rows.
        def semantics(conn):
            tables = _tables(conn)
            return {name: _encoded([{k: v for k, v in row.items() if not (
                name in _EVAL_TABLES and _owner(name, row, *identity[:3]) and k in {'created_at', 'updated_at'})}
                for row in rows]) for name, rows in tables.items()}
        if semantics(staging) != semantics(evaluated): raise ValueError("actual evaluated state differs from core admission replay")
        if raw_evidence_digest(source) != raw_evidence_digest(evaluated): raise ValueError("canonical raw evidence differs")
        actual = _receipt(source, added, evaluated, p13, identity, first, current)
        if actual.to_dict() != receipt.to_dict(): raise ValueError("complete evaluation preparation receipt did not replay")
        if not verify_knowledge_authority(evaluated, current).get('eligible'):
            raise ValueError("actual current authority is invalid")
        return {'verified': True, 'eligible_for_disposable_evaluation': True,
                'production_admission': False, 'receipt_digest': actual.receipt_digest, 'reasons': []}
    except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {'verified': False, 'eligible_for_disposable_evaluation': False,
                'production_admission': False, 'reasons': [str(exc)]}
    finally:
        if staging is not None: staging.close()
