"""No-model task provisioning and differential qualification for local SWE20.

Selected tasks are never replaced after an environment failure. Baseline and
reference-fix qualification are evaluator-only and never populate TEHM history.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from select_tasks import ROOT, save
from local_sandbox import sandbox_args, isolated_command


def record(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    scratch=path.with_suffix(path.suffix+'.tmp')
    scratch.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    scratch.replace(path)


def execute(args,log,*,timeout=3600,cwd=None,input=None):
    log.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with log.open('x') as out:
        result=subprocess.run(args,stdout=out,stderr=subprocess.STDOUT,cwd=cwd,
                              text=True,input=input,timeout=timeout)
    record(log.with_suffix('.cost.json'),{'wall_s':time.monotonic()-started,
        'returncode':result.returncode,'kind':'environment_preparation','model_requests':0})
    if result.returncode:
        raise RuntimeError('command failed; see '+str(log))


def clone_base(task,path):
    path.mkdir(parents=True,exist_ok=False)
    commands=[['git','init','-q',str(path)],
              ['git','-C',str(path),'remote','add','origin','https://github.com/'+task['repo']+'.git'],
              ['git','-C',str(path),'fetch','--depth','1','origin',task['base_commit']],
              ['git','-C',str(path),'checkout','--detach','FETCH_HEAD'],
              ['git','-C',str(path),'remote','remove','origin']]
    for i,cmd in enumerate(commands):
        execute(cmd,path.parent/f'clone-{i}.log')
    head=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    if head!=task['base_commit']:
        raise ValueError('checkout identity mismatch')


def eval_worktree(task,repo,home,env,out,patch):
    from official import get_test_directives, spec_for, parse_and_grade
    out.mkdir(parents=True,exist_ok=False)
    target=out/'repo'
    # Private fresh evaluator checkout: no source, test or environment modified
    # by the Agent is reused except its submitted patch bytes.
    shutil.copytree(repo,target,symlinks=True)
    for label,text in [('candidate',patch),('tests',task['test_patch'])]:
        if text.strip():
            execute(['git','-C',str(target),'apply','--check','-'],out/(label+'-check.log'),input=text)
            execute(['git','-C',str(target),'apply','-'],out/(label+'-apply.log'),input=text)
    eval_home=out/'home';eval_home.mkdir()
    spec=spec_for(task)
    test_cmd=' '.join([spec['test_cmd'],*[shlex.quote(x) for x in get_test_directives(task)]])
    command='cd /testbed; '+test_cmd
    started=time.monotonic()
    try:
        run=isolated_command(target,eval_home,command,env=env,timeout=3600)
        text=run.stdout+'\n'+run.stderr
        (out/'test-output.txt').write_text(text)
        grade=parse_and_grade(task,text)
        if grade['status']=='PASS' and run.returncode!=0:
            grade.update(status='UNKNOWN',resolved=None,
                         reason='test process failed despite parsed target passes; no false acceptance')
        grade.update(returncode=run.returncode,wall_s=time.monotonic()-started,
                     patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                     instance_id=task['instance_id'],test_command=test_cmd,
                     test_output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                     evaluator='pinned v4.1.0 parser/grader, local reproduction, NOT official Docker execution')
    except subprocess.TimeoutExpired:
        grade={'status':'UNKNOWN','reason':'evaluation safety timeout','wall_s':time.monotonic()-started}
    record(out/'grade.json',grade)
    return grade


def prepare_one(task):
    from official import spec_for, make_env_script_list_py, MAP_REPO_TO_INSTALL
    work=ROOT/'qualification'/task['instance_id']
    if work.exists():
        receipt=work/'qualification.json'
        if receipt.exists() and json.loads(receipt.read_text()).get('status')=='PASS':
            return json.loads(receipt.read_text())
        raise RuntimeError('previous qualification incomplete or failed; no implicit retry: '+str(work))
    work.mkdir(parents=True)
    record(ROOT/'reports/provision-state.json',{'status':'QUALIFYING','task':task['instance_id'],
                                              'updated':time.time(),'paid_requests':0})
    repo=work/'base'
    try:
        clone_base(task,repo)
        home=work/'setup-home';home.mkdir()
        # A private conda prefix, created inside the sandbox. System conda is
        # read-only. No sudo, apt mutation, external user files or ToS acceptance.
        (home/'.condarc').write_text('channels:\n  - conda-forge\nchannel_priority: flexible\nalways_yes: true\n')
        spec=spec_for(task)
        upstream=make_env_script_list_py(task,spec,'testbed')
        (work/'upstream-setup.json').write_text(json.dumps(upstream,indent=2)+'\n')
        setup=local_dependency_recipe(spec,upstream)
        setup+=['cd /testbed']
        if task['repo'] in MAP_REPO_TO_INSTALL:
            setup.append(MAP_REPO_TO_INSTALL[task['repo']])
        setup+=spec.get('pre_install',[])
        if 'install' in spec:
            setup.append(spec['install'])
        # Native file tools are installed in the environment in advance, not
        # removed or replaced by shell-only helpers for difficult environments.
        setup += ["python -m pip install 'tree-sitter==0.21.3' tree-sitter-languages"]
        script='set -e\n'+'\n'.join(setup)+'\n'
        (work/'setup.sh').write_text(script)
        execute(sandbox_args(repo,home)+['--','/bin/bash','-c',script],work/'setup.log',timeout=7200)
        env=home/'envs/testbed'
        if not ((env/'bin/python').exists() or (env/'bin/python').is_symlink()):
            raise ValueError('task environment missing')
        execute(['git','-C',str(repo),'add','-u'],work/'setup-stage.log')
        execute(['git','-C',str(repo),'-c','user.name=local-environment-setup',
                 '-c','user.email=setup@example.invalid','commit','--allow-empty','-qm',
                 'Record shared upstream environment preparation, not an agent repair'],
                work/'setup-commit.log')
        # Editable installs and package data refer to /testbed; this is identical
        # for qualification, the two Agent arms and final evaluation.
        before=eval_worktree(task,repo,home,env,work/'baseline-eval','')
        after=eval_worktree(task,repo,home,env,work/'reference-eval',task['patch'])
        if before['status']!='FAIL' or after['status']!='PASS':
            raise RuntimeError('qualification requires real failing baseline and passing reference, no missing tests')
        receipt={'status':'PASS','instance_id':task['instance_id'],'base_commit':task['base_commit'],
                 'baseline_status':before['status'],'reference_status':after['status'],
                 'environment_prefix':str(env),'repo':str(repo),'setup_sha256':hashlib.sha256(script.encode()).hexdigest(),
                 'paper_data':False,'model_requests':0,'runtime':'local reproduction, no Docker',
                 'baseline_grade':str(work/'baseline-eval/grade.json'),
                 'reference_grade':str(work/'reference-eval/grade.json')}
        record(work/'qualification.json',receipt)
        return receipt
    except Exception as exc:
        record(work/'qualification.json',{'status':'BLOCKED','instance_id':task['instance_id'],
            'reason':str(exc),'exception':type(exc).__name__,'paper_data':False,'model_requests':0})
        raise


def local_dependency_recipe(spec,upstream):
    """Preserve upstream Python minor and pip pins; do not accept Anaconda ToS.

    Container-specific conda runtime build strings are not portable. Use system
    Python of the same minor where present, otherwise a conda-forge-only Python.
    Explicitly disclose that this is a local reproduction, not image identity.
    """
    import yaml
    version=spec['python']
    binary=Path('/usr/bin/python'+version)
    managed=sorted((ROOT/'python-runtimes').glob('cpython-'+version+'.*-linux-x86_64-gnu'))
    command=['export CONDA_ENVS_PATH=/root/envs','export CONDA_PKGS_DIRS=/root/conda-pkgs',
             'export CONDARC=/root/.condarc','mkdir -p /root/envs']
    if managed:
        binary=Path('/managed-python')/managed[-1].name/'bin'/('python'+version)
    if managed or binary.exists():
        site='/root/envs/testbed/lib/python'+version+'/site-packages'
        command += [f'{binary} -m venv --copies --without-pip /root/envs/testbed',
                    f'unzip -qo /bootstrap/setuptools-75.1.0-py3-none-any.whl -d {site}',
                    f'unzip -qo /bootstrap/pip-24.2-py3-none-any.whl -d {site}',
                    f'unzip -qo /bootstrap/wheel-0.44.0-py3-none-any.whl -d {site}',
                    '/root/envs/testbed/bin/python -m pip install --no-index --find-links=/bootstrap '
                    '--force-reinstall pip==24.2 setuptools==75.1.0 wheel==0.44.0']
    else:
        command += [f'/opt/anaconda3/bin/conda create --override-channels -c conda-forge '
                    f'-p /root/envs/testbed python={version} pip setuptools wheel -y']
    command += ['export PATH=/root/envs/testbed/bin:/usr/bin:/bin']
    # Official cached envs carry exact pip dependencies. Read only that block;
    # OS/runtime conda builds are archived, never presented as reproduced bytes.
    env_text=None
    for item in upstream:
        if 'EOF_59812759871' in item and 'environment.yml' in item:
            env_text=item.split('\n',1)[1].rsplit('\nEOF_59812759871',1)[0]
            break
    if env_text:
        data=yaml.safe_load(env_text)
        pip_deps=[p for dep in data.get('dependencies',[]) if isinstance(dep,dict)
                  for p in dep.get('pip',[])]
        if not pip_deps:
            raise ValueError('cached environment has no portable pip dependency list')
        command += ["python -m pip install "+' '.join(shlex.quote(p) for p in pip_deps)]
    else:
        # No cached file: preserve upstream dependency commands but refuse a
        # silent conversion of conda-only package sets.
        raise ValueError('uncached dependency recipe requires explicit local adaptation; no task replacement')
    return command


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--first',type=int,default=20)
    parser.add_argument('--resume-preparation',action='store_true')
    args=parser.parse_args()
    pin=ROOT/'inputs/dataset-pin-verification.json'
    if not pin.exists() or json.loads(pin.read_text())['status']!='PASS':
        raise ValueError('pinned dataset verification missing')
    tasks=json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
    count=0
    try:
        for task in tasks[:args.first]:
            work=ROOT/'qualification'/task['instance_id']
            if args.resume_preparation and work.exists():
                old=work/'qualification.json'
                if old.exists() and json.loads(old.read_text()).get('status')!='PASS':
                    if any((ROOT/'hosts').glob('*/**/episodes/'+task['instance_id'])):
                        raise ValueError('cannot reset qualification of a launched task')
                    suffix=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
                    archive=work.with_name(work.name+'--preparation-failed-'+suffix)
                    work.rename(archive)
            prepare_one(task)
            count+=1
    except Exception as exc:
        record(ROOT/'reports/provision-state.json',{'status':'BLOCKED','qualified':count,
            'reason':str(exc),'paid_requests':0,'updated':time.time()})
        raise
    record(ROOT/'reports/provision-state.json',{'status':'QUALIFICATION_COMPLETE',
        'qualified':count,'registered':20,'paid_requests':0,'updated':time.time()})


if __name__=='__main__':
    main()
