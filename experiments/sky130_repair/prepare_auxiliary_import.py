#!/usr/bin/env python3
"""Package one proven auxiliary win; retain superseded checker evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from strict_signoff import inspect_run,final_lvs


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')


def retain_prior_attempts(root,receipt_path,receipt,results,cid,proofs):
    """Keep the failed configuration/evidence behind a successful retry."""
    prior=proofs/'prior_attempts';records=[]
    for source in sorted(receipt_path.parent.glob('repair_before_*.json')):
        if not source.resolve().is_relative_to(root):raise ValueError('prior receipt escapes auxiliary root')
        old=json.loads(source.read_text())
        if old['design']!=receipt['design'] or old['baseline']!=receipt['baseline']:
            raise ValueError('prior receipt belongs to another baseline')
        target=prior/source.stem;target.mkdir(parents=True)
        shutil.copy2(source,target/'receipt.json')
        for index,attempt in enumerate(old.get('attempts',[]),1):
            for group,key,names in (
                ('work','work',('config.mk','constraint.sdc','flow.log','slang.v','frontend.v','frontend.ys','source_audit.json','translation_evidence.json','upstream_assets.json')),
                ('run','run',('result.json','strict_signoff_audit.json','6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log'))):
                if not attempt.get(key):continue
                original=Path(attempt[key])
                if not original.resolve().is_relative_to(root):raise ValueError('prior artifact escapes auxiliary root')
                for name in names:
                    path=original/name
                    if path.is_file():
                        if not path.resolve().is_relative_to(root):raise ValueError('prior evidence symlink escapes auxiliary root')
                        dest=target/('attempt_'+str(index))/group/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        records.append(dict(receipt=str(source),sha256=sha(source),export=str(target.relative_to(proofs))))
    for source in sorted(results.glob(cid+'_manifest_before_*.json')):
        prior.mkdir(exist_ok=True);shutil.copy2(source,prior/source.name)
    save(proofs/'prior_attempt_index.json',records)


def main():
    p=argparse.ArgumentParser();p.add_argument('--case',required=True)
    p.add_argument('--arm',choices=['repair','recheck'],default='repair')
    p.add_argument('--signoff-diagnostic',help='Name of retained complete LVS recheck on the same DEF')
    p.add_argument('--checker-overlay',help='Explicit overlay used by a later retained recheck; otherwise use execution manifest')
    p.add_argument('--proof',type=Path,action='append',default=[])
    a=p.parse_args();root=Path('/tmp/sky130_aux208')
    case=json.loads((root/'cases.json').read_text())[a.case];runname=case['run_name']
    design=runname.removesuffix('__sky130hd__p5')
    results=root/('results' if a.arm=='repair' else 'results_recheck')
    receipt_path=results/'cases'/design/'repair.json';receipt=json.loads(receipt_path.read_text())
    detail=receipt['attempts'][-1];source=Path(detail['run']);original_work=Path(detail['work'])
    if not source.is_relative_to(root) or not original_work.is_relative_to(root):raise ValueError('source outside auxiliary root')
    for name,digest in detail.get('implementation_sha256',{}).items():
        if sha(original_work/name)!=digest:raise ValueError('executed implementation changed: '+name)
    if (original_work/'rtl').exists():
        audit=json.loads((original_work/'source_audit.json').read_text())
        if audit['status']!='source_preservation_checked':raise ValueError('source preservation not established')
        original_root=Path(audit['original_root'])
        for entry in audit['files']:
            path=Path(entry['path']);rel=path.relative_to(original_root) if path.is_absolute() else path
            private=original_work/'rtl'/rel
            if not private.resolve().is_relative_to(original_work/'rtl') or sha(private)!=entry['private_sha256']:
                raise ValueError('executed private source differs from preservation audit')
    out=root/'export'/design;out.mkdir(parents=True,exist_ok=False)
    run=out/'accepted_run';shutil.copytree(source,run)
    proofs=out/'evidence';proofs.mkdir()
    for name,path in [('original_receipt.json',receipt_path),('physical_run_manifest.json',results/(a.case+'_manifest.json')),
                      ('execution_image_validation.json',root/'execution_image_validation.json')]:shutil.copy2(path,proofs/name)
    retain_prior_attempts(root,receipt_path,receipt,results,a.case,proofs)
    if a.checker_overlay:
        shutil.copy2(root/a.checker_overlay/'overlay_provenance.json',proofs/'checker_overlay.json')
    else:
        manifest=json.loads((proofs/'physical_run_manifest.json').read_text())
        save(proofs/'checker_overlay.json',manifest['checker_overlay'])
    control=root/'results_control/cases'/design/'repair.json'
    if control.exists():shutil.copy2(control,proofs/'original_control_receipt.json')
    if a.signoff_diagnostic:
        diagnostic=root/'diagnostics'/a.signoff_diagnostic
        checked=diagnostic/'runs'/runname;checker=diagnostic/'lvs_projects'/runname/'lvs'
        if sha(checked/'6_final.def')!=sha(source/'6_final.def'):raise ValueError('recheck is a different layout')
        if final_lvs((checker/'netgen_lvs.rpt').read_text())!='clean':raise ValueError('recheck did not match at top level')
        for n in ('result.json','netgen_lvs.rpt','netgen_lvs.log'):
            if (run/n).exists():shutil.copy2(run/n,run/(n+'.before_checker_correction'))
        for n in ('netgen_lvs.rpt','netgen_lvs.log'):shutil.copy2(checker/n,run/n)
        signed=json.loads((run/'result.json').read_text());rechecked=json.loads((checked/'result.json').read_text())
        signed.update(lvs_status='clean',lvs_seconds=rechecked.get('lvs_seconds'),
                      lvs_checker_correction='comparison-input or report-path correction only; see exact overlay and evidence; no layout, logic or rule change')
        save(run/'result.json',signed)
        extraction=proofs/'accepted_extraction';extraction.mkdir()
        for n in ('extracted.spice','powered.v','run_netgen_lvs.tcl','netgen_lvs_result.json','identifier_encoding.json',
                  'extracted.spice.before_identifier_encoding','powered.v.before_identifier_encoding'):
            if (checker/n).is_file():shutil.copy2(checker/n,extraction/n)
        detail['lvs_before_checker_correction']=detail.get('lvs')
        receipt['checker_correction']=signed['lvs_checker_correction']
    check=inspect_run(run)
    if not check['dual_clean']:raise ValueError('strict physical acceptance failed')
    work=out/'work';work.mkdir()
    for path in original_work.iterdir():
        if path.is_file() and (path.name in ('config.mk','constraint.sdc','flow.log','slang.v','frontend.v','frontend.log','source_audit.json',
                'upstream_assets.json','translation_evidence.json','source_order_evidence.json','conditional_include_evidence.json','pin_geometry_evidence.json','area_feedthrough_evidence.json','hierarchy_evidence.json','driver_replication_evidence.json','driver_replication_final_audit.json','i2c_hierarchy_evidence.json','i2c_final_audit.json','c03259_hierarchical_mapped.v') or path.suffix in ('.py','.tcl','.ys')):
            shutil.copy2(path,work/path.name)
    if (original_work/'rtl').exists():shutil.copytree(original_work/'rtl',work/'rtl')
    extra=[]
    for i,path in enumerate(a.proof):
        if not path.resolve().is_relative_to(root):raise ValueError('proof outside auxiliary root')
        name=str(i)+'_'+path.name;shutil.copy2(path,proofs/name);extra.append(dict(file=name,source=str(path),sha256=sha(path)))
    save(proofs/'extra_proofs.json',extra)
    remote=Path('/proj/workarea/yangao/sky130_repair_20261026/auxiliary208')/design
    receipt.update(status='dual_clean',baseline=case['original_run'],authoritative_reconciliation_pending=True,
                   source_host='208',evidence_root=str(remote/'evidence'))
    detail.update(run=str(remote/'accepted_run'),work=str(remote/'work'),status='completed',lvs='clean',
                  source_physical_run=str(source),source_host='208')
    if (work/'source_audit.json').exists():detail['source_audit']=str(remote/'work/source_audit.json')
    save(out/'repair.json',receipt);save(run/'strict_signoff_audit.json',check)
    files={str(path.relative_to(out)):sha(path) for path in out.rglob('*') if path.is_file()}
    save(out/'transfer_manifest.json',files)
    archive=root/(design+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:tar.add(out,arcname=design)
    print(json.dumps(dict(design=design,archive=str(archive),bytes=archive.stat().st_size,sha256=sha(archive))))


if __name__=='__main__':main()
