#!/usr/bin/env python3
"""Run isolated candidates with the exact 216 image and checkers.

At most two eight-core workers on 208, with case locks and immutable shared
helpers. No writes or process control on 216. Results remain
auxiliary until reconciled against the authoritative baseline/campaign.
"""
import argparse
import dataclasses
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'agent')]
import run_campaign as campaign
from aux208_container_adapter import IMAGE, SOURCE_IMAGE


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def install_immutable(source,target):
    """Prepare shared helpers atomically; never truncate another slot's file."""
    raw=source.read_bytes();target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('shared helper differs; drain slots before upgrade: '+str(target))
        return
    fd,name=tempfile.mkstemp(prefix='.'+target.name+'.',dir=target.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw)
        try:os.link(temp,target)
        except FileExistsError:
            if target.read_bytes()!=raw:raise ValueError('concurrent shared helper differs')
    finally:temp.unlink(missing_ok=True)


def verify_completed_input_retry(case,previous,old_manifest,root):
    """One diagnosed export omission; never reset a physical repair failure."""
    if (not case['design'].startswith('C03125__') or previous.get('status')!='not_clean'
            or not previous.get('attempts')):
        raise ValueError('not the reviewed incomplete-input failure')
    rel='package/sources/R01743__fpgawars__apio-examples/examples/icezum/marcha-imperial/imperial.list'
    digest='822cadb20ce0398dda2385805311cf1128f027beffd32428b7b162a9cc6f8573'
    if rel in old_manifest['source_bundle_sha256']:
        raise ValueError('old execution already contained the input; no export-omission retry')
    hashes=json.loads((root/'original_files.json').read_text())
    if hashes.get(rel)!=digest or sha(root/rel)!=digest:
        raise ValueError('original ROM bytes not restored and hash-verified')
    for attempt in previous['attempts']:
        if attempt.get('status')!='fail' or attempt.get('strategy') not in ('pin_pdn_clearance','pin_side_rebalance:right'):
            raise ValueError('unreviewed previous attempt')
        result=json.loads((Path(attempt['run'])/'result.json').read_text())
        if result['status']!='fail' or result['stage']!='1_1_yosys_canonicalize':
            raise ValueError('previous attempt progressed beyond the missing-input stage')
        log=Path(attempt['work'])/'flow.log'
        with log.open('rb') as handle:
            handle.seek(max(0,log.stat().st_size-8192));tail=handle.read().decode(errors='replace')
        if 'Can not open file `imperial.list`' not in tail:
            raise ValueError('missing original ROM signature absent')
    return dict(asset=rel,sha256=digest,reason='restore original data omitted by auxiliary export; physical strategy was not reached')


def restore_exported_aliases(work,name,cfg,driver,*,original,root):
    """Materialize source-host-verified aliases inside the private work dir."""
    aliases=original.get('source_aliases',{})
    if not aliases:return
    # Only the source/include declarations are relevant: repair configurations
    # legitimately append physical strategy settings to the frozen config.
    frozen=(root/'baseline'/original['run_name']/'config.mk').read_text()
    if hashlib.sha256(frozen.encode()).hexdigest()!=original['alias_config_sha256']:raise ValueError('alias baseline changed')
    for key in ('VERILOG_FILES','VERILOG_INCLUDE_DIRS'):
        if campaign.mk(cfg,key)!=campaign.mk(frozen,key):raise ValueError('aliased source declaration changed')
    pending=[]
    for alias,target in aliases.items():
        if not re.fullmatch(r'd\d+',alias):raise ValueError('invalid source alias')
        logical=Path(target)
        if not logical.is_relative_to(campaign.PACKAGE/'sources'):raise ValueError('source alias escapes package')
        physical=root/'package'/logical.relative_to(campaign.PACKAGE)
        if not physical.is_dir() or not physical.resolve().is_relative_to(root/'package'):raise ValueError('source alias target missing')
        pending.append((work/'_src'/alias,target))
    for link,target in pending:
        link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(target)


