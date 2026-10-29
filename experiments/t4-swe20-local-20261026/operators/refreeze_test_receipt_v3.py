"""Keep the stale pre-launch freeze; bind the finalized test receipt instead.

The preceding freeze raced the final JUnit write. Its startup gate correctly
refused it before any launch. No runtime, authorization or episode is changed.
"""
import copy
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from admission import active_registration,sha,unresolved_unknowns
from batch import continuation_plan,roster
from provision import record


def main():
    pointer=ROOT/'inputs/active-registration.json'
    previous_pointer=pointer.read_text()
    previous=json.loads(previous_pointer)
    if previous['path']!='inputs/registration-v2.json':
        raise ValueError('only the unlaunched v2 receipt may be superseded')
    base=active_registration(ROOT)
    changed=[rel for rel,h in base['files'].items() if sha(ROOT/rel)!=h]
    if changed!=['reports/continuation-v2-tests.xml']:
        raise ValueError('not a test-receipt-only delta: '+str(changed))
    tests=ROOT/'reports/continuation-v2-tests.xml'
    test_sha=sha(tests)
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)!=25 or any(any(c.find(k) is not None for k in ('error','failure','skipped')) for c in cases):
        raise ValueError('final 25-test regression must pass')
    continuation_plan(ROOT,base,roster())
    value=copy.deepcopy(base)
    value.update(epoch='collector-continuation-v3',created=time.time(),
        previous_registration=previous['path'],previous_registration_sha256=previous['sha256'],
        prelaunch_delta='JUnit receipt finalization only; runtime identical to unlaunched v2',
        regression_tests=len(cases))
    value['files']['reports/continuation-v2-tests.xml']=test_sha
    value['files'][str(Path(__file__).relative_to(ROOT))]=sha(Path(__file__))
    target=ROOT/'inputs/registration-v3.json'
    if target.exists():
        raise ValueError('v3 already exists')
    if sha(tests)!=test_sha:
        raise ValueError('test receipt is still changing')
    record(target,value)
    archive=ROOT/'reports/continuation-v2-active-pointer.json'
    with archive.open('x') as out:
        out.write(previous_pointer)
    record(pointer,{'path':str(target.relative_to(ROOT)),'sha256':sha(target)})
    from run_swe import check_freeze
    check_freeze()
    if unresolved_unknowns(ROOT):
        raise ValueError('unacknowledged UNKNOWN')
    receipt={'status':'FROZEN_READY','registration':str(target),
        'registration_sha256':sha(target),'runtime_changes_since_v2':0,
        'preserved_first_pair_files':len(value['protected_first_pair']),
        'remaining_unstarted':38,'tests_passed':len(cases),'created':time.time(),
        'superseded_prelaunch_freeze':'v2 refused before launch: test XML finalized after freeze; original receipt preserved'}
    record(ROOT/'reports/continuation-v3-freeze.json',receipt)
    print(json.dumps(receipt))


if __name__=='__main__':
    main()
