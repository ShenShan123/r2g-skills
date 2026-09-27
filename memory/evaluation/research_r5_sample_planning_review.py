"""Package and cold-recompute R5 sample-planning evidence, with no model/EDA."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

MAIN=Path('/data1/zhangdy/Typed-Executable-Hardware-Memory')
PILOT=Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(root):return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
def write(path,obj):
    with path.open('x') as h:h.write(json.dumps(obj,indent=2,sort_keys=True)+'\n')
def read(path):return json.loads(path.read_text())

def evaluate(bundle,output):
    from tehm.evaluation.research_r5_sample_planning import planning_scenarios
    lock=read(bundle/'lock.json')
    for name,digest in lock['files'].items():assert sha(bundle/name)==digest,name
    f1=bundle/'inputs/f1'
    for name,digest in read(f1/'receipt.json')['files'].items():assert sha(f1/name)==digest,name
    manifest=read(f1/'manifest.json');rows=read(f1/'normalized-results.json');summary=read(f1/'summary.json')
    tasks=manifest['tasks'];views=manifest['views']
    expected={(t['task_id'],v) for t in tasks for v in views}
    actual=[(r['task_id'],r['view']) for r in rows]
    assert set(actual)==expected and len(actual)==len(set(actual))
    assert len(tasks)==summary['registered_tasks']==1
    assert all(t['role']=='FINAL_TEST' for t in tasks)
    assert summary['source_independence_granted'] is False
    assert all(r['shared_contract_digest']==manifest['shared_contract_digest'] for r in rows)
    c7=read(bundle/'inputs/c7-audit.json');b1=read(bundle/'inputs/b1-result.json')
    assert c7['role']=='KNOWN_DEV_CALIBRATION_NOT_TRANSFER_NOT_FINAL'
    assert c7['method_tasks']==c7['final_tasks']==0 and c7['actual_http_attempts']==3
    assert b1['screened_candidate_scopes']==len(b1['rows'])==2
    assert b1['admitted_independent_unseen_sources']==0
    assert {r['observed_leaf_role'] for r in b1['rows']}=={'TRAIN','DEV'}
    output.mkdir(exist_ok=False)
    write(output/'scenarios.json',planning_scenarios())
    write(output/'evidence-grain.json',{'schema':'r5-sample-plan-evidence-grain-v1',
        'f1_registered_final_tasks':len(tasks),'f1_view_rows':len(rows),
        'f1_source_independence_granted':False,'f1_inferential_scope':manifest['inferential_scope'],
        'c7_dev_generation_calls':3,'c7_final_tasks':0,
        'b1_excluded_wrapper_scopes':len(b1['rows']),
        'current_population_inference_admitted':False,'original_readout_hashes_verified':True,
        'new_rtl_raw_audits_or_simulations':0,'new_model_calls':0,
        'method_or_memory_changes':False,'source_files':lock['files']})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.bundle:
        evaluate(args.bundle,args.output);return
    base=PILOT/'paper/sample-planning-p1';base.mkdir(mode=0o700)
    bundle=base/'bundle';bundle.mkdir()
    inputs=bundle/'inputs';inputs.mkdir()
    f1=PILOT/'final/rds-gaxi-gen5-f1/readout-r1'
    shutil.copytree(f1,inputs/'f1')
    sources={'design.md':MAIN/'memory/docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md',
        'paper-protocol.json':MAIN/'memory/evaluation/research_r5_paper_protocol_v1.json',
        'paper-protocol.md':MAIN/'memory/evaluation/research_r5_paper_protocol_20260925.md',
        'planning-note.md':MAIN/'memory/evaluation/research_r5_sample_planning_20260926.md',
        'b1-result.json':PILOT/'paper/final-scope-screen-b1/evidence-r1/result.json',
        'c7-audit.json':PILOT/'agent/s2-provider-c7/raw-audit.json'}
    seal=read(PILOT/'agent/s2-provider-c7/seal.json')
    assert sha(sources['c7-audit.json'])==seal['files']['raw-audit.json']
    for name,source in sources.items():shutil.copyfile(source,inputs/name)
    code=bundle/'code/tehm/evaluation';code.mkdir(parents=True)
    for stem in ('sample_planning','sample_planning_checks'):
        name=f'research_r5_{stem}.py';shutil.copyfile(MAIN/'memory/tehm/evaluation'/name,code/name)
    shutil.copyfile(Path(__file__).resolve(),bundle/'review.py')
    write(bundle/'lock.json',{'schema':'r5-sample-planning-lock-v1','files':inventory(bundle)})
    evaluate(bundle,base/'results')
    backup=Path(tempfile.mkdtemp(prefix='tehm-r5-sample-plan-'));shutil.copytree(base,backup/'package')
    original=inventory(base);assert inventory(backup/'package')==original
    archive=backup/'package.tar.gz'
    with tarfile.open(archive,'x:gz') as tar:tar.add(backup/'package',arcname='package')
    restored=backup/'restored';restored.mkdir()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert not member.issym() and not member.islnk() and '..' not in Path(member.name).parts
            assert not Path(member.name).is_absolute()
        tar.extractall(restored)
    restored=restored/'package';assert inventory(restored)==original
    recovered=backup/'recovery';recovered.mkdir()
    common=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv',
        '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
        '--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
        '--ro-bind',str(restored/'bundle'),'/bundle','--bind',str(recovered),'/out',
        '--setenv','PYTHONDONTWRITEBYTECODE','1','--setenv','PYTHONPATH','/bundle/code',
        '--chdir','/','/usr/bin/python3']
    commands={'checks':['-m','tehm.evaluation.research_r5_sample_planning_checks'],
        'replay':['/bundle/review.py','--bundle','/bundle','--output','/out/results']}
    for name,tail in commands.items():
        result=subprocess.run(common+tail,capture_output=True,timeout=60)
        with (recovered/f'{name}.stdout.log').open('xb') as h:h.write(result.stdout)
        with (recovered/f'{name}.stderr.log').open('xb') as h:h.write(result.stderr)
        write(recovered/f'{name}.process.json',{'returncode':result.returncode,'command':common+tail})
        assert result.returncode==0,name
    assert inventory(recovered/'results')==inventory(base/'results')
    shutil.copytree(recovered,base/'recovery');assert inventory(base/'recovery')==inventory(recovered)
    receipt={'schema':'r5-sample-planning-recovery-v1','backup_root':str(backup),
        'archive_sha256':sha(archive),'original_files':original,'recovery_files':inventory(recovered),
        'recomputed_outputs':inventory(base/'results'),'provider_calls':0,'simulator_calls':0,
        'checks_passed':6,'second_copy_network_disabled':True,'offsite_backup':False}
    write(base/'receipt.json',receipt);shutil.copyfile(base/'receipt.json',backup/'receipt.json')
    print(json.dumps({'receipt_sha256':sha(base/'receipt.json'),'backup':str(backup),
        'archive_sha256':sha(archive),'recomputed_outputs':receipt['recomputed_outputs']}))

if __name__=='__main__':main()