def run_control(case,out,driver,drc,lvs):
    """Fresh unchanged A arm, explicitly separate from the repaired B arm."""
    baseline=Path(case['run']);target=out/'cases'/case['design'];receipt=target/'repair.json'
    if receipt.exists():return json.loads(receipt.read_text())
    work=target/'attempt_1/work';work.mkdir(parents=True,exist_ok=False)
    initial={n:sha(baseline/n) for n in ('config.mk','constraint.sdc','result.json')}
    for name in ('config.mk','constraint.sdc'):shutil.copy2(baseline/name,work/name)
    campaign.restore_source_aliases(work,case['design'],(work/'config.mk').read_text(),driver)
    record=dict(design=case['design'],baseline=str(baseline),status='preparing',
                arm='unchanged_control',baseline_hashes=initial,attempts=[])
    detail=dict(strategy='unchanged_control',config_delta={},work=str(work),status='flow_running')
    record['attempts'].append(detail);campaign.save(target/'progress.json',record)
    start=time.time()
    rc=campaign.podman(work,'control_'+case['design'].split('__')[0]+'_'+str(os.getpid()),
        'cd "$ORFS_ROOT/flow" && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work',work/'flow.log')
    run=target/'attempt_1/runs'/baseline.name;driver.harvest(work,run)
    result=driver.Result(design=case['design'],platform='sky130hd',period='5')
    result.seconds=round(time.time()-start,1);result.stage=driver.last_stage(work)
    result.status='pass' if rc==0 and (run/'6_final.gds').is_file() else 'fail'
    result.failure_class='' if result.status=='pass' else ('timeout' if rc==124 else driver.classify((work/'flow.log').read_text(errors='replace'),result.stage))
    driver.read_metrics(work,result);campaign.save(run/'result.json',dataclasses.asdict(result))
    detail.update(run=str(run),status=result.status,flow_returncode=rc,seconds=result.seconds)
    if result.status=='pass':
        detail['status']='signoff_running';campaign.save(target/'progress.json',record)
        drc.one_and_record(run,out/'drc_projects',campaign.TIMEOUT,'sky130hd')
        lvs.one_and_record(run,out/'lvs_projects',campaign.TIMEOUT,'sky130hd')
        signed=json.loads((run/'result.json').read_text())
        detail.update(status='completed',drc=signed.get('drc_status'),lvs=signed.get('lvs_status'))
    if result.status=='pass':
        audited=campaign.inspect_run(run);campaign.save(run/'strict_signoff_audit.json',audited)
        detail.update(reported_drc=detail.get('drc'),reported_lvs=detail.get('lvs'),
                      drc=audited['drc'],lvs=audited['lvs'],acceptance_audit=audited)
    record['status']='dual_clean' if result.status=='pass' and audited['dual_clean'] else 'not_clean'
    record['baseline_unchanged']=all(sha(baseline/n)==digest for n,digest in initial.items())
    if not record['baseline_unchanged']:raise RuntimeError('copied baseline was modified')
    if sha(work/'config.mk')!=initial['config.mk'] or sha(work/'constraint.sdc')!=initial['constraint.sdc']:
        raise RuntimeError('control arm differs from original constraints')
    campaign.save(receipt,record);campaign.save(target/'progress.json',record)
    return record


