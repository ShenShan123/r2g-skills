import json
import fcntl
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import t2_freepdk45_corrective as C


def fixture(tmp_path):
    root=tmp_path/'root';revision=root/'revision';revision.mkdir(parents=True)
    probe=root/'probe';probe.mkdir()
    source=root/'nb/episodes/C01930__floppy__ao486__initial__NB__r1';source.mkdir(parents=True)
    inputs={}
    for name in ('input.gds','input.cdl','deck.lylvs'):
        p=source/name;p.write_text(name);inputs[str(p)]=C.sha(p)
        (probe/name).write_bytes(p.read_bytes())
    (probe/'result.lvsdb').write_text('native database')
    (probe/'checker.log').write_text('CONGRATULATIONS! Netlists match.\n')
    receipt={'source':str(source),'passed':True,'source_unchanged':True,'exit_status':0,
        'crash':False,'timed_out':False,'negative_control':False,'verdict':'MATCH',
        'executable_sha256':'checker','model_requests':0,'implementation_flows':0,
        'native_checker_invocations':1,'implicit_retries':0,'inputs':inputs}
    C.save(revision/'registration.json',{'native_probe':str(probe/'receipt.json'),'checker_sha256':'checker'})
    return root,revision,probe,receipt


def test_operator_waits_for_a_real_terminal_receipt(tmp_path):
    root,revision,probe,receipt=fixture(tmp_path)
    assert C.native_followup_ready(root,revision) is False
    C.save(probe/'receipt.json',receipt)
    assert C.native_followup_ready(root,revision) is True


@pytest.mark.parametrize('edit',[
    {'passed':False}, {'source_unchanged':False}, {'exit_status':124},
    {'crash':True}, {'timed_out':True}, {'negative_control':True},
    {'verdict':'UNKNOWN'}, {'executable_sha256':'different'},
    {'model_requests':1}, {'implementation_flows':1},
    {'native_checker_invocations':2}, {'implicit_retries':1},
    {'source':'another-design'},
])
def test_failed_or_unrelated_validation_never_opens_the_queue(tmp_path,edit):
    root,revision,probe,receipt=fixture(tmp_path);receipt.update(edit)
    C.save(probe/'receipt.json',receipt)
    with pytest.raises(ValueError):C.native_followup_ready(root,revision)


@pytest.mark.parametrize('changed',['input.gds','input.cdl','deck.lylvs','result.lvsdb','checker.log','source'])
def test_input_or_verdict_drift_blocks_activation(tmp_path,changed):
    root,revision,probe,receipt=fixture(tmp_path);C.save(probe/'receipt.json',receipt)
    if changed=='source':Path(next(iter(receipt['inputs']))).write_text('different')
    elif changed=='result.lvsdb':(probe/changed).unlink()
    elif changed=='checker.log':(probe/changed).write_text('CONGRATULATIONS! Netlists match.\nSignal number: 11\n')
    else:(probe/changed).write_text('different')
    with pytest.raises(ValueError):C.native_followup_ready(root,revision)


def test_real_mismatch_is_a_valid_checker_result(tmp_path):
    root,revision,probe,receipt=fixture(tmp_path);receipt['verdict']='MISMATCH'
    (probe/'checker.log').write_text("ERROR : Netlists don't match\n")
    C.save(probe/'receipt.json',receipt)
    assert C.native_followup_ready(root,revision) is True


def test_preparation_does_not_overwrite_an_existing_revision(tmp_path):
    with pytest.raises(FileExistsError):C.prepare_native_followup(tmp_path,tmp_path,tmp_path/'unread.xml')


def test_invalid_test_receipt_prevents_any_installation_preparation(tmp_path):
    tests=tmp_path/'tests.xml';tests.write_text('<testsuite><testcase><failure/></testcase></testsuite>')
    revision=tmp_path/'revision'
    with pytest.raises(ValueError,match='Passing corrective tests'):C.prepare_native_followup(tmp_path,revision,tests)
    assert not revision.exists()


