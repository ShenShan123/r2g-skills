"""One authorized re-freeze, preserving the original first pair and UNKNOWN."""
import copy
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from admission import sha,row_sha,unresolved_unknowns
from batch import roster,continuation_plan
from provision import record


def main():
    active=ROOT/'inputs/active-registration.json'
    if active.exists():
        raise ValueError('continuation already frozen; never overwrite')
    tests=ROOT/'reports/continuation-v2-tests.xml'
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)<22 or any(any(c.find(k) is not None for k in ('error','failure','skipped')) for c in cases):
        raise ValueError('continuation regression has not passed')
    original=ROOT/'inputs/registration.json'
    base=json.loads(original.read_text())
    changes={rel for rel,h in base['files'].items() if sha(ROOT/rel)!=h}
    allowed={'adapters/run_swe.py','adapters/batch.py'}
    if changes!=allowed:
        raise ValueError('unexpected delta from original runtime: '+str(changes))
    tasks=roster()
    first=tasks[0]['instance_id']
    protected={}
    protected_links={}
    for arm in ('native','tehm'):
        episode=ROOT/'hosts/swe-agent'/arm/'episodes'/first
        for path in sorted(episode.rglob('*')):
            if path.is_symlink():
                protected_links[str(path.relative_to(ROOT))]=str(path.readlink())
            elif path.is_file():
                protected[str(path.relative_to(ROOT))]=sha(path)
    journal=ROOT/'hosts/swe-agent/native/episodes'/first/'provider/requests.jsonl'
    ident='d9def14501734d6aa8b772cf93ea1a8d'
    hits=[json.loads(s) for s in journal.read_text().splitlines()
          if json.loads(s).get('event')=='request_ended' and json.loads(s).get('request_local_id')==ident]
    if len(hits)!=1 or hits[0].get('usage')!='UNKNOWN':
        raise ValueError('original UNKNOWN event not uniquely identified')
    auth=ROOT/'inputs/continuation-v2-authorization.json'
    if auth.exists():
        raise ValueError('authorization already recorded')
    record(auth,{'user_authorization':'授权保留本次 UNKNOWN、不重跑首组，重新冻结修复版后接续剩余 38 个任务',
        'recorded_at':time.time(),'preserved_task':first,
        'remaining_episodes':[[t['instance_id'],a] for t in tasks[1:] for a in ('native','tehm')],
        'scope':'SWE20 only; two native/TEHM lanes; no episode or request retry',
        'actual_unknown_usage':'UNKNOWN; no invented token occupancy and no cumulative cap',
        'new_unknown_policy':'pause; this acknowledgement is for this request only',
        'unknown_acknowledgements':[{'journal':str(journal.relative_to(ROOT)),
            'journal_sha256':sha(journal),'request_local_id':ident,'event_sha256':row_sha(hits[0])}],
        'original_pause_sha256':sha(ROOT/'reports/UNKNOWN-PAUSE.json')})
    value=copy.deepcopy(base)
    value.update(epoch='collector-continuation-v2',created=time.time(),
        base_registration_sha256=sha(original),
        continuation_authorization=str(auth.relative_to(ROOT)),protected_first_pair=protected,
        protected_first_pair_symlinks=protected_links,
        runtime_delta=sorted(allowed|{'adapters/admission.py'}),
        completed_on_original_runtime=2,remaining_on_this_runtime=38,
        unknown_policy='actual UNKNOWN retained; exact acknowledged request permits continuation; new UNKNOWN pauses')
    extra=list((ROOT/'adapters').glob('*.py'))+list((ROOT/'tests').glob('*.py'))+[
        auth,tests,Path(__file__),ROOT/'operators/qualify_remaining_v2.py']
    for path in extra:
        value['files'][str(path.relative_to(ROOT))]=sha(path)
    continuation_plan(ROOT,value,tasks)
    target=ROOT/'inputs/registration-v2.json'
    if target.exists():
        raise ValueError('v2 registration already exists')
    record(target,value)
    record(active,{'path':str(target.relative_to(ROOT)),'sha256':sha(target)})
    from run_swe import check_freeze
    check_freeze()
    if unresolved_unknowns(ROOT):
        raise ValueError('UNKNOWN admission remains blocked')
    record(ROOT/'reports/continuation-v2-freeze.json',{'status':'FROZEN_READY','registration':str(target),
        'registration_sha256':sha(target),'preserved_first_pair_files':len(protected),
        'preserved_first_pair_symlinks':len(protected_links),
        'unstarted_episodes':38,'new_unknowns_pause':True,'tests':len(cases),'created':time.time()})
    print(json.dumps({'status':'FROZEN_READY','sha256':sha(target),'remaining':38,'protected_files':len(protected)}))


if __name__=='__main__':
    main()
