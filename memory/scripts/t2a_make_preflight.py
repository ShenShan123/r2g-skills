#!/usr/bin/env python3
"""Real ORFS Make-only DEV config query; no EDA target, provider, or paper result."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

import t2a_envelope as E
import t2a_qualify as Q


def check(root,source):
    root.mkdir(parents=True,exist_ok=False)
    project=root/'project';shutil.copytree(source/'constraints',project/'constraints')
    cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(source)+'/',str(project)+'/'))
    initial=E.effective_config(cfg.read_text())
    Q.save(root/'manifest.json',{'task':'DEV/config-only','initial_config':initial,'immutable':{'files':{
         'constraints/constraint.sdc':'sha256:'+Q.digest(project/'constraints/constraint.sdc')}},'allowlist':{}})
    out=root/'query';out.mkdir()
    flow=Q.REPO/'r2g-skills/signoff-loop/scripts/flow'
    pin=Q.EVAL/'toolchain/frozen-r2g-cc6d103/r2g-skills/signoff-loop/references/env.local.sh'
    files={str(p):Q.digest(p) for p in [cfg,project/'constraints/constraint.sdc',root/'manifest.json',
         flow/'t2a_effective_config.py',flow/'t2a_effective_config.mk',flow/'_env.sh',pin]}
    Q.save(root/'registration.json',{'files':files,'started':time.time(),'target':'r2g-effective-config',
           'no_eda':True,'no_llm':True,'source_dev_project':str(source),'no_retry':True})
    workspace=project/'.orfs-work';workspace.mkdir()
    H=Q.module('t2a_make_harness',Q.REPO/'memory/scripts/r2g_memory_eval.py')
    H.SKILL=Q.REPO/'r2g-skills/signoff-loop';H._EPISODE['ledger']=project
    env=dict(os.environ)
    env.update(R2G_ENV_FILE=str(pin),R2G_TASK_MANIFEST=str(root/'manifest.json'),WORK_HOME=str(workspace),
               NUM_CORES='8',PYTHONDONTWRITEBYTECODE='1',R2G_BUDGET_COMPONENT='dev_make_preflight')
    for key in ('R2G_BUDGET_FILE','R2G_LEDGER_PATH','R2G_PHASE_LEDGER','R2G_PHASE_BUDGET_FILE'):
        env.pop(key,None);os.environ.pop(key,None)
    # _env.sh sets only the frozen EDA environment. No credentials file is sourced.
    shell='source "$1"; cd "$2"; exec "$3" "$4" --project "$5" --backend "$6" --flow "$FLOW_DIR" --design-config "$7" --variant dev-config-only'
    rc=H.run(['bash','-c',shell,'_',flow/'_env.sh',workspace,sys.executable,flow/'t2a_effective_config.py',
              project,out,cfg],env,root/'query.log',120)
    receipt=json.loads((out/'effective-config.json').read_text()) if (out/'effective-config.json').exists() else {}
    Q.save(root/'result.json',{'rc':rc,'status':receipt.get('status','UNKNOWN'),'errors':receipt.get('errors'),
          'receipt_sha256':Q.digest(out/'effective-config.json') if receipt else None,
          'llm_requests':0,'eda_flows':0,'real_eda_hooks_validated':False})
    print(json.dumps({'status':receipt.get('status','UNKNOWN'),'rc':rc,'errors':receipt.get('errors'),
                      'real_eda_hooks_validated':False}))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--source',type=Path,required=True);a=ap.parse_args();check(a.root.resolve(),a.source.resolve())
