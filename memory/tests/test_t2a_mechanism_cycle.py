"""The DEV operator never resumes formal dispatch or silently retries."""
import importlib.util
from pathlib import Path
import pytest

FILE=Path(__file__).resolve().parents[1]/'evaluation/tools/t2a_mechanism_cycle.py'
spec=importlib.util.spec_from_file_location('mechanism_cycle_test',FILE)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)


def test_full_junit_gate_rejects_failures_or_partial_case_counts(tmp_path):
    xml=tmp_path/'tests.xml'
    cases='<testcase/>'*1800
    xml.write_text('<testsuites><testsuite tests="1800" failures="0" errors="0" skipped="4">'+cases+'</testsuite></testsuites>')
    assert C.junit(xml)['tests']==1800
    for payload in [
        '<testsuites><testsuite tests="1800" failures="1">'+cases+'</testsuite></testsuites>',
        '<testsuites><testsuite tests="1800" failures="0"><testcase/></testsuite></testsuites>']:
        xml.write_text(payload)
        with pytest.raises(RuntimeError):C.junit(xml)


def test_old_existing_cycle_cannot_be_restarted_or_overwritten(tmp_path):
    (tmp_path/'cycle-start.json').write_text('{}')
    with pytest.raises(RuntimeError,match='already started'):C.run(tmp_path)


def test_mechanism_dev_inventory_is_exactly_the_old_calibration_cohort():
    import t2a_batch as B
    inv={'schema':'t2a-mechanism-dev-inventory-v1','status':'FROZEN_DEV','split':'DEV',
         'source_sha256':B.A.SOURCE_SHA,'tasks':[{'task':t,'family':values[0],'status':'ADMITTED_DEV'}
            for t,values in B.MECHANISM_DEV_TASKS.items()]}
    assert B.validate_dev_inventory(inv)==[]
    inv['tasks'][0]={'task':'ibex/u80','family':'lowRISC/ibex','status':'ADMITTED_DEV'}
    assert B.validate_dev_inventory(inv)


def test_formal_cannot_enable_a_paired_dev_subset(tmp_path):
    import t2a_batch as B
    with pytest.raises(ValueError,match='DEV subset'):
        B.prepare(tmp_path/'never',None,None,None,None,split='TEST',dev_arms=('NB','TM0'))
    assert not (tmp_path/'never').exists()
