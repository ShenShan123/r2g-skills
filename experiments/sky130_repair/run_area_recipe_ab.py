#!/usr/bin/env python3
"""Execute prepared R2G-selected A/B arms with the frozen physical toolchain.

Single eight-core slot. R2G's standard CLI already applied B's recipe. Only
path relocation to the container is performed here; no strategy is selected or
invented by this executor. Judging/promotion happens separately from execution.
"""
import dataclasses
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'agent'),str(HERE.parents[1]/'r2g-skills/signoff-loop/scripts/reports')]
from area_trial_evidence import classify
import run_campaign as c
from aux208_container_adapter import IMAGE


def relocate_intervention(cfg,role,strategy,work,repo):
    if strategy!='fixed_area_route_relief' or role not in ('A','B'):
        raise ValueError('unsupported area intervention')
    if any(c.mk(cfg,key) for key in ('PRE_IO_PLACEMENT_TCL','POST_GLOBAL_PLACE_TCL','PRE_PDN_TCL')):
        raise ValueError('unexpected hook in area-only trial')
    return cfg


def verify_area_config(arm):
    from fixed_area_recipe import candidate
    project=Path(arm['project']);subject=Path(arm['subject'])
    original=(subject/'constraints/config.mk').read_text()
    import re
    cfg=dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$',original,re.M))
    strategy=candidate(subject,{'status':'fail'},cfg)
    if strategy is None:raise ValueError('original area geometry no longer proves candidate')
    expected=original
    if arm['role']=='B':
        for key,value in strategy['config_edits'].items():expected=c.set_mk(expected,key,value)
    expected=expected.replace(str(subject),str(project))
    actual=(project/'constraints/config.mk').read_text()
    parse=lambda text:dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$',text,re.M))
    if parse(expected)!=parse(actual):raise ValueError('area arm changes more than reviewed geometry')


def verify_pdn_arm_inputs(arm):
    project=Path(arm['project']);provenance=project/'source_provenance.json'
    if c.sha(provenance)!=arm['source_provenance_sha256']:raise ValueError('prepared source provenance changed')
    original=json.loads(provenance.read_text())
    if original['design']!=arm['case'] or original['family']!=arm['source_repository']:raise ValueError('prepared subject identity differs')
    for rel,digest in original['source_files'].items():
        path=project/'rtl'/rel
        if not path.resolve().is_relative_to(project/'rtl') or c.sha(path)!=digest:raise ValueError('prepared RTL/ROM changed')
    if c.sha(project/'constraints/constraint.sdc')!=original['sdc_sha256']:raise ValueError('prepared SDC changed')