def run_recheck(case,out,driver,drc,lvs):
    """Fresh signoff of an unchanged baseline layout; preserve prior evidence."""
    baseline=Path(case['run']);target=out/'cases'/case['design'];receipt=target/'repair.json'
    if receipt.exists():return json.loads(receipt.read_text())
    initial={n:sha(baseline/n) for n in ('config.mk','constraint.sdc','result.json')}
    physical={n:sha(baseline/n) for n in ('6_final.def','6_final.v','6_final.gds')}
    work=target/'attempt_1/work';work.mkdir(parents=True,exist_ok=False)
    run=target/'attempt_1/runs'/baseline.name;shutil.copytree(baseline,run)
    for name in ('config.mk','constraint.sdc'):shutil.copy2(baseline/name,work/name)
    for name in ('result.json','6_final.gds','6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log'):
        if (run/name).exists():shutil.copy2(run/name,run/(name+'.before_recheck'))
    for name in ('6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log'):
        # Failed fresh checkers must never inherit an older passing report.
        (run/name).unlink(missing_ok=True)
    record=dict(design=case['design'],category=case['category'],baseline=str(baseline),status='preparing',
        policy_revision=campaign.POLICY_REVISION,baseline_hashes=initial,
        original_physical_sha256=physical,physical_reexecution=False,
        acceptance_kind='fresh signoff evidence on unchanged original layout; no RTL/physical repair claimed')
    detail=dict(strategy='signoff_evidence_recheck',config_delta={},work=str(work),run=str(run),
        semantic_review='original RTL, DEF and mapped netlist unchanged',status='signoff_running')
    record['attempts']=[detail];campaign.save(target/'progress.json',record)
    start=time.time();drc.one_and_record(run,out/'drc_projects',campaign.TIMEOUT,'sky130hd')
    # Keep the actual extraction for diagnosis of a remaining topology failure.
    original_rmtree=shutil.rmtree;retained=out/'lvs_projects'/run.name
    def keep_extraction(path,*args,**kwargs):
        if Path(path)==retained:return
        return original_rmtree(path,*args,**kwargs)
    lvs.shutil.rmtree=keep_extraction
    try:lvs.one_and_record(run,out/'lvs_projects',campaign.TIMEOUT,'sky130hd')
    finally:lvs.shutil.rmtree=original_rmtree
    if any(sha(run/n)!=digest for n,digest in physical.items()):raise ValueError('signoff changed physical design/reference')
    if any(sha(run/n)!=initial[n] for n in ('config.mk','constraint.sdc')):raise ValueError('signoff changed original constraints')
    audited=campaign.inspect_run(run);campaign.save(run/'strict_signoff_audit.json',audited)
    signed=json.loads((run/'result.json').read_text())
    detail.update(status='completed',seconds=round(time.time()-start,1),drc=audited['drc'],lvs=audited['lvs'],
                  reported_drc=signed.get('drc_status'),reported_lvs=signed.get('lvs_status'),acceptance_audit=audited)
    record['status']='dual_clean' if audited['dual_clean'] else 'not_clean'
    record['baseline_unchanged']=all(sha(baseline/n)==v for n,v in initial.items())
    if not record['baseline_unchanged']:raise ValueError('baseline modified')
    campaign.save(receipt,record);campaign.save(target/'progress.json',record);return record


