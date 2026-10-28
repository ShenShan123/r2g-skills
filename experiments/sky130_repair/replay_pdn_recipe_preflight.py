#!/usr/bin/env python3
"""Exercise real diagnose/apply on retained failing geometry, without a flow.

Uses a private knowledge-store snapshot. A successful forced B preparation is
not a physical A/B result or a promotion.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    here=Path(__file__).resolve().parent;repo=here.parents[1]
    root=Path(tempfile.mkdtemp(prefix='sky130_pdn_recipe_preflight_'))
    skill=repo/'r2g-skills/signoff-loop';db=root/'knowledge.sqlite'
    with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as source,sqlite3.connect(db) as target:
        source.backup(target)
    shutil.copy2(skill/'knowledge/heuristics.json',root/'heuristics.json')
    env=os.environ.copy()
    for name in ('PYTHONHOME','PYTHONPATH','PYTHONSAFEPATH'):env.pop(name,None)
    env.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(root/'heuristics.json'))
    records=[]
    for cid in ('C01466','C03125'):
        baseline=next(Path('/tmp/sky130_aux208/baseline').glob(cid+'__*'))
        project=root/cid;(project/'constraints').mkdir(parents=True);(project/'reports').mkdir()
        for name in ('config.mk','constraint.sdc'):shutil.copy2(baseline/name,project/'constraints'/name)
        for name in ('6_final.def','6_drc.lyrdb'):(project/name).symlink_to(baseline/name)
        counts=Counter((i.findtext('category') or '') for i in ET.parse(baseline/'6_drc.lyrdb').findall('.//items/item'))
        (project/'reports/drc.json').write_text(json.dumps(dict(status='fail',total_violations=sum(counts.values()),categories={k:{'count':v} for k,v in counts.items()})))
        cfg=project/'constraints/config.mk';before=cfg.read_bytes();sdc=(project/'constraints/constraint.sdc').read_bytes()
        commands=[]
        def cli(*args):
            p=subprocess.run([sys.executable,str(skill/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','drc',*args],env=env,text=True,capture_output=True)
            commands.append(dict(args=args,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr));return p
        plan=cli('--list');assert plan.returncode==0,plan.stderr
        strategy=next(s for s in json.loads(plan.stdout)['strategies'] if s['id']=='pin_pdn_clearance')
        denied=cli('--apply','pin_pdn_clearance')
        assert denied.returncode!=0 and cfg.read_bytes()==before,'unpromoted strategy applied without A/B authorization'
        selected=cli('--rank-first','pin_pdn_clearance','--next')
        assert selected.returncode==0 and selected.stdout.startswith('pin_pdn_clearance\tfloorplan\tdrc')
        applied=cli('--rank-first','pin_pdn_clearance','--apply','pin_pdn_clearance')
        assert applied.returncode==0,applied.stderr
        result=json.loads(applied.stdout)
        assert result['status']=='applied' and set(result['config_edits'])=={'PRE_IO_PLACEMENT_TCL'}
        assert (project/'constraints/constraint.sdc').read_bytes()==sdc
        assert (baseline/'config.mk').read_bytes()==before
        record=dict(case=cid,project=str(project),strategy=strategy,commands=commands,
                    unpromoted_live_apply_rejected=True,ab_selection_and_application_passed=True,
                    original_config_sha256=hashlib.sha256(before).hexdigest(),sdc_sha256=hashlib.sha256(sdc).hexdigest(),
                    physical_execution=False,promotion_claimed=False)
        (project/'preflight.json').write_text(json.dumps(record,indent=2)+'\n');records.append(record)
    output=dict(private_root=str(root),records=records,shipped_store_changed=False,
                scope='real CLI preparation only; repeated physical A/B and independent replay remain pending')
    (here/'auxiliary208_evidence/pdn_recipe_cli_preflight.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(dict(cases=[r['case'] for r in records],live_apply_rejected=True,ab_apply_prepared=True,physical_execution=False,private_root=str(root))))


if __name__=='__main__':main()
