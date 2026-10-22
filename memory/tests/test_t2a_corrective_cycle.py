"""The corrected batch is gated, not a free retry/payment reset."""
import importlib.util
import json
from pathlib import Path

import pytest

PATH=Path(__file__).resolve().parents[1]/'evaluation/tools/t2a_corrective_cycle.py'
spec=importlib.util.spec_from_file_location('corrective_cycle',PATH)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)


def bank(requests=124,tokens=373391):
    return {'caps':{'llm_requests':336},'combined_tokens_cap':4704000,
            'budget_spent':{'llm_requests':requests,'tokens_in':tokens,'tokens_out':0}}


def test_corrected_cycle_reuses_remaining_quota_instead_of_resetting_it():
    C.require_payment(bank(),192)
    with pytest.raises(RuntimeError,match='Insufficient'):
        C.require_payment(bank(requests=145),192)
    with pytest.raises(RuntimeError,match='unknown or exhausted'):
        C.require_payment(bank(tokens=4704000),192)
    with pytest.raises(RuntimeError,match='unknown or exhausted'):
        C.require_payment(bank(tokens='UNKNOWN'),192)


def test_complete_test_receipt_is_required_for_the_identical_runtime(tmp_path):
    p=tmp_path/'tests.json';hashes={'core.py':'same-code'}
    p.write_text(json.dumps({'passed':True,'runtime_hashes':hashes,'checks':{'suite':True}}))
    assert C.check_tests(p,hashes)
    with pytest.raises(RuntimeError,match='not bound'):
        C.check_tests(p,{'core.py':'changed'})
    p.write_text(json.dumps({'passed':False,'runtime_hashes':hashes,'checks':{'suite':True}}))
    with pytest.raises(RuntimeError,match='not bound'):C.check_tests(p,hashes)


def test_existing_cycle_cannot_be_resumed_or_retried_implicitly(tmp_path):
    (tmp_path/'registration.json').write_text('{}')
    (tmp_path/'cycle-start.json').write_text('{}')
    with pytest.raises(RuntimeError,match='already started'):
        C.run(tmp_path)


def test_junit_gate_requires_complete_cases_and_zero_failures(tmp_path):
    p=tmp_path/'results.xml'
    p.write_text('<testsuites><testsuite tests="2" failures="0" errors="0" skipped="1">'
                 '<testcase/><testcase><skipped/></testcase></testsuite></testsuites>')
    assert C.review_junit(p,minimum=2)['tests']==2
    with pytest.raises(RuntimeError,match='incomplete or failed'):C.review_junit(p,minimum=3)
    p.write_text('<testsuites><testsuite tests="2" failures="1"><testcase/><testcase/></testsuite></testsuites>')
    with pytest.raises(RuntimeError,match='incomplete or failed'):C.review_junit(p,minimum=2)
    p.write_text('<testsuites><testsuite tests="2" failures="0"><testcase/></testsuite></testsuites>')
    with pytest.raises(RuntimeError,match='incomplete or failed'):C.review_junit(p,minimum=2)
