"""Admission and numeric/categorical binding share canonical positive evidence."""
import json

import pytest
import tehm_backend as B
from test_memory_harm_fixes import _store


@pytest.mark.parametrize('bad', ['rolled_back','unknown_rollback','unverified','not_repair'])
def test_only_confirmed_positive_own_witnesses_vote(bad):
    conn=_store({'good':'PASS','bad':'PASS'}, {'r':[{
        'good':{'$H0':'route','$H1':-8},'bad':{'$H0':'route','$H1':-40}}]})
    delta={'experiment_kind':'REPAIR','rolled_back':'false'};verifier={'oracle_complete':True}
    if bad=='rolled_back':delta['rolled_back']='true'
    if bad=='unknown_rollback':delta.pop('rolled_back')
    if bad=='unverified':verifier['oracle_complete']=False
    if bad=='not_repair':delta['experiment_kind']='OBSERVE'
    conn.execute('UPDATE tehm_transitions SET observation_delta_json=?,verifier_json=? WHERE transition_id=?',
                 (json.dumps(delta),json.dumps(verifier),'bad'))
    assert B._hole_witnesses(conn,'r',{'$H0':'route'})['$H1']==[-8]


def test_copies_of_the_same_physical_attempt_vote_once():
    conn=_store({'a':'PASS','copy':'PASS','b':'PASS'}, {'r':[{
        'a':{'$H1':-8},'copy':{'$H1':-8},'b':{'$H1':-16}}]})
    conn.execute('UPDATE tehm_transitions SET provenance_json=? WHERE transition_id IN (?,?)',
                 (json.dumps({'record_id':'same_physical_attempt'}),'a','copy'))
    assert sorted(B._hole_witnesses(conn,'r')['$H1'])==[-16,-8]


def test_conflicting_copy_and_unknown_category_never_select_a_parameter():
    conn=_store({'a':'PASS','copy':'UNKNOWN','missing':'PASS'}, {'r':[{
        'a':{'$H0':'route','$H1':-8},'copy':{'$H0':'route','$H1':-8},'missing':{'$H1':-40}}]})
    conn.execute('UPDATE tehm_transitions SET provenance_json=? WHERE transition_id IN (?,?)',
                 (json.dumps({'record_id':'same_physical_attempt'}),'a','copy'))
    assert B._hole_witnesses(conn,'r',{'$H0':'route'})=={}
