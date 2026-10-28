"""Export the validated recipe selector and an exact knowledge snapshot, no RTL.

The bundle is only for isolated diagnosis/application. It does not install a
physical toolchain, update a running campaign or manufacture lifecycle rows.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SKILL=REPO/'r2g-skills/signoff-loop'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    root=Path('/tmp/sky130_selector_export_20261027')
    root.mkdir(exist_ok=False)
    dest=root/'signoff-loop'
    dest.mkdir()
    paths=list((SKILL/'scripts').rglob('*.py'))
    paths += [p for p in (SKILL/'knowledge').iterdir() if p.suffix in ('.py','.json','.sql')]
    paths += [SKILL/'scripts/flow/orfs_hooks'/name for name in
              ('pin_pdn_clearance.tcl','buffer_port_feedthroughs.tcl')]
    for p in paths:
        rel=p.relative_to(SKILL)
        if p.is_symlink() or not p.is_file():raise ValueError('unsupported selector source')
        target=dest/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,target)
    store=SKILL/'knowledge/knowledge.sqlite'
    before=sha(store)
    with sqlite3.connect('file:'+str(store)+'?mode=ro',uri=True) as source,sqlite3.connect(dest/'knowledge/knowledge.sqlite') as snapshot:
        source.backup(snapshot)
    if sha(store)!=before:raise ValueError('production store changed during snapshot')
    sys.path.insert(0,str(dest/'knowledge'))
    import recipe_lifecycle
    import physical_ab_scope
    import port_feedthrough_scope
    import pin_pdn_scope
    import fixed_area_scope
    keys=[module.KEY for module in (port_feedthrough_scope,pin_pdn_scope,fixed_area_scope)]
    with sqlite3.connect('file:'+str(dest/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as db:
        for key in keys:
            if not physical_ab_scope.has_durable_repeated_wins(db,recipe_lifecycle,key):
                raise ValueError('published recipe evidence incomplete: '+key['strategy'])
    evidence=root/'evidence'
    evidence.mkdir()
    for name in ('sky130_recipe_validation_20261026','sky130_pdn_recipe_validation_20261026','sky130_area_recipe_validation_20261027'):
        sub=evidence/name
        sub.mkdir()
        for filename in ('durable_evidence.json','published.json'):
            shutil.copy2(REPO/'design_cases'/name/filename,sub/filename)
    manifest=dict(at=time.strftime('%FT%T%z'),source_store_sha256=before,
        snapshot_store_sha256=sha(dest/'knowledge/knowledge.sqlite'),validated_anchor_keys=keys,
        scope='Selector Python code, two pinned hooks, knowledge snapshot and published evidence manifests. No design RTL, baseline artifacts, datasets, PDK or credentials. No physical execution or existing installation update.',
        files={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()})
    (root/'bundle_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=Path('/tmp/sky130_selector_bundle_20261027.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:
        for p in sorted(root.iterdir()):tar.add(p,arcname=p.name)
    record=dict(archive=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,
        files=len(manifest['files']),source_store_sha256=before,
        scope=manifest['scope'],validated_anchor_keys=keys,remote_staged=False)
    (HERE/'selector_bundle_prepared_20261027.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))


if __name__=='__main__':main()
