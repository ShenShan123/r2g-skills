"""Independent operator receipt cannot turn incomplete DEV into a pass."""
import importlib.util
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'evaluation/tools/t2a_dev_completion_audit.py'
spec=importlib.util.spec_from_file_location('dev_completion_audit',PATH)
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)


def complete(accepted=True):
    return {'split':'DEV','paper_data':False,'status':'ACCEPTED' if accepted else 'NOT_ACCEPTED',
        'accepted':accepted,'final':{'accepted':accepted,'envelope':{'status':'PASS'},
          'acceptance':{'accepted':accepted,'unknown':[],
             'checks':{k:'pass' if accepted or k!='A2_drc_full_deck' else 'fail' for k in A.CHECKS}}}}


def test_only_complete_measured_success_or_failure_is_a_valid_dev_terminal():
    assert A.terminal_complete(complete()) and A.terminal_complete(complete(False))
    item=complete(False);item['final']['execution_recovery']='PARTIAL_VERIFIED'
    assert not A.terminal_complete(item)
    item=complete(False);item['final']['acceptance']['checks'].pop('A11_provenance')
    assert not A.terminal_complete(item)
    item=complete(False);item['final']['acceptance']['unknown']=['A3_lvs']
    assert not A.terminal_complete(item)


def test_inconsistent_acceptance_or_test_label_cannot_pass():
    item=complete();item['final']['acceptance']['checks']['A3_lvs']='fail'
    assert not A.terminal_complete(item)


def test_all_three_native_paths_must_pass_on_the_identical_runtime(tmp_path):
    import json
    hashes={'memory/scripts/t2a_runtime.py':'frozen-code'}
    items=[]
    for case in ('cts_partial','completed_area','place_partial'):
        root=tmp_path/case;root.mkdir()
        (root/'registration.json').write_text(json.dumps({'case':case,'paper_data':False,
            'runtime_hashes':hashes,'llm_requests':0}))
        (root/'result.json').write_text(json.dumps({'status':'PASS','paper_data':False,
            'checks':{'complete':True},'accounting':{'passed':True,'flows':2,
                'llm_requests':0,'repair_attempts':0}}))
        items.append({'case':case,'root':str(root),'registration_sha256':A.digest(root/'registration.json'),
                      'result_sha256':A.digest(root/'result.json')})
    assert A.native_checks_bound(items,hashes)
    assert not A.native_checks_bound(items[:2],hashes)
    assert not A.native_checks_bound([items[0],items[0],items[2]],hashes)
    assert not A.native_checks_bound(items,{**hashes,'new-file':'different'})
    (tmp_path/'cts_partial/result.json').write_text('{"status":"FAIL"}')
    assert not A.native_checks_bound(items,hashes)
    item=complete();item['paper_data']=True
    assert not A.terminal_complete(item)
    item=complete();item['final']['envelope']['status']='FAIL'
    assert not A.terminal_complete(item)
