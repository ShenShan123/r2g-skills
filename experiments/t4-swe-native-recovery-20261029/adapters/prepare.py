"""Offline preparation and launch-readiness checks; never sends model requests."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT/'sources/SWE-agent'
COMMIT = '0f3acafacabc0def8cc76b4e48acb4b6cf302cb9'
PRIOR = Path('/data1/zhangdy/tehm-experiments/t4-cross-host-online-20261026')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_upstream():
    commit = subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    changed = subprocess.check_output(['git','-C',str(UPSTREAM),'status','--porcelain'],text=True).strip()
    if commit != COMMIT or changed:
        raise ValueError('upstream source differs from the pinned release')
    return commit


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)+'\n')


def public_tasks(data):
    fields = {'instance_id', 'repo', 'base_commit', 'problem_statement'}
    rows = data['public_tasks']
    if not rows or any(set(row) != fields or any(not isinstance(v,str) or not v for v in row.values()) for row in rows):
        raise ValueError('public tasks must contain exactly issue/repo/base commit/id; no hidden evaluator fields')
    if len({row['instance_id'] for row in rows}) != len(rows):
        raise ValueError('duplicate task id')
    return rows


def readiness():
    docker = shutil.which('docker')
    daemon = False
    if docker:
        try:
            daemon = subprocess.run([docker,'info','--format','{{.ServerVersion}}'],
                capture_output=True,timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    qualified = ROOT/'reports/official-environment-qualification.json'
    return {'docker_binary_available': bool(docker), 'docker_daemon_available': daemon,
            'official_task_qualification_present': qualified.exists(),
            'paid_launch_authorized': False, 'formal_launch_allowed': False,
            'reason': 'Preparation only. Official instance environments, evaluator-owned tests and paid launch registration are not qualified. Docker alone never grants admission.'}


def prepare():
    target = ROOT/'inputs/preparation-manifest.json'
    if target.exists():
        raise ValueError('do not overwrite an existing preparation freeze')
    commit = verify_upstream()
    original = json.loads((PRIOR/'inputs/swe-verified-first2.json').read_text())
    tasks = public_tasks(original)
    test_report = ROOT/'reports/offline-tests.xml'
    cases = list(ET.parse(test_report).getroot().iter('testcase'))
    if not cases or any(case.find('failure') is not None or case.find('error') is not None for case in cases):
        raise ValueError('offline adapter tests have not passed')
    save(ROOT/'inputs/public-tasks.json',{'source':original['source'],'public_tasks':tasks,
        'selection':'same two previously registered software tasks; no outcome-based reselection',
        'private_answers_excluded':True})
    config = yaml.safe_load((UPSTREAM/'config/default.yaml').read_text())['agent']
    config['model'] = {'name':'deepseek-flash','per_instance_cost_limit':0,'total_cost_limit':0,
                       'retry':{'retries':1},'fallbacks':[]}
    # Two byte-identical host configurations; the memory switch lives outside
    # native tools/templates and never removes the host's own capabilities.
    for arm in ('native','tehm'):
        (ROOT/'inputs'/f'{arm}.yaml').write_text(yaml.safe_dump({'agent':config},sort_keys=False))
        for part in ('memory','artifacts','episodes','ledgers'):
            (ROOT/'hosts/swe-agent'/arm/part).mkdir(parents=True,exist_ok=True)
    files = {p for base in (ROOT/'adapters',ROOT/'code/memory') for p in base.rglob('*.py')
             if '__pycache__' not in p.parts}
    files.update(ROOT/'inputs'/name for name in ('public-tasks.json','native.yaml','tehm.yaml',
                                                'requirements.lock.txt','replacement-plan.json'))
    files.add(test_report)
    core = {str(p.relative_to(ROOT/'code/memory')):sha(p) for p in (ROOT/'code/memory').rglob('*.py')
            if '__pycache__' not in p.parts}
    assert all(sha(PRIOR/'code/memory'/name)==h for name,h in core.items())
    manifest = {'schema':'table4-swe-agent-preparation-v1','prepared_at':time.time(),
        'status':'OFFLINE_PREPARED_NOT_LAUNCH_AUTHORIZED','paper_data':False,
        'host':'SWE-agent','arms':['native','tehm'],'release':'v1.1.0','upstream_commit':commit,
        'publication':'NeurIPS 2024 main conference; v1.1.0 is a later public engineering version, not a paper-result reproduction',
        'replaced_host':'mini-SWE-agent','old_experiment_untouched':str(PRIOR),
        'tasks':[t['instance_id'] for t in tasks], 'online_write_back':True,
        'initial_history':'empty separate private stores; no EDA-history transfer claim',
        'cumulative_cost_cap':None, 'network_retries':0,
        'native_format_requeries':'retained and individually metered',
        'per_task_stopping_rule':'native rule and safety envelope must be registered before a paid launch; no Table2 three-call cap imported',
        'software_execution':'SWE-agent DefaultAgent + original ToolHandler + SWE-ReX; no R2G repair scripts',
        'direct_reuse':'promoted, state-bound, supported existing-file patch plus independent verifier; otherwise native fallback',
        'oracle':'official SWE-bench evaluation only for resolved; shell success is UNKNOWN',
        'runtime_versions':{name:importlib.metadata.version(name) for name in ('sweagent','swe-rex','litellm','pydantic')},
        'offline_tests':{'passed':sum(case.find('skipped') is None for case in cases),
                         'skipped':sum(case.find('skipped') is not None for case in cases),
                         'report':'reports/offline-tests.xml'},
        'core_files':core,'files':{str(p.relative_to(ROOT)):sha(p) for p in sorted(files)},
        'readiness':readiness()}
    save(target,manifest)
    return manifest


def check():
    manifest = json.loads((ROOT/'inputs/preparation-manifest.json').read_text())
    verify_upstream()
    for name,h in manifest['files'].items():
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT) or sha(p)!=h:
            raise ValueError('prepared runtime changed: '+name)
    assert (ROOT/'inputs/native.yaml').read_bytes()==(ROOT/'inputs/tehm.yaml').read_bytes()
    for arm in ('native','tehm'):
        for part in ('memory','artifacts','episodes','ledgers'):
            p=ROOT/'hosts/swe-agent'/arm/part
            if p.is_symlink() or not p.resolve().is_relative_to(ROOT):
                raise ValueError('shared/escaping writable arm state')
    return {'runtime_hashes_pass':True, 'upstream_commit':manifest['upstream_commit'],
            'paper_data':False,'status':manifest['status'],**readiness()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','check'])
    args=parser.parse_args()
    result=prepare() if args.command=='prepare' else check()
    print(json.dumps({k:v for k,v in result.items() if k not in {'core_files','files'}},ensure_ascii=False,indent=2))