def installation_fixture(tmp_path,monkeypatch):
    root=tmp_path/'root';revision=root/'revision';revision.mkdir(parents=True)
    (root/'nb/episodes/closed').mkdir(parents=True)
    episode=root/'nb/episodes/closed/episode.json';C.save(episode,{'status':'ACCEPTED'})
    old=root/'code/runtime.py';old.parent.mkdir();old.write_text('old runtime')
    staged=revision/'staged.py';staged.write_text('new runtime')
    tests=revision/'offline-tests.xml';tests.write_text('<testsuite>'+'<testcase/>'*30+'</testsuite>')
    C.save(root/'nb/registration.json',{'files':{},'external_hashes':{}})
    C.save(root/'registration.json',{'files':{str(root/'nb/registration.json'):C.sha(root/'nb/registration.json')},'external_hashes':{}})
    for name in ('dispatch-state.json','nb/dispatch-state.json'):C.save(root/name,{'status':'PAUSED'})
    grant={'root':str(root),'operator_sha256':C.sha(C.__file__),
        'base_registration_sha256':C.sha(root/'registration.json'),
        'base_nb_registration_sha256':C.sha(root/'nb/registration.json'),
        'files':{str(old):{'staged':str(staged),'sha256':C.sha(staged),'previous_sha256':C.sha(old)}},
        'tool_files':{},'finished_receipts':{},'offline_tests_sha256':C.sha(tests),
        'native_probe':str(revision/'native.json')}
    C.save(revision/'native.json',{'passed':True})
    C.save(revision/'registration.json',grant)
    monkeypatch.setattr(C,'native_followup_ready',lambda *args:True)
    monkeypatch.setattr(C,'followup_reviews',lambda *args:{})
    launches=[];monkeypatch.setattr(C.os,'execv',lambda *args:launches.append(args))
    return root,revision,old,episode,launches


