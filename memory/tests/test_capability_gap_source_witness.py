"""DB-binding and negative units; mocked gap/facts are not empirical evidence."""
import copy
import dataclasses
import sqlite3
import pytest
from contracts import MemoryQuery
from tehm.evolution import gap_source as source
from test_capability_gap_reason import _gap,_route


@dataclasses.dataclass
class ToyFact:
    transition_id: str
    lineage_id: str


@pytest.fixture
def bound(monkeypatch):
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE tehm_dataset_membership (transition_id TEXT,campaign_id TEXT,split TEXT,learner_eligible INTEGER)')
    for identity in ('transition-a','transition-b'):
        conn.execute('INSERT INTO tehm_dataset_membership VALUES (?,?,?,?)',(identity,'unit-campaign','training',1))
    conn.commit()
    gap=_gap();route=_route(gap)
    monkeypatch.setattr(source,'detect_capability_gaps',lambda *_,**kw:[gap])
    def routing(actual,_,**kwargs):
        assert actual.execute('PRAGMA query_only').fetchone()[0]==1
        assert kwargs['mode']=='shadow' and kwargs['persist_state'] is False
        with pytest.raises(sqlite3.OperationalError):actual.execute('DELETE FROM tehm_dataset_membership')
        return route
    monkeypatch.setattr(source,'route_memory',routing)
    monkeypatch.setattr(source,'load_transition_facts',lambda _,identity:ToyFact(identity,'lineage-'+identity))
    query=MemoryQuery(context_ref=None,dominant_dimensions={},query_plan={'mechanism_family':gap.mechanism_family,'compatibility_profile':gap.compatibility_profile})
    receipt=source.derive_capability_gap_source(conn,campaign_id='unit-campaign',case_id='unit-case',query=query,
        transition_ids=gap.evidence_transitions)
    yield conn,query,receipt
    conn.close()


def test_receipt_roundtrip_and_db_rederivation(bound):
    conn,_,receipt=bound;before='\n'.join(conn.iterdump())
    payload={**receipt.to_dict(),'receipt_id':receipt.receipt_id,'receipt_digest':receipt.receipt_digest}
    assert source.CapabilityGapSourceReceipt.from_dict(payload)==receipt
    assert source.verify_capability_gap_source(conn,payload)['verified'] is True
    assert source.verify_capability_gap_source(conn,receipt)['source_memory_digest']==receipt.source_memory_digest
    assert '\n'.join(conn.iterdump())==before and conn.execute('PRAGMA query_only').fetchone()[0]==0


@pytest.mark.parametrize('field',['source_memory_digest','source_facts_digest','source_membership_digest'])
def test_rehashed_digest_label_is_not_source_evidence(bound,field):
    conn,_,receipt=bound;payload=receipt.to_dict();payload[field]='sha256:'+'0'*64
    assert source.verify_capability_gap_source(conn,payload)['verified'] is False


@pytest.mark.parametrize('kind',['foreign_campaign','heldout','calibration','nonlearner','unrelated_after_state'])
def test_receipt_cannot_replay_against_changed_source_or_partition(bound,kind):
    conn,_,receipt=bound
    conn.execute('SAVEPOINT changed_source')
    if kind=='foreign_campaign':conn.execute('UPDATE tehm_dataset_membership SET campaign_id=?',('foreign',))
    elif kind=='nonlearner':conn.execute('UPDATE tehm_dataset_membership SET learner_eligible=0')
    elif kind=='unrelated_after_state':conn.execute('CREATE TABLE candidate_after_state (payload TEXT)')
    else:conn.execute('UPDATE tehm_dataset_membership SET split=?',(kind,))
    assert source.verify_capability_gap_source(conn,receipt)['verified'] is False
    conn.execute('ROLLBACK TO changed_source');conn.execute('RELEASE changed_source')
    assert source.verify_capability_gap_source(conn,receipt)['verified'] is True


@pytest.mark.parametrize('field',['localized_update_plan','replacement_knowledge','shadow_after_state','production_authority','fix','heldout_answer'])
def test_mutation_or_gold_fields_rejected(bound,field):
    conn,query,receipt=bound
    payload=receipt.to_dict();payload['source_query']['query_plan'][field]={'payload':'not-source-evidence'}
    assert source.verify_capability_gap_source(conn,payload)['verified'] is False
    invalid=copy.deepcopy(query);invalid.query_plan[field]='not-source-evidence'
    with pytest.raises(ValueError,match='source-only'):
        source.derive_capability_gap_source(conn,campaign_id='unit-campaign',case_id='unit',query=invalid,
            transition_ids=receipt.transition_ids)


def test_admitted_flag_and_reason_type_not_authority(bound):
    conn,_,receipt=bound
    for mutate in (lambda p:p['admission'].update(admitted=False),lambda p:p['reason'].update(reason='MEMORY_INTERFERENCE'),
        lambda p:p.update(evaluation_only=1),lambda p:p.update(transition_ids=['transition-a','transition-a'])):
        payload=receipt.to_dict();mutate(payload)
        assert source.verify_capability_gap_source(conn,payload)['verified'] is False


def test_to_dict_is_not_a_mutable_alias(bound):
    _,_,receipt=bound;payload=receipt.to_dict();payload['reason']['input_receipt_ids'].clear()
    assert receipt.to_dict()['reason']['input_receipt_ids']


def test_missing_or_extra_fields_and_bad_digest_fail_closed(bound):
    conn,_,receipt=bound
    for payload in ({},{**receipt.to_dict(),'admitted':True},{**receipt.to_dict(),'receipt_digest':'sha256:'+'0'*64}):
        assert source.verify_capability_gap_source(conn,payload)['verified'] is False
    assert source.verify_capability_gap_source(None,receipt)['verified'] is False
