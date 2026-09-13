"""Explicit-source gap selection units; mocked facts are not empirical gains."""
import sqlite3
from types import SimpleNamespace
import pytest
from tehm.assets import gap_detector
from tehm.evolution.reason_derivation import derive_capability_gap_reason
from contracts import MemoryRoutingDecision


@pytest.fixture
def source(monkeypatch):
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE tehm_transitions (transition_id TEXT PRIMARY KEY)')
    conn.execute('CREATE TABLE tehm_dataset_membership (transition_id TEXT,campaign_id TEXT,split TEXT,learner_eligible INTEGER)')
    facts={}
    for identity,lineage,outcome in [('t1','l1','IMPROVED'),('t2','l2','IMPROVED'),('c1','l1','FAIL'),('c2','l2','FAIL')]:
        conn.execute('INSERT INTO tehm_transitions VALUES (?)',(identity,))
        conn.execute('INSERT INTO tehm_dataset_membership VALUES (?,?,?,?)',(identity,'training-campaign','training',1))
        facts[identity]=SimpleNamespace(transition_id=identity,lineage_id=lineage,mechanism_family='HANDSHAKE_COMPLETION',
            compatibility_profile='rtl.fsm.guard_conjunction.v1',outcome=outcome,
            delta={'original_failure':'REMOVED' if outcome=='IMPROVED' else 'PRESENT'},action={'domain':'rtl.FSM_GUARD_CONJOIN'})
    calls=[]
    monkeypatch.setattr(gap_detector,'load_transition_facts',lambda _,identity:facts[identity])
    monkeypatch.setattr(gap_detector,'_promoted_asset_profiles',lambda _:set())
    monkeypatch.setattr(gap_detector,'_promoted_rule_families',lambda _:set())
    from tehm import verified_execution
    monkeypatch.setattr(verified_execution,'require_verified_transition',lambda _,identity:calls.append(identity))
    yield conn,facts,calls
    conn.close()


def test_explicit_subset_counts_two_source_failures_not_four_representations(source):
    conn,_,calls=source;before='\n'.join(conn.iterdump())
    all_rows=gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign')
    assert all_rows[0].current_action_coverage['failure_evidence']==4
    assert calls==[]  # Historical diagnostic semantics unchanged.
    selected=gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign',transition_ids=('t2','t1'))
    assert selected[0].current_action_coverage['failure_evidence']==2
    assert selected[0].current_action_coverage['observed']==2
    assert selected[0].evidence_transitions==('t1','t2')
    assert selected[0].evidence_lineages==('l1','l2')
    assert calls==['t1','t2']
    assert '\n'.join(conn.iterdump())==before


@pytest.mark.parametrize('ids',[[],(),'',{'t1','t2'},('t1','t1'),('t1',True),('t1',''),('t1','  ')])
def test_malformed_or_duplicate_subset_rejected(source,ids):
    conn,_,calls=source
    with pytest.raises(ValueError,match='nonempty unique'):
        gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign',transition_ids=ids)
    assert calls==[]


@pytest.mark.parametrize('kind',['unknown','foreign_campaign','calibration','heldout','nonlearner'])
def test_subset_must_not_silently_drop_ineligible_evidence(source,kind):
    conn,_,calls=source
    if kind=='unknown':ids=('t1','not-real')
    else:
        ids=('t1','t2')
        if kind=='foreign_campaign':conn.execute('UPDATE tehm_dataset_membership SET campaign_id=? WHERE transition_id=?',('foreign','t2'))
        elif kind=='nonlearner':conn.execute('UPDATE tehm_dataset_membership SET learner_eligible=0 WHERE transition_id=?',('t2',))
        else:conn.execute('UPDATE tehm_dataset_membership SET split=? WHERE transition_id=?',(kind,'t2'))
    before='\n'.join(conn.iterdump())
    with pytest.raises(ValueError,match='exactly match'):
        gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign',transition_ids=ids)
    assert calls==[] and '\n'.join(conn.iterdump())==before


def test_explicit_subset_requires_verified_source_not_only_valid_fact_shape(source,monkeypatch):
    conn,_,_=source
    from tehm import verified_execution
    def unavailable(*_):raise ValueError('no independently verified execution')
    monkeypatch.setattr(verified_execution,'require_verified_transition',unavailable)
    with pytest.raises(ValueError,match='independently verified'):
        gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign',transition_ids=('t1','t2'))


def test_two_representations_of_one_lineage_do_not_establish_gap_reason(source):
    conn,_,_=source
    gaps=gap_detector.detect_capability_gaps(conn,campaign_id='training-campaign',transition_ids=('t1','c1'),min_failures=1)
    route=MemoryRoutingDecision(decision='NO_SKILL',resolved_state_id='unit-source-state',selected_rule_ids=(),selected_path_ids=(),
        selected_asset_ids=(),applicability={},causal_support={},risk={},abstain_reasons=(),no_memory_budget=1,memory_budget=0,no_skill_reason='NO_MATCH')
    assert all(derive_capability_gap_reason(gap,campaign_id='training-campaign',case_id='unit',routing=route) is None for gap in gaps)