def main():
    p=argparse.ArgumentParser();p.add_argument('--case',required=True,help='Exact case ID in the hash-verified prepared bundle')
    p.add_argument('--arm',choices=['control','repair','recheck'],default='repair')
    p.add_argument('--review-slot',action='store_true',help='Independent second 8-core slot, only for unchanged-layout signoff rechecks')
    p.add_argument('--secondary-slot',action='store_true',help='Use the same second 8-core resource slot for an independent prepared repair')
    p.add_argument('--auxiliary-slot',type=int,choices=range(2),help='Explicit bounded 8-core slot (0 or 1)')
    p.add_argument('--retry-verified-input-completion',action='store_true',help='Only the diagnosed C03125 original ROM export omission')
    p.add_argument('--root',type=Path,default=Path('/tmp/sky130_aux208'))
    a=p.parse_args();root=a.root.resolve()
    if not root.is_relative_to(Path('/tmp')):raise ValueError('auxiliary root must be under /tmp')
    if a.review_slot and a.arm!='recheck':p.error('second slot is restricted to unchanged-layout signoff rechecks')
    if a.review_slot and a.secondary_slot:p.error('select only one second-slot mode')
    if a.auxiliary_slot is not None and (a.review_slot or a.secondary_slot):p.error('explicit slot cannot be combined with legacy slot flags')
    slot=a.auxiliary_slot if a.auxiliary_slot is not None else int(a.review_slot or a.secondary_slot)
    lock_names=('worker.lock','review_worker.lock','worker_2.lock','worker_3.lock')
    lock=(root/lock_names[slot]).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not re.fullmatch(r'C\d{5}',a.case):p.error('invalid case ID')
    (root/'case_locks').mkdir(exist_ok=True)
    case_lock=(root/'case_locks'/(a.case+'_'+a.arm+'.lock')).open('a');fcntl.flock(case_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if shutil.disk_usage(root).free<40*1024**3:raise RuntimeError('insufficient free disk for isolated validation')
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    if image['Id']!=IMAGE:raise RuntimeError('wrong physical tool image')
    identity=json.loads((root/'execution_image_validation.json').read_text())
    if (identity['source_image_id']!=SOURCE_IMAGE or identity['execution_image_id']!=IMAGE or
            not identity['rootfs_layers_equal'] or identity['rootfs_layers']!=image['RootFS']['Layers'] or
            identity['runtime_config_differences'] or identity['all_config_field_differences']):
        raise RuntimeError('imported image does not match the frozen source filesystem/configuration')
    hashes=json.loads((root/'original_files.json').read_text())
    for rel,digest in hashes.items():
        if sha(root/rel)!=digest:raise ValueError('copied frozen input changed: '+rel)
    original=json.loads((root/'cases.json').read_text())[a.case]
    if original.get('source_aliases'):
        campaign.restore_source_aliases=lambda work,name,cfg,driver:restore_exported_aliases(
            work,name,cfg,driver,original=original,root=root)
    inv=json.loads((HERE/'baseline_inventory_audited_20261026.json').read_text())
    case=next(c.copy() for c in inv['failures'] if c['design'].startswith(a.case+'__'))
    if case['run']!=original['original_run']:raise ValueError('baseline identity mismatch')
    original_run=case['run'];case['run']=str(root/'baseline'/original['run_name'])
    out=root/({'control':'results_control','repair':'results','recheck':'results_recheck'}[a.arm])
    existing=out/'cases'/case['design']/'repair.json'
    input_recovery=None
    if a.retry_verified_input_completion and (a.arm!='repair' or not existing.is_file()):
        raise ValueError('input completion requires the original failed auxiliary repair receipt')
    if existing.exists():
        previous=json.loads(existing.read_text())
        # A cache hit must not replace the manifest of the actual old physical
        # execution with this invocation's tool/policy/source metadata.
        if a.retry_verified_input_completion:
            old=out/(a.case+'_manifest.json')
            input_recovery=verify_completed_input_retry(case,previous,json.loads(old.read_text()),root)
            archive=existing.with_name('repair_before_original_rom_export_completion.json')
            manifest_archive=old.with_name(a.case+'_manifest_before_original_rom_export_completion.json')
            for source,target in ((existing,archive),(old,manifest_archive)):
                if target.exists() and target.read_bytes()!=source.read_bytes():raise ValueError('input-recovery archive differs')
                if not target.exists():shutil.copy2(source,target)
            input_recovery.update(prior_receipt=str(archive),prior_receipt_sha256=sha(archive),prior_manifest=str(manifest_archive))
            existing.unlink()
        elif a.arm=='repair' and (campaign.reviewed_source_congestion_retry(case,previous)
                               or campaign.reviewed_frontend_placement_retry(case,previous)
                               or campaign.reviewed_source_pdn_retry(case,previous)
                               or campaign.reviewed_i2c_driver_retry(case,previous)
                               or (previous['status']=='blocked' and not previous.get('attempts')
                                   and a.case in campaign.REVIEWED_NEW_CASES
                                   and previous.get('policy_revision',1)<campaign.POLICY_REVISION)):
            old=out/(a.case+'_manifest.json');archive=out/(a.case+'_manifest_before_policy_v'+str(campaign.POLICY_REVISION)+'.json')
            if archive.exists() and archive.read_bytes()!=old.read_bytes():raise ValueError('prior execution manifest archive differs')
            if not archive.exists():shutil.copy2(old,archive)
        else:
            print(json.dumps(dict(design=case['design'],status=previous['status'],cached=True)),flush=True)
            return
    environment=root/'environment'
    expected={
        'miniconda3/envs/eda/bin/magic':'56300dc410b2ddb783612ef38edb6dde1879c4dc21edc6195575d507161e8ed6',
        'miniconda3/envs/eda/bin/netgen':'8349a7edd856773cbbc6be319bcf5cf3477f911d4300f6165735ee449dd0e7a8',
        'miniconda3/envs/eda/share/pdk/sky130A/libs.tech/netgen/sky130A_setup.tcl':'8e2c2ad2be542fad833f031b5635799bdd183a10aed40539fafecdab244d23af',
        'miniconda3/envs/eda/share/pdk/sky130A/libs.tech/magic/sky130A.magicrc':'c44df81f19824b6e01322ca9e2107b6c11d172853d0e12ae72828f813261f113',
    }
    for rel,digest in expected.items():
        if sha(environment/rel)!=digest:raise ValueError('signoff environment mismatch: '+rel)
    scripts=json.loads((root/'expected_checker_scripts.json').read_text())
    for rel,digest in scripts['checker_scripts_sha256'].items():
        if sha(environment/rel)!=digest:raise ValueError('checker script mismatch: '+rel)
    libraries=json.loads((root/'expected_signoff_libraries.json').read_text())
    for rel,digest in libraries.items():
        if sha(environment/'miniconda3/envs/eda'/rel)!=digest:raise ValueError('signoff library mismatch: '+rel)
    tools=root/'tools';bin_dir=tools/'bin';bin_dir.mkdir(exist_ok=True)
    # Source files are mounted at their original logical path in the container,
    # but source preparation runs on this host's verified private copy.
    source_repair=campaign.source_repair
    campaign.source_repair=lambda work,cfg:source_repair(work,cfg,source_package=root/'package')
    inout_repair=campaign.prepare_zube_driver_replication
    campaign.prepare_zube_driver_replication=lambda work,name,cfg:inout_repair(work,name,cfg,source_package=root/'package')
    i2c_repair=campaign.prepare_i2c_hierarchy
    campaign.prepare_i2c_hierarchy=lambda work,name,cfg:i2c_repair(work,name,cfg,source_package=root/'package')
    for asset in (HERE/'upstream_memory_assets').rglob('*'):
        if asset.is_file():install_immutable(asset,tools/'upstream_memory_assets'/asset.relative_to(HERE/'upstream_memory_assets'))
    adapter=tools/'aux208_container_adapter.py';install_immutable(HERE/adapter.name,adapter);adapter.chmod(0o755)
    shim=bin_dir/'podman'
    if not shim.exists():shim.symlink_to('../aux208_container_adapter.py')
    if shim.resolve()!=adapter.resolve():raise ValueError('unexpected container adapter')
    for name in ('spef_name_encoding.tcl','normalize_spef_names.py'):
        install_immutable(HERE/name,tools/name)
    # The campaign checks actual translated/source hashes against these
    # independent-frontend proofs before starting physical implementation.
    # Copy only the current case's support, without replacing shared files.
    support=[]
    if a.case in ('C02511','C03962','C03966','C03980','C03986'):
        support.append(a.case.lower()+'_translation_evidence.json')
    if a.case=='C02108':support.append('c02108_tribuf_evidence.json')
    if a.case=='C03954':support.append('c03954_inactive_include_evidence.json')
    if a.case in ('C03963','C03964','C03965'):support.append('fpu_package_order_evidence.json')
    if a.case=='C03259':support.extend(('c03259_hierarchy_evidence_v27.json','c03259_hierarchical_mapped.v','check_i2c_tristate_topology.tcl','preserve_i2c_tristate_drivers.tcl','preserve_i2c_shared_bus.tcl','connect_i2c_driver_power.tcl','check_i2c_final_power.tcl'))
    if a.case=='C03490':support.extend(('c03490_driver_replication_evidence.json','replicate_zube_inout_drivers.tcl'))
    if case['category']=='drc_violation':
        support.append('pin_pdn_clearance.tcl')
    for name in support:
        install_immutable(HERE/name,tools/name)
    hook=Path('r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl')
    target=tools/hook;target.parent.mkdir(parents=True,exist_ok=True)
    install_immutable(HERE.parents[1]/hook,target)
    if sha(target)!=scripts['campaign_feedthrough_hook_sha256']:raise ValueError('repair hook differs from the 216 campaign')
    token=a.case+'_'+a.arm+'_'+str(os.getpid())
    os.environ.update(R2G_AUX208_ROOT=str(root),R2G_AUX208_TOKEN=token,
                      PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    campaign.HERE=tools
    driver=campaign.module('aux208_driver',tools/'orfs_spec.py')
    drc=campaign.module('aux208_drc',tools/'signoff_pass.py')
    lvs=campaign.module('aux208_lvs',tools/'lvs130.py')
    overlay=root/'checker_strict_verdict'
    overlay_manifest=json.loads((overlay/'overlay_provenance.json').read_text())
    for change in overlay_manifest['differences']:
        if sha(overlay/change['file'])!=change['after']:raise ValueError('checker overlay changed')
        original_helper=environment/'r2g/signoff-loop'/change['file']
        if change['before'] is not None and sha(original_helper)!=change['before']:
            raise ValueError('checker overlay source differs from the frozen helper')
    lvs.R2G=overlay
    out.mkdir(exist_ok=True)
    manifest=dict(host='208',case=case['design'],arm=a.arm,token=token,image_id=image['Id'],
        source_image_id=SOURCE_IMAGE,execution_image_validation=identity,
        checker_overlay=overlay_manifest,
        original_baseline='memlab216:'+original_run,source_bundle_sha256=hashes,
        checker_environment_sha256=expected,policy_revision=campaign.POLICY_REVISION,
        checker_scripts_sha256=scripts['checker_scripts_sha256'],signoff_libraries_sha256=libraries,
        runner_sha256=sha(HERE/'run_campaign.py'),auxiliary_runner_sha256=sha(Path(__file__)),adapter_sha256=sha(adapter),
        repair_hooks_sha256={n:sha(tools/n) for n in ('spef_name_encoding.tcl','normalize_spef_names.py',str(hook))},
        candidate_support_sha256={n:sha(tools/n) for n in support},
        source_preparation_sha256={n:sha(HERE/n) for n in ('source_audit.py','upstream_assets.py')},
        source_frontend_sha256=sha(HERE.parents[1]/'agent/r2g_agent/frontend.py'),
        upstream_asset_sha256={str(p.relative_to(tools/'upstream_memory_assets')):sha(p)
                               for p in (tools/'upstream_memory_assets').rglob('*') if p.is_file()},
        workers=1,cores=8,independent_review_slot=a.review_slot,auxiliary_slot=slot,maximum_auxiliary_slots=2,
        flow_timeout=campaign.TIMEOUT,signoff_timeout=campaign.TIMEOUT,
        acceptance='Fresh original DRC/LVS checkers; auxiliary results not yet merged or counted',
        started_at=time.strftime('%FT%T%z'))
    campaign.save(out/(a.case+'_manifest.json'),manifest)
    result=None
    try:
        runner={'control':run_control,'repair':campaign.run_case,'recheck':run_recheck}[a.arm]
        result=runner(case,out,driver,drc,lvs)
    finally:
        # Only this invocation's labelled containers can be removed, including
        # an orphan left by an outer checker timeout. Other jobs are untouched.
        ids=subprocess.check_output(['docker','ps','-aq','--filter','label=r2g.aux208.run='+token],text=True).split()
        if ids:subprocess.run(['docker','rm','-f',*ids],check=True,capture_output=True)
    result.update(execution_host='208',original_baseline='memlab216:'+original_run,
                  authoritative_reconciliation_pending=True)
    if input_recovery:result['auxiliary_input_recovery']=input_recovery
    campaign.save(out/'cases'/case['design']/'repair.json',result)
    print(json.dumps(dict(design=case['design'],status=result['status'],
                         authoritative_reconciliation_pending=True)),flush=True)


if __name__=='__main__':main()