def test_installation_preserves_closed_episode_and_validates_both_registrations(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    before=C.sha(episode)
    C.operate_native_followup(root,revision)
    assert C.sha(episode)==before and old.read_text()=='new runtime'
    assert (revision/'original/code/runtime.py').read_text()=='old runtime'
    import t2_freepdk45_main as M
    M.F.verify_files(C.load(root/'registration.json')['files'])
    M.F.verify_files(C.load(root/'nb/registration.json')['files'])
    assert C.load(revision/'operator.json')['status']=='INSTALLED_VERIFIED_ACTIVATING'
    assert len(launches)==1 and launches[0][1][-2:]==['--correction',str(revision)]


@pytest.mark.parametrize('blocker',['active_worker','leased_supervisor','changed_runtime','failed_native'])
def test_installation_does_not_touch_live_runtime_on_a_failed_gate(tmp_path,monkeypatch,blocker):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    lease=None
    if blocker=='active_worker':C.save(episode,{'status':'RUNNING'})
    elif blocker=='leased_supervisor':
        lease=(root/'supervisor.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    elif blocker=='changed_runtime':old.write_text('unexpected change')
    else:
        def refuse(*args):raise ValueError('native validation failed')
        monkeypatch.setattr(C,'native_followup_ready',refuse)
    previous=old.read_bytes();reg=C.sha(root/'registration.json')
    try:
        with pytest.raises((ValueError,BlockingIOError)):C.operate_native_followup(root,revision)
    finally:
        if lease:fcntl.flock(lease,fcntl.LOCK_UN);lease.close()
    assert old.read_bytes()==previous and C.sha(root/'registration.json')==reg
    assert not launches and C.load(revision/'operator.json')['status']=='BLOCKED_VALIDATION_OR_INTEGRITY'


def test_a_revision_waits_for_its_own_named_probe_and_ignores_an_earlier_timed_out_one(tmp_path):
    """2026-10-22: the first floppy probe timed out at 5,400 s (UNKNOWN). A new revision names a second,
    explicitly logged probe of the same original case; the first receipt stays on disk unchanged and
    neither blocks nor opens the new revision."""
    root,revision,probe,receipt=fixture(tmp_path)
    first=root/'lvs-config-fix-20261023/floppy-native-watchdog';first.mkdir(parents=True)
    C.save(first/'receipt.json',{**receipt,'exit_status':124,'timed_out':True,'verdict':'UNKNOWN','passed':False})
    C.save(probe/'receipt.json',receipt)
    assert C.native_followup_ready(root,revision) is True
    assert json.loads((first/'receipt.json').read_text())['verdict']=='UNKNOWN'


def test_preparation_names_the_given_probe_or_the_first_probe_by_default():
    import inspect
    src=inspect.getsource(C.prepare_native_followup)
    assert "Path(probe).resolve() if probe else" in src
    assert "root/'lvs-config-fix-20261023/floppy-native-watchdog/receipt.json'" in src


def test_config_only_revision_installs_without_any_native_probe(tmp_path,monkeypatch):
    """2026-10-22 user decision: config fix with the old checker. No probe is consulted or bound."""
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    grant=C.load(revision/'registration.json');grant.update(checker_swap=False,native_probe=None,checker_sha256=None)
    C.save(revision/'registration.json',grant)
    def never(*args):raise AssertionError('native gate consulted')
    monkeypatch.setattr(C,'native_followup_ready',never)
    before=C.sha(episode)
    C.operate_native_followup(root,revision)
    assert C.sha(episode)==before and old.read_text()=='new runtime' and len(launches)==1
    applied=C.load(revision/'applied.json')
    assert not any('native' in p for p in applied['files'])


def test_a_checker_swap_revision_without_a_probe_never_installs(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    grant=C.load(revision/'registration.json');grant['native_probe']=None
    C.save(revision/'registration.json',grant)
    with pytest.raises(ValueError,match='Checker swap without a native probe'):C.operate_native_followup(root,revision)
    assert old.read_text()=='old runtime' and not launches
    assert C.load(revision/'operator.json')['status']=='BLOCKED_VALIDATION_OR_INTEGRITY'


def test_config_only_preparation_never_stages_the_checker_pin():
    import inspect
    src=inspect.getsource(C.prepare_native_followup)
    assert "if not checker_swap:continue\n        dest=code/'memory/scripts/t2a_batch.py'" in src
    assert "'tool_files':snap['checker_candidate_files'] if checker_swap else {}" in src


def test_root_registration_without_external_hashes_installs(tmp_path,monkeypatch):
    """The live FreePDK45 root registration has no external_hashes key; the first config-only
    install (2026-10-22) raised KeyError after writing the nb registration. The fixture hid it."""
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    reg=C.load(root/'registration.json');del reg['external_hashes'];C.save(root/'registration.json',reg)
    grant=C.load(revision/'registration.json');grant['base_registration_sha256']=C.sha(root/'registration.json')
    C.save(revision/'registration.json',grant)
    C.operate_native_followup(root,revision)
    assert old.read_text()=='new runtime' and len(launches)==1


def test_a_failure_during_install_rolls_everything_back(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    added=root/'code/added.sh';staged=revision/'added.sh';staged.write_text('new file')
    grant=C.load(revision/'registration.json')
    grant['files'][str(added)]={'staged':str(staged),'sha256':C.sha(staged),'previous_sha256':None}
    C.save(revision/'registration.json',grant)
    bases={p:C.sha(root/p) for p in ('registration.json','nb/registration.json')}
    real=C.save
    def failing(path,data):
        if Path(path)==root/'registration.json':raise KeyError('external_hashes')
        return real(path,data)
    monkeypatch.setattr(C,'save',failing)
    with pytest.raises(KeyError):C.operate_native_followup(root,revision)
    assert old.read_text()=='old runtime' and not added.exists() and not launches
    assert all(C.sha(root/p)==h for p,h in bases.items())
    assert C.load(revision/'rolled-back.json')['verified'] is True


def native_crash_cell(tmp_path,signal=True,error='Initial signoff was not measured; no model repair of checker failure'):
    p=tmp_path/'C03610__register_file_top__x__initial__NB__r1';(p/'reports').mkdir(parents=True);(p/'lvs').mkdir()
    C.save(p/'reports/lvs.json',{'status':'crash','reason':'klayout_cpp_crash'})
    (p/'lvs/6_lvs.log').write_text('ERROR: Signal number: 11\n' if signal else 'CONGRATULATIONS! Netlists match.\n')
    return p,{'error':error}


def test_native_lvs_crash_classifier(tmp_path):
    p,e=native_crash_cell(tmp_path);assert C.known_native_lvs_crash(p,e)
    p,e=native_crash_cell(tmp_path/'a',signal=False);assert not C.known_native_lvs_crash(p,e)
    p,e=native_crash_cell(tmp_path/'b',error='Candidate tool was technically terminated');assert not C.known_native_lvs_crash(p,e)
    p,e=native_crash_cell(tmp_path/'c');C.save(p/'reports/t2a-workspace-unsafe.json',{})
    assert not C.known_native_lvs_crash(p,e)
    p,e=native_crash_cell(tmp_path/'d');C.save(p/'reports/lvs.json',{'status':'fail'});assert not C.known_native_lvs_crash(p,e)


def test_acknowledgement_requires_a_user_authorization(tmp_path):
    with pytest.raises(ValueError,match='user authorization'):
        C.prepare_acknowledgement(tmp_path,tmp_path/'rev',tmp_path/'tests.xml','')


def test_acknowledgement_only_revision_changes_no_runtime_file_and_binds_the_reviews(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    grant=C.load(revision/'registration.json')
    grant.update(files={},checker_swap=False,native_probe=None,checker_sha256=None)
    C.save(revision/'registration.json',grant)
    review=revision/'outcome-reviews/C03610.json';C.save(review,{'classification':'UNVERIFIED_OR_SAFETY_STOP'})
    monkeypatch.setattr(C,'followup_reviews',lambda *a:{str(root/'nb/episodes/C03610'):{'path':str(review),'sha256':C.sha(review)}})
    C.operate_native_followup(root,revision)
    assert old.read_text()=='old runtime' and len(launches)==1
    nbreg=C.load(root/'nb/registration.json')
    assert str(root/'nb/episodes/C03610') in nbreg['acknowledged_technical_stops']


def test_memory_resume_requires_authorization_and_a_paused_queue(tmp_path):
    with pytest.raises(ValueError,match='user authorization'):
        C.prepare_memory_resume(tmp_path,tmp_path/'rev',tmp_path/'t.xml','',tmp_path)
    tests=tmp_path/'t.xml';tests.write_text('<testsuite>'+'<testcase/>'*30+'</testsuite>')
    C.save(tmp_path/'dispatch-state.json',{'status':'BUILDING_MEMORY'})
    with pytest.raises(ValueError,match='not technically paused'):
        C.prepare_memory_resume(tmp_path,tmp_path/'rev',tests,'user text',tmp_path)
    assert not (tmp_path/'rev').exists()


def test_activation_command_is_restricted(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    grant=C.load(revision/'registration.json');grant['activation_command']='drain';C.save(revision/'registration.json',grant)
    with pytest.raises(ValueError,match='Unregistered activation command'):C.operate_native_followup(root,revision)
    assert not launches


def test_memory_resume_activation_runs_resume_memory(tmp_path,monkeypatch):
    root,revision,old,episode,launches=installation_fixture(tmp_path,monkeypatch)
    grant=C.load(revision/'registration.json');grant['activation_command']='resume-memory';C.save(revision/'registration.json',grant)
    C.operate_native_followup(root,revision)
    assert launches and launches[0][1][2]=='resume-memory'
