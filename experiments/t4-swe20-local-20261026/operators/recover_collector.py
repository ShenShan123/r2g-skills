"""Regrade saved native patches without Agent reruns or model requests.

Old results, malformed display patches and UNKNOWN usage remain unchanged.
Both arms get the same artifact recovery, not only the TEHM arm.
"""
import dataclasses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from provision import eval_worktree,record
from run_swe import final_write
from bridge import FullMemory
from host import SoftwareState


def main():
    instance='sphinx-doc__sphinx-9673'
    batch=json.loads((ROOT/'reports/batch-state.json').read_text())
    if batch['status']!='PAUSED_TECHNICAL':
        raise ValueError('wait for both active episodes to end; never touch a running Agent')
    private=next(t for t in json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text()) if t['instance_id']==instance)
    public=next(t for t in json.loads((ROOT/'inputs/public-tasks.json').read_text())['public_tasks'] if t['instance_id']==instance)
    q=json.loads((ROOT/'qualification'/instance/'qualification.json').read_text())
    for arm in ('native','tehm'):
        ep=ROOT/'hosts/swe-agent'/arm/'episodes'/instance
        raw=ep/'result.json'
        result=json.loads(raw.read_text())
        artifact=ep/'home/model.patch'
        if not artifact.exists():
            record(ep/'collector-recovery.json',{
                'kind':'offline artifact audit, NOT an Agent rerun','raw_status':result['status'],
                'raw_result_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
                'verified_patch_status':'UNKNOWN','reason':'no native submitted patch; no patch fabricated from worktree',
                'additional_model_requests':0,'actual_usage':result['usage'],
                'unknown_usage_unchanged':True,'new_admissions_allowed':False})
            print(arm,'UNKNOWN: no native submitted patch; raw status retained')
            continue
        patch=artifact.read_text()
        trajectory=json.loads((ep/'trajectory'/(instance+'.traj')).read_text())
        if trajectory['info'].get('submission')!=patch:
            raise ValueError('native trajectory and raw submission disagree: '+arm)
        worktree=subprocess.check_output(['git','-C',str(ep/'repo'),'diff','--binary','HEAD'],text=True)
        if patch!=worktree:
            raise ValueError('saved native patch is not the final worktree: '+arm)
        started=time.monotonic()
        grade=eval_worktree(private,Path(q['repo']),ep/'home',Path(q['environment_prefix']),
                            ep/'collector-recovery-evaluation',patch)
        receipt={'kind':'offline collector correction, NOT an Agent rerun',
            'raw_result_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
            'raw_status':result['status'],'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),
            'native_patch_matches_trajectory_and_final_worktree':True,
            'verified_patch_status':grade['status'],'evaluation_grade':str(ep/'collector-recovery-evaluation/grade.json'),
            'additional_model_requests':0,'actual_usage':result['usage'],
            'unknown_usage_unchanged':True,'new_admissions_allowed':False,
            'extra_verification_wall_s':time.monotonic()-started,'created':time.time()}
        # The raw episode status is never overwritten or silently treated as a
        # complete cost result. Recovery adds only independently checked facts.
        if arm=='tehm' and grade['status'] in {'PASS','FAIL'}:
            memory=FullMemory('swe-agent',arm=arm,root=ROOT)
            try:
                state=SoftwareState(memory,public,lambda:'')
                state.check={'status':'FAIL','scope':'qualified baseline; hidden test details not exposed'}
                before=state()
                state.read_diff=lambda:patch
                after=dataclasses.replace(state(),reports={'tests':{'status':grade['status'],
                    'scope':'terminal independently verified issue tests'}})
                receipt['terminal_memory_write']=final_write(memory,public,before,after,patch,grade,ep,
                    grade_path=ep/'collector-recovery-evaluation/grade.json')
            except Exception as exc:
                receipt['memory_write_error']=str(exc)
            finally:
                memory.close()
        record(ep/'collector-recovery.json',receipt)
        print(arm,grade['status'],'raw status preserved:',result['status'])


if __name__=='__main__':
    main()
