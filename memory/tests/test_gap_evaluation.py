"""Evaluation seam units; injected source checks/path level are not empirical evidence."""
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tehm import db
from tehm.causal import build_transition_causal_fragment, consolidate_causal_path
from tehm.causal.path_builder import causal_path_digest
from tehm.causal.mechanism import load_transition_facts
from tehm.artifact_store import ArtifactStore
from tehm.knowledge import build_knowledge_from_path, register_knowledge, get_knowledge_status
from tehm.knowledge.authority import verify_knowledge_authority
from tehm.assets.registry import set_asset_status, get_asset_status
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.capability.delta import evaluate_memory_delta
from tehm.evolution import gap_evaluation as seam
from test_shadow_update import _capture
from test_capability_gap_reason import _gap


@pytest.fixture(scope='module')
def unit_source_states(tmp_path_factory):
    root = tmp_path_factory.mktemp('unit_gap_evaluation_sources')
    conn = db.connect(root/'tehm.sqlite');db.ensure_schema(conn)
    tmp_tehm = (conn, ArtifactStore(root/'artifacts'), root)
    # Distinct fixture sources, not relabeled repetitions of one canonical
    # state (which correctly preserves the first source lineage).
    ids = [_capture(tmp_tehm, name) for name in ('req_ack_bug', 'req_ack_bug2')]
    path = consolidate_causal_path(conn, [build_transition_causal_fragment(conn, i, campaign_id='live')
                                       for i in ids], campaign_id='live')
    row = conn.execute('SELECT * FROM tehm_causal_paths WHERE path_id=?', (path.path_id,)).fetchone()
    # Projection fixture only: no claim of a real controlled intervention.
    h = causal_path_digest(mechanism_family=row['mechanism_family'], compatibility_profile=row['compatibility_profile'],
        evidence_level='L3_REPLICATED_EFFECT', source_transition_ids=json.loads(row['source_transitions_json']),
        node_ids=json.loads(row['ordered_nodes_json']), edge_ids=json.loads(row['ordered_edges_json']), support=json.loads(row['support_json']))
    conn.execute('UPDATE tehm_causal_paths SET evidence_level=?,path_digest=? WHERE path_id=?',
                 ('L3_REPLICATED_EFFECT', h, path.path_id));conn.commit()
    source = seam._staging_copy(conn)
    added = seam._staging_copy(source)
    knowledge = build_knowledge_from_path(added, path.path_id, status='candidate')
    register_knowledge(added, knowledge, target_scope='rtl_target', evidence_refs=[{
        'evidence_type': 'causal_path', 'evidence_id': path.path_id, 'lineage_id': None,
        'split': 'training', 'evidence_level': knowledge.evidence_level}])
    fact = load_transition_facts(source, ids[0])
    proposal = build_rtl_asset_proposal(_gap(), name='unit-evaluation-asset', transformation_family=fact.action['transformation_family'],
        action_payload_template={**fact.action['payload'], 'domain': fact.action['domain']},
        compatibility_profile=knowledge.compatibility_profile,
        verifier_obligations=('RTL_COMPILE_PASS',), mechanism_knowledge_ids=(knowledge.object_id,))
    asset = register_asset_proposal(added, proposal, target_scope='rtl_target')
    set_asset_status(added, asset_id=asset.asset_id, target_scope='rtl_target', status='shadow')
    delta = evaluate_memory_delta(seam._logical(source), seam._logical(added), {'version': 'memory-delta-v1',
        'added_knowledge_ids': ['knowledge:'+knowledge.object_id], 'added_asset_ids': ['asset:'+asset.asset_id]})
    p13 = SimpleNamespace(receipt_digest='sha256:'+'a'*64)
    witness = SimpleNamespace(receipt_digest='sha256:'+'b'*64)
    identity = (knowledge.object_id, asset.asset_id, 'rtl_target', witness, delta)
    try:
        yield seam._staging_snapshot_bytes(source), seam._staging_snapshot_bytes(added), p13, identity
    finally:
        source.close();added.close();conn.close()


@pytest.fixture
def prepared(unit_source_states, monkeypatch):
    source_bytes, added_bytes, p13, identity = unit_source_states
    source = sqlite3.connect(':memory:');source.row_factory = sqlite3.Row;source.deserialize(source_bytes)
    added = sqlite3.connect(':memory:');added.row_factory = sqlite3.Row;added.deserialize(added_bytes)
    monkeypatch.setattr(seam, '_check_added_source', lambda *_: identity)
    emitted = []
    receipt = seam.prepare_gap_evaluation_shadow(source, added, p13, staging_artifact_sink=emitted.append)
    evaluated = sqlite3.connect(':memory:');evaluated.row_factory = sqlite3.Row;evaluated.deserialize(emitted[0])
    yield source, added, evaluated, p13, receipt, identity
    source.close();added.close();evaluated.close()


