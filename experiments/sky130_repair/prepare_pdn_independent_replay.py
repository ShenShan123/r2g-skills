"""Prepare an untouched third-family PDN subject; never force or promote selection."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    auxiliary = Path('/tmp/sky130_aux208')
    baseline = next((auxiliary/'baseline').glob('C02620__*'))
    family = 'R01493__refatmollah__vader'
    original = auxiliary/'package/sources'/family
    manifest = json.loads((auxiliary/'original_files.json').read_text())
    relevant = [p for p in original.rglob('*') if p.is_file()]
    relevant += [p for p in baseline.iterdir() if p.is_file()]
    for p in relevant:
        key = str(p.relative_to(auxiliary))
        if key not in manifest or sha(p) != manifest[key]:
            raise ValueError('unverified original artifact: '+key)
    skill = REPO/'r2g-skills/signoff-loop'
    root = auxiliary/'pdn_independent_replay'
    root.mkdir(exist_ok=False)
    project = root/'r01493_sine_gen'
    (project/'constraints').mkdir(parents=True)
    (project/'reports').mkdir()
    shutil.copytree(original, project/'rtl'/family)
    cfg = (baseline/'config.mk').read_text().replace(
        '/proj/workarea/yangao/rtl-certified-handoff-0002/sources/', str(project/'rtl')+'/')
    cfg = re.sub(r'^export SDC_FILE\s*=.*$', 'export SDC_FILE = '+str(project/'constraints/constraint.sdc'), cfg, flags=re.M)
    (project/'constraints/config.mk').write_text(cfg)
    shutil.copy2(baseline/'constraint.sdc', project/'constraints/constraint.sdc')
    results = project/'backend/RUN_FROZEN_SUBJECT/results'
    shutil.copytree(baseline, results)
    (project/'drc').mkdir()
    shutil.copy2(baseline/'6_drc.lyrdb', project/'drc/6_drc.lyrdb')
    counts = Counter(i.findtext('category') for i in ET.parse(baseline/'6_drc.lyrdb').findall('.//items/item'))
    def save(path, value):
        path.write_text(json.dumps(value, indent=2)+'\n')
    save(project/'reports/drc.json', dict(status='fail', total_violations=sum(counts.values()),
        categories={k:dict(count=v) for k,v in counts.items()}, report_file=str(results/'6_drc.lyrdb')))
    save(project/'reports/lvs.json', dict(status='clean', tool='netgen', report_file=str(results/'netgen_lvs.rpt')))
    result = json.loads((baseline/'result.json').read_text())
    save(project/'reports/ppa.json', dict(geometry=dict(instance_count=result['cells']), source_result_sha256=sha(baseline/'result.json')))
    save(project/'source_provenance.json', dict(case='C02620', design=result['design'], family=family,
        source_files={str(p.relative_to(project/'rtl')):sha(p) for p in (project/'rtl').rglob('*') if p.is_file()},
        sdc_sha256=sha(project/'constraints/constraint.sdc'), original_artifacts={str(p.relative_to(auxiliary)):sha(p) for p in relevant}))
    # Preparation has an isolated read-only snapshot of current recipe state.
    # Refresh the store after real promotion before attempting live selection.
    with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro', uri=True) as src, sqlite3.connect(root/'knowledge.sqlite') as dst:
        src.backup(dst)
    shutil.copy2(skill/'knowledge/heuristics.json', root/'heuristics.json')
    env=os.environ.copy()
    env.update(R2G_JOURNAL='0', R2G_KNOWLEDGE_DB=str(root/'knowledge.sqlite'), R2G_HEURISTICS_PATH=str(root/'heuristics.json'))
    cli=[sys.executable, '-P', str(skill/'scripts/reports/diagnose_signoff_fix.py'), str(project), '--check', 'drc']
    before=sha(project/'constraints/config.mk')
    plan=json.loads(subprocess.check_output(cli+['--list'], env=env, text=True))
    strategy=next(s for s in plan['strategies'] if s['id']=='pin_pdn_clearance')
    selected=subprocess.run(cli+['--next'], env=env, text=True, capture_output=True)
    assert sha(project/'constraints/config.mk')==before
    record=dict(project=str(project), routing_context=plan['routing_context'], strategy=strategy,
        normal_next=dict(returncode=selected.returncode, stdout=selected.stdout, stderr=selected.stderr),
        config_sha256=before, physical_execution=False, forced_selection=False,
        remaining='After promotion refresh recipe store, require normal next/apply, preserve ROM resolution and execute strict signoff in a free slot.')
    save(root/'preparation.json', record)
    save(HERE/'pdn_independent_prepared_20261027.json', record)
    print(json.dumps(dict(project=str(project), routing_context=plan['routing_context'], normal_next=selected.stdout, physical_execution=False)))


if __name__=='__main__':
    main()
