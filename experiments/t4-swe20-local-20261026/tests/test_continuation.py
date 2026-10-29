import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from admission import active_registration, row_sha, sha, unresolved_unknowns
from batch import continuation_plan


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(root):
    tasks=[{'instance_id':f'task-{i}'} for i in range(20)]
    for arm in ('native','tehm'):
        save(root/f'hosts/swe-agent/{arm}/episodes/task-0/result.json',{'status':'TECHNICAL_STOP'})
    journal=root/'hosts/swe-agent/native/episodes/task-0/provider/requests.jsonl'
    row={'event':'request_ended','request_local_id':'old-unknown','usage':'UNKNOWN'}
    save(journal,row)
    pause=root/'reports/UNKNOWN-PAUSE.json'
    save(pause,{'source':str(journal.parent),'actual_usage':'UNKNOWN'})
    save(root/'inputs/registration.json',{'version':1})
    auth=root/'inputs/resume-authorization.json'
    save(auth,{'preserved_task':'task-0',
        'remaining_episodes':[[t['instance_id'],a] for t in tasks[1:] for a in ('native','tehm')],
        'unknown_acknowledgements':[{'journal':str(journal.relative_to(root)),
            'journal_sha256':sha(journal),'request_local_id':'old-unknown','event_sha256':row_sha(row)}]})
    reg={'base_registration_sha256':sha(root/'inputs/registration.json'),
         'continuation_authorization':str(auth.relative_to(root)),
         'files':{str(auth.relative_to(root)):sha(auth)},
         'protected_first_pair':{str(journal.relative_to(root)):sha(journal)}}
    save(root/'inputs/registration-v2.json',reg)
    save(root/'inputs/active-registration.json',{'path':'inputs/registration-v2.json',
                                               'sha256':sha(root/'inputs/registration-v2.json')})
    return tasks,reg,journal,auth


def test_acknowledgement_allows_only_old_unknown_without_changing_it(tmp_path):
    _,_,journal,_=fixture(tmp_path)
    original=journal.read_bytes()
    assert unresolved_unknowns(tmp_path)==[]
    assert journal.read_bytes()==original
    assert json.loads(original)['usage']=='UNKNOWN'


def test_new_unknown_pauses_despite_old_pause_file_and_old_approval(tmp_path):
    fixture(tmp_path)
    new=tmp_path/'hosts/swe-agent/tehm/episodes/task-1/provider/requests.jsonl'
    save(new,{'event':'request_ended','request_local_id':'new','usage':'UNKNOWN'})
    assert unresolved_unknowns(tmp_path)[0]['request_local_id']=='new'


def test_changed_acknowledged_journal_is_refused(tmp_path):
    _,_,journal,_=fixture(tmp_path)
    journal.write_text(journal.read_text()+'\n')
    with pytest.raises(ValueError,match='journal changed'):
        unresolved_unknowns(tmp_path)


def test_unbound_authorization_is_refused(tmp_path):
    _,_,_,auth=fixture(tmp_path)
    auth.write_text(auth.read_text()+' ')
    with pytest.raises(ValueError,match='authorization hash'):
        unresolved_unknowns(tmp_path)


def test_first_pair_evidence_cannot_change(tmp_path):
    _,_,journal,_=fixture(tmp_path)
    journal.write_text('tampered')
    with pytest.raises(ValueError,match='first-pair evidence'):
        active_registration(tmp_path)


def test_only_remaining_38_are_selected(tmp_path):
    tasks,reg,_,_=fixture(tmp_path)
    assert continuation_plan(tmp_path,reg,tasks)==tasks[1:]


def test_existing_remaining_episode_never_retried(tmp_path):
    tasks,reg,_,_=fixture(tmp_path)
    (tmp_path/'hosts/swe-agent/tehm/episodes/task-2').mkdir()
    with pytest.raises(ValueError,match='previously started'):
        continuation_plan(tmp_path,reg,tasks)


def test_changed_roster_never_reselected(tmp_path):
    tasks,reg,_,_=fixture(tmp_path)
    tasks[1],tasks[2]=tasks[2],tasks[1]
    with pytest.raises(ValueError,match='authorized remaining 38'):
        continuation_plan(tmp_path,reg,tasks)


def test_old_pause_without_matching_approval_stays_closed(tmp_path):
    (tmp_path/'reports').mkdir()
    save(tmp_path/'reports/UNKNOWN-PAUSE.json',{})
    assert unresolved_unknowns(tmp_path)


def test_original_registration_must_not_be_changed(tmp_path):
    fixture(tmp_path)
    save(tmp_path/'inputs/registration.json',{'version':'tampered'})
    with pytest.raises(ValueError,match='original registration'):
        active_registration(tmp_path)


def test_sandbox_only_symlinks_protected_without_following_outside(tmp_path):
    fixture(tmp_path)
    link=tmp_path/'hosts/swe-agent/native/episodes/task-0/private-python'
    link.symlink_to('/root/nonexistent-sandbox-only-path')
    path=tmp_path/'inputs/registration-v2.json'
    data=json.loads(path.read_text())
    data['protected_first_pair_symlinks']={str(link.relative_to(tmp_path)):str(link.readlink())}
    save(path,data)
    save(tmp_path/'inputs/active-registration.json',{'path':str(path.relative_to(tmp_path)),
                                                  'sha256':sha(path)})
    active_registration(tmp_path)
    link.unlink()
    link.symlink_to('/different-target')
    with pytest.raises(ValueError,match='first-pair symlink'):
        active_registration(tmp_path)
