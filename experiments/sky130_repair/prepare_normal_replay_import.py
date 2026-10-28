"""Package a new signed normal replay for the existing strict 216 importer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path.insert(0,str(HERE))
from area_trial_evidence import classify
from run_recipe_ab import verify_pdn_arm_inputs
from strict_signoff import inspect_run
import run_campaign as campaign

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def validate_normal_selection(trials,plan,config):
    strategy=plan['key']['strategy']
    knobs={'lvs_port_feedthrough_buffer':{'POST_GLOBAL_PLACE_TCL'},
           'pin_pdn_clearance':{'PRE_IO_PLACEMENT_TCL'},
           'fixed_area_route_relief':{'DIE_AREA','CORE_AREA'}}
    if strategy not in knobs:raise ValueError('unsupported normal physical recipe')
    stage='place' if strategy=='lvs_port_feedthrough_buffer' else 'floorplan'
    check={'lvs_port_feedthrough_buffer':'lvs','pin_pdn_clearance':'drc','fixed_area_route_relief':'route'}[strategy]
    selected=json.loads((trials/'selection.json').read_text())
    if selected['returncode']!=0 or selected['stdout'].strip().split('\t')!=[strategy,stage,check]:
        raise ValueError('ordinary selection receipt differs')
    applied=json.loads((trials/'application.json').read_text())
    if applied['returncode']!=0:raise ValueError('ordinary recipe application failed')
    action=json.loads(applied['stdout'])
    if action.get('status')!='applied' or action.get('applied')!=strategy:
        raise ValueError('ordinary application receipt differs')
    edits=action.get('config_edits')
    # CLI appends an auto-fix block; later Make assignments override originals.
    effective=dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$',config,re.M))
    if not isinstance(edits,dict) or set(edits)!=knobs[strategy] or any(effective.get(k,'').strip()!=v for k,v in edits.items()):
        raise ValueError('ordinary application edits differ from executed recipe')
    if action.get('sdc_edits') or action.get('env_edits'):
        raise ValueError('ordinary application contains extra intervention')


def prepare(trials,validate_only=False):
    trials=Path(trials).resolve();auxiliary=Path('/tmp/sky130_aux208')
    if not trials.is_relative_to(auxiliary):raise ValueError('outside auxiliary evidence root')
    plan=json.loads((trials/'plan.json').read_text())
    if plan.get('mode')!='normal_unforced_independent_replay' or plan.get('forced_selection') is not False or len(plan['arms'])!=1:
        raise ValueError('not an independent ordinary replay')
    arm=plan['arms'][0];project=Path(arm['project'])
    if not project.resolve().is_relative_to(trials):raise ValueError('ordinary replay project escapes trial root')
    validate_normal_selection(trials,plan,(project/'constraints/config.mk').read_text())
    execution=json.loads((project/'execution.json').read_text())
    if execution['status']!='completed':raise ValueError('physical replay is still incomplete')
    run=Path(execution['run']);accepted=classify(run,execution['flow_returncode'])
    if not accepted['is_success']:raise ValueError('fresh strict DRC/LVS dual-clean is required')
    if not json.loads((run/'result.json').read_text()).get('cells',0):raise ValueError('nonempty cell metrics required')
    if execution.get('acceptance',accepted)!=accepted:raise ValueError('acceptance evidence changed')
    if execution.get('strict_signoff')!=accepted['strict_signoff']:raise ValueError('strict signature differs')
    for name in ('6_final.gds','6_final.def','6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log'):
        if not (run/name).is_file() or not (run/name).stat().st_size:raise ValueError('missing signed artifact: '+name)
    verify_pdn_arm_inputs(arm)
    if sha(project/'constraints/config.mk')!=arm['config_sha256']:raise ValueError('prepared config changed')
    provenance=json.loads((project/'source_provenance.json').read_text());work=Path(execution['work'])
    for rel,digest in provenance['source_files'].items():
        if sha(work/'subject/rtl'/rel)!=digest:raise ValueError('executed RTL/asset copy changed')
    if sha(work/'constraint.sdc')!=provenance['sdc_sha256']:raise ValueError('executed SDC changed')
    expected=(project/'constraints/config.mk').read_text().replace(str(project),'/work/subject')
    expected=campaign.set_mk(expected,'SDC_FILE','/work/constraint.sdc')
    strategy=plan['key']['strategy']
    hooks={'lvs_port_feedthrough_buffer':('POST_GLOBAL_PLACE_TCL','buffer_port_feedthroughs.tcl','98baa3fb699c67fa72a195184f16fae8ad760f383459c54f339b10dc31b18b29'),
           'pin_pdn_clearance':('PRE_IO_PLACEMENT_TCL','pin_pdn_clearance.tcl','9338d25a0f60802dcd71ea0a40b011a96e45c2f03c63d91f1a2b648e3fae94b6')}
    if strategy in hooks:
        knob,name,digest=hooks[strategy];original=REPO/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/name
        if sha(work/name)!=digest or sha(original)!=digest:raise ValueError('executed hook differs from published hook')
        expected=campaign.set_mk(expected,knob,'/work/'+name)
    elif strategy!='fixed_area_route_relief':raise ValueError('unsupported physical recipe')
    if expected!=(work/'config.mk').read_text():raise ValueError('executed config has an unreviewed change')
    design=arm['case'];snapshot=json.loads((HERE/'status_snapshot_20261027_latest.json').read_text())
    row=next(r for r in snapshot['designs'] if r['design']==design)
    if row['physical_verdict_verified'] and not validate_only:raise ValueError('historical win already counted; archive replay separately')
    subject=Path(arm['subject']);candidates=list(subject.glob('backend/RUN_FROZEN*/results'))
    if len(candidates)!=1:raise ValueError('ambiguous original baseline')
    baseline=candidates[0]
    if json.loads((baseline/'result.json').read_text())['design']!=design:raise ValueError('different baseline design')
    baseline_hashes={n:sha(baseline/n) for n in ('config.mk','constraint.sdc','result.json')}
    if sha(work/'constraint.sdc')!=baseline_hashes['constraint.sdc']:raise ValueError('clock constraints differ from original')
    original_cfg=(baseline/'config.mk').read_text().replace('/proj/workarea/yangao/rtl-certified-handoff-0002/sources/',str(subject/'rtl')+'/')
    original_cfg=re.sub(r'^export SDC_FILE\s*=.*$',lambda _: 'export SDC_FILE = '+str(subject/'constraints/constraint.sdc'),original_cfg,flags=re.M)
    parse=lambda text:dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$',text,re.M))
    if parse(original_cfg)!=parse((subject/'constraints/config.mk').read_text()):raise ValueError('original subject knobs differ from baseline')
    if strategy in hooks:
        knob,name,_=hooks[strategy]
        original_cfg=campaign.set_mk(original_cfg.replace(str(subject),str(project)),knob,str(REPO/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/name))
        if parse(original_cfg)!=parse((project/'constraints/config.mk').read_text()):raise ValueError('recipe changed additional baseline knobs')
    else:
        from run_area_recipe_ab import verify_area_config
        tool=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
        if sha(tool)!='6454121a552fc8625fa27f474dfc699f2d82a10a8959238168f2c2249988fd48':raise ValueError('geometry tool changed')
        os.environ['R2G_LAYOUT_OPENROAD']=str(tool);verify_area_config(arm)
    manifests=list(trials.glob('execution_manifest*.json'))
    if len(manifests)!=1:raise ValueError('ambiguous physical execution manifest')
    physical=json.loads(manifests[0].read_text())
    if physical['plan_sha256']!=sha(trials/'plan.json'):raise ValueError('execution plan changed')
    recovery_path=None
    if execution.get('recovery'):
        recovery_path=Path(execution['recovery'])
        if recovery_path.resolve()!=project.resolve()/'recovery.json' or sha(recovery_path)!=execution.get('recovery_sha256'):
            raise ValueError('recovery evidence changed')
        recovered=json.loads(recovery_path.read_text())
        if (recovered.get('flow_returncode')!=execution['flow_returncode']
            or recovered.get('original_manifest_sha256')!=sha(manifests[0])
            or recovered.get('inputs_verified') is not True
            or recovered.get('elapsed_downtime_counts_against_original_budget') is not True):
            raise ValueError('recovery provenance is incomplete')
    if validate_only:
        return dict(design=design,validation_only=True,strict_dual_clean=True,
                    original_sources_assets_and_SDC_verified=True,exact_physical_recipe_verified=True,
                    baseline_hashes=baseline_hashes,execution_plan_sha256=physical['plan_sha256'],
                    already_counted=bool(row['physical_verdict_verified']),recovery_verified=bool(recovery_path),
                    transfer_output_created=False,remote_import_performed=False,campaign_count_changed=False)
    out=auxiliary/'normal_export'/design;out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(run,out/'accepted_run');(out/'evidence').mkdir();(out/'work').mkdir()
    for name in ('config.mk','constraint.sdc','flow.log'):
        shutil.copy2(work/name,out/'work'/name)
    shutil.copytree(work/'subject/rtl',out/'work/rtl')
    if strategy in hooks:shutil.copy2(work/hooks[strategy][1],out/'work'/hooks[strategy][1])
    for name in ('plan.json','selection.json','application.json'):shutil.copy2(trials/name,out/'evidence'/name)
    shutil.copy2(project/'execution.json',out/'evidence/execution.json')
    shutil.copy2(project/'source_provenance.json',out/'evidence/source_provenance.json')
    shutil.copy2(manifests[0],out/'evidence/original_execution_manifest.json')
    if recovery_path:
        shutil.copy2(recovery_path,out/'evidence/recovery.json')
        for name in ('flow_before_recovery.log',):
            if (project/name).exists():shutil.copy2(project/name,out/'evidence'/name)
        if (work/'flow.recovery.log').exists():shutil.copy2(work/'flow.recovery.log',out/'evidence/flow.recovery.log')
    shutil.copy2(auxiliary/'execution_image_validation.json',out/'evidence/execution_image_validation.json')
    source_binding={'package/sources/'+rel:digest for rel,digest in provenance['source_files'].items()}
    save(out/'evidence/physical_run_manifest.json',dict(physical,source_bundle_sha256=source_binding,
         source_binding_note='Original export hashes rechecked against project and executed copies at packaging; importer must independently compare with frozen 216 source files.'))
    save(out/'work/source_audit.json',dict(status='original_export_and_executed_copy_hashes_verified',files=source_binding,
         sdc_sha256=provenance['sdc_sha256'],scope='No RTL edits; physical recipe only. Not an unbounded functional proof.'))
    remote=Path('/proj/workarea/yangao/sky130_repair_20261026/auxiliary208')/design
    receipt=dict(design=design,category=row['category'],status='dual_clean',policy_revision=1,normal_recipe_transfer_revision=1,
         baseline=str(Path(snapshot['baseline'])/'runs'/(design+'__sky130hd__p5')),baseline_hashes=baseline_hashes,
         source_host='208',authoritative_reconciliation_pending=True,evidence_root=str(remote/'evidence'),
         execution_mode=plan['mode'],recipe_key=plan['key'],
         attempts=[dict(strategy=strategy,status='completed',run=str(remote/'accepted_run'),work=str(remote/'work'),
             source_host='208',source_physical_run=str(run),source_audit=str(remote/'work/source_audit.json'),
             semantic_review='original_RTL_assets_and_SDC_unchanged_exact_physical_recipe',lvs='clean')])
    save(out/'repair.json',receipt);save(out/'accepted_run/strict_signoff_audit.json',inspect_run(out/'accepted_run'))
    save(out/'transfer_manifest.json',{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()})
    archive=auxiliary/(design+'.normal_recipe.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:tar.add(out,arcname=design)
    return dict(design=design,archive=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),remote_import_performed=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true',help='Audit all packaging gates without creating a transfer or changing campaign yield')
    args=parser.parse_args()
    print(json.dumps(prepare(args.trials,validate_only=args.validate_only)))