def test_separate_evaluation_preparation_is_source_preserving(prepared):
    source, added, evaluated, p13, receipt, identity = prepared
    kid, version = identity[0].rsplit('@', 1)
    assert receipt.payload['added_shadow_memory_digest'] != receipt.payload['evaluation_memory_digest']
    assert receipt.payload['memory_delta']['baseline_memory_digest'] == seam._logical(source)
    assert receipt.payload['memory_delta']['candidate_memory_digest'] == seam._logical(evaluated)
    assert get_knowledge_status(added, knowledge_id=kid, version=int(version), target_scope=identity[2])['status'] == 'candidate'
    assert get_asset_status(added, asset_id=identity[1], target_scope=identity[2])['status'] == 'shadow'
    assert get_knowledge_status(evaluated, knowledge_id=kid, version=int(version), target_scope=identity[2])['status'] == 'validated'
    assert get_asset_status(evaluated, asset_id=identity[1], target_scope=identity[2])['status'] == 'candidate'
    check = seam.verify_gap_evaluation_admission(source, added, evaluated, p13, receipt)
    assert check['verified'] and check['eligible_for_disposable_evaluation'] and not check['production_admission']
    assert not verify_knowledge_authority(evaluated, receipt.payload['consumed_status_v1_authority'])['eligible']
    assert verify_knowledge_authority(evaluated, receipt.payload['current_status_v2_authority'])['eligible']


@pytest.mark.parametrize('field', ['baseline_memory_digest', 'added_shadow_memory_digest', 'evaluation_memory_digest',
    'p13_receipt_digest', 'source_witness_digest', 'asset_id', 'knowledge_object_id', 'target_scope', 'canonical_raw_digest'])
def test_rehashed_receipt_labels_do_not_replace_database_evidence(prepared, field):
    source, added, evaluated, p13, receipt, _ = prepared
    value = copy.deepcopy(receipt.payload);value[field] = 'unverified-label'
    forged = seam.GapEvaluationAdmissionReceipt(value)
    assert not seam.verify_gap_evaluation_admission(source, added, evaluated, p13, forged)['verified']


@pytest.mark.parametrize('kind', ['asset_promoted', 'knowledge_unvalidated', 'authority_missing', 'content_changed',
                                 'unrelated_row_changed', 'new_table', 'canonical_changed'])
def test_actual_evaluated_state_tamper_rejected(prepared, kind):
    source, added, evaluated, p13, receipt, identity = prepared
    if kind == 'asset_promoted': evaluated.execute("UPDATE tehm_asset_status SET status='promoted' WHERE asset_id=?", (identity[1],))
    elif kind == 'knowledge_unvalidated': evaluated.execute("UPDATE tehm_mechanism_knowledge_status SET status='candidate'")
    elif kind == 'authority_missing': evaluated.execute('DELETE FROM tehm_knowledge_authority_receipts')
    elif kind == 'content_changed': evaluated.execute("UPDATE tehm_assets SET definition_json='{}'")
    elif kind == 'new_table': evaluated.execute('CREATE TABLE forbidden_extra(x TEXT)')
    elif kind == 'canonical_changed': evaluated.execute("UPDATE tehm_transitions SET outcome='FAIL'")
    else: evaluated.execute("UPDATE tehm_meta SET value='tampered' WHERE key='schema_version'")
    assert not seam.verify_gap_evaluation_admission(source, added, evaluated, p13, receipt)['verified']


@pytest.mark.parametrize('flag,value', [('evaluation_only', False), ('production_admission', True),
    ('promotion_attempted', True), ('canonical_memory_mutation', 'write'), ('staging_discarded', False)])
def test_evaluation_receipt_never_grants_production(prepared, flag, value):
    payload = copy.deepcopy(prepared[4].payload);payload[flag] = value
    with pytest.raises(ValueError, match='authority boundary'): seam.GapEvaluationAdmissionReceipt(payload)


def test_receipt_digest_and_mutable_alias_protection(prepared):
    receipt = prepared[4];value = receipt.to_dict()
    assert seam.GapEvaluationAdmissionReceipt.from_dict(value).to_dict() == value
    value['current_status_v2_authority']['eligible'] = False
    assert receipt.to_dict()['current_status_v2_authority']['eligible'] is True
    with pytest.raises(ValueError, match='digest mismatch'): seam.GapEvaluationAdmissionReceipt.from_dict(value)


def test_sink_failure_preserves_added_source_and_closes_staging(prepared, monkeypatch):
    source, added, _, p13, _, _ = prepared
    before = seam._logical(added);created = [];real = seam._staging_copy
    def tracked(conn):
        copied = real(conn);created.append(copied);return copied
    def fail(_): raise RuntimeError('unit sink failure')
    monkeypatch.setattr(seam, '_staging_copy', tracked)
    with pytest.raises(RuntimeError, match='sink failure'):
        seam.prepare_gap_evaluation_shadow(source, added, p13, staging_artifact_sink=fail)
    assert seam._logical(added) == before
    with pytest.raises(sqlite3.ProgrammingError): created[0].execute('SELECT 1')


def test_untyped_p13_receipt_rejected_before_admission(tmp_tehm):
    conn, _, _ = tmp_tehm
    with pytest.raises(TypeError, match='typed P13'): seam._check_added_source(conn, conn, {'eligible': True})


def test_full_path_registered_once_with_aggregate_lineage(prepared):
    _, added, _, _, receipt, _ = prepared
    kid, version = receipt.payload['knowledge_object_id'].rsplit('@', 1)
    rows = added.execute('SELECT * FROM tehm_mechanism_knowledge_evidence WHERE knowledge_id=? AND version=?',
                         (kid, int(version))).fetchall()
    assert len(rows) == 1 and rows[0]['lineage_id'] is None
    claim = added.execute('SELECT support_lineages_json FROM tehm_mechanism_knowledge WHERE knowledge_id=?', (kid,)).fetchone()
    assert len(json.loads(claim[0])) == 2