def main():
    root=Path('/tmp/sky130_aux208');repo=HERE.parents[1]
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=Path,default=root/'area_recipe/trials')
    parser.add_argument('--case',help='Run only this prepared case ID, keeping its repeats on one worker')
    parser.add_argument('--auxiliary-slot',type=int,choices=(0,1),default=0)
    args=parser.parse_args()
    layout_tool=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
    if c.sha(layout_tool)!='6454121a552fc8625fa27f474dfc699f2d82a10a8959238168f2c2249988fd48':
        raise ValueError('geometry reader differs from reviewed frozen tool')
    os.environ['R2G_LAYOUT_OPENROAD']=str(layout_tool)
    trials=args.trials.resolve()
    if not trials.is_relative_to(root):raise ValueError('trial directory outside auxiliary root')
    lock=(root/('worker.lock' if args.auxiliary_slot==0 else 'review_worker.lock')).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    plan=json.loads((trials/'plan.json').read_text())
    arms=[arm for arm in plan['arms'] if not args.case or arm['case'].split('__')[0]==args.case]
    if not arms:raise ValueError('case is absent from the prepared trial')
    if plan['key']['strategy']!='fixed_area_route_relief':raise ValueError('unsupported physical A/B strategy')
    manifest_path=trials/('execution_manifest'+('_'+args.case if args.case else '')+'.json')
    if manifest_path.exists():
        old=json.loads(manifest_path.read_text())
        if old['runner_sha256']!=c.sha(Path(__file__)) and any(not (Path(a['project'])/'execution.json').exists() for a in arms):
            raise ValueError('executor changed during an unfinished trial; retain evidence and review before resuming')
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    if image['Id']!=IMAGE:raise ValueError('physical image changed')
    for rel,digest in json.loads((root/'original_files.json').read_text()).items():
        if c.sha(root/rel)!=digest:raise ValueError('frozen input changed: '+rel)
    overlay=root/'checker_strict_verdict';provenance=json.loads((overlay/'overlay_provenance.json').read_text())
    for change in provenance['differences']:
        if c.sha(overlay/change['file'])!=change['after']:raise ValueError('checker helper changed')
    for rel,digest in json.loads((root/'expected_signoff_libraries.json').read_text()).items():
        if c.sha(root/'environment/miniconda3/envs/eda'/rel)!=digest:raise ValueError('comparison deck/library changed')
    for rel,digest in {
        'bin/magic':'56300dc410b2ddb783612ef38edb6dde1879c4dc21edc6195575d507161e8ed6',
        'bin/netgen':'8349a7edd856773cbbc6be319bcf5cf3477f911d4300f6165735ee449dd0e7a8',
    }.items():
        if c.sha(root/'environment/miniconda3/envs/eda'/rel)!=digest:raise ValueError('frozen signoff executable changed')
    token='recipeab_'+str(os.getpid())
    os.environ.update(R2G_AUX208_ROOT=str(root),R2G_AUX208_TOKEN=token,
                      PATH=str(root/'tools/bin')+os.pathsep+os.environ['PATH'])
    c.HERE=root/'tools';driver=c.module('recipe_ab_driver',root/'tools/orfs_spec.py')
    drc=c.module('recipe_ab_drc',root/'tools/signoff_pass.py');lvs=c.module('recipe_ab_lvs',root/'tools/lvs130.py');lvs.R2G=overlay
    if not manifest_path.exists():
        c.save(manifest_path,dict(image=IMAGE,checker_overlay=provenance,runner_sha256=c.sha(Path(__file__)),
            workers=1,cores=8,maximum_auxiliary_slots=2,auxiliary_slot=args.auxiliary_slot,
            timeout=c.TIMEOUT,key=plan['key'],plan_sha256=c.sha(trials/'plan.json'),mode=plan.get('mode','planned_ab'),started_at=time.strftime('%FT%T%z')))
    try:
        for arm in arms:
            project=Path(arm['project']);receipt=project/'execution.json'
            if not project.resolve().is_relative_to(trials):raise ValueError('arm escapes trial directory')
            arm_lock=(project/'execution.lock').open('a');fcntl.flock(arm_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if receipt.exists():
                old=json.loads(receipt.read_text())
                if old['status']!='completed':raise ValueError('unfinished arm requires live-process inspection before resuming')
                if classify(Path(old['run']),old['flow_returncode'])!=old['acceptance']:
                    raise ValueError('completed arm signoff changed')
                arm_lock.close()
                continue
            if shutil.disk_usage(root).free<40*1024**3:raise ValueError('insufficient disk for next physical A/B arm')
            cfgpath=project/'constraints/config.mk'
            if c.sha(cfgpath)!=arm['config_sha256']:raise ValueError('prepared intervention changed')
            verify_pdn_arm_inputs(arm)
            verify_area_config(arm)
            work=project/'ab_work';work.mkdir(exist_ok=False);(work/'subject').mkdir()
            shutil.copytree(project/'rtl',work/'subject/rtl')
            for p in (project/'rtl').rglob('*'):
                if p.is_file() and c.sha(p)!=c.sha(work/'subject/rtl'/p.relative_to(project/'rtl')):raise ValueError('RTL copy differs')
            cfg=cfgpath.read_text().replace(str(project),'/work/subject')
            cfg=c.set_mk(cfg,'SDC_FILE','/work/constraint.sdc')
            cfg=relocate_intervention(cfg,arm['role'],plan['key']['strategy'],work,repo)
            (work/'config.mk').write_text(cfg);shutil.copy2(project/'constraints/constraint.sdc',work/'constraint.sdc')
            record=dict(arm=arm,status='flow_running',work=str(work),config_path_relocation_only=True)
            c.save(receipt,record);start=time.time()
            rc=c.podman(work,'ab_'+project.name,'cd "$ORFS_ROOT/flow" && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work',work/'flow.log')
            run=project/'physical_run';driver.harvest(work,run)
            result=driver.Result(design=arm['case'],platform='sky130hd',period='5')
            result.seconds=round(time.time()-start,1);result.stage=driver.last_stage(work)
            result.status='pass' if rc==0 and (run/'6_final.gds').is_file() else 'fail'
            driver.read_metrics(work,result);c.save(run/'result.json',dataclasses.asdict(result))
            record.update(flow_returncode=rc,run=str(run),flow_seconds=result.seconds)
            if result.status=='pass':
                record['status']='signoff_running';c.save(receipt,record)
                drc.one_and_record(run,project/'drc_projects',c.TIMEOUT,'sky130hd')
                lvs.one_and_record(run,project/'lvs_projects',c.TIMEOUT,'sky130hd')
                record['strict_signoff']=c.inspect_run(run)
            record['acceptance']=classify(run,rc)
            record.update(status='completed',wall_seconds=round(time.time()-start,1))
            c.save(receipt,record)
            print(json.dumps(dict(project=project.name,role=arm['role'],repeat=arm['repeat'],
                flow_returncode=rc,signoff=record.get('strict_signoff'))),flush=True)
            arm_lock.close()
    finally:
        ids=subprocess.check_output(['docker','ps','-aq','--filter','label=r2g.aux208.run='+token],text=True).split()
        if ids:subprocess.run(['docker','rm','-f',*ids],check=True,capture_output=True)


if __name__=='__main__':main()
