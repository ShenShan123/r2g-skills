#!/usr/bin/env python3
"""Import one signed auxiliary run atomically; never overwrite active cases."""
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from strict_signoff import inspect_run


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_receipt(parent,design,receipt,preserve_existing=False):
    parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.'+design+'.import.',dir=parent));dest=parent/design
    try:
        for name in ('repair.json','progress.json'):(staging/name).write_text(json.dumps(receipt,indent=2)+'\n')
        libc=ctypes.CDLL(None,use_errno=True)
        if libc.renameat2(-100,os.fsencode(staging),-100,os.fsencode(dest),1):
            error=ctypes.get_errno()
            if error!=errno.EEXIST:raise OSError(error,'Atomic publication failed')
            if preserve_existing:return False
            if json.loads((dest/'repair.json').read_text())!=receipt:raise ValueError('verified receipt already differs')
            return False
        return True
    finally:
        if staging.exists():shutil.rmtree(staging)


def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--design',required=True);a=p.parse_args()
    root=Path('/proj/workarea/yangao/sky130_repair_20261026')
    if not re.fullmatch(r'C\d{5}__[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+',a.design):raise ValueError('invalid design')
    if sha(a.archive)!=a.sha256:raise ValueError('archive checksum mismatch')
    out=root/'auxiliary208';out.mkdir(exist_ok=True);target=out/a.design
    with tarfile.open(a.archive) as tar:
        for member in tar.getmembers():
            name=Path(member.name)
            if name.is_absolute() or '..' in name.parts or name.parts[0]!=a.design or not(member.isfile() or member.isdir()):raise ValueError('unexpected archive entry')
        if target.exists():
            archived_manifest=tar.extractfile(a.design+'/transfer_manifest.json').read()
            if (target/'transfer_manifest.json').read_bytes()!=archived_manifest:raise ValueError('existing auxiliary archive differs')
        else:tar.extractall(out,filter='data')
    files=json.loads((target/'transfer_manifest.json').read_text())
    for rel,want in files.items():
        path=(target/rel).resolve()
        if not path.is_relative_to(target.resolve()) or sha(path)!=want:raise ValueError('transferred artifact differs: '+rel)
    receipt=json.loads((target/'repair.json').read_text());baseline=Path(receipt['baseline'])
    inv=json.loads((root/'tools/inventory_audited.json').read_text())
    case=next(c for c in inv['failures'] if c['design']==a.design)
    if str(baseline)!=case['run']:raise ValueError('not the frozen dev design')
    for name,want in receipt['baseline_hashes'].items():
        if sha(baseline/name)!=want:raise ValueError('baseline changed')
    manifest=json.loads((target/'evidence/physical_run_manifest.json').read_text());count=0
    for rel,want in manifest['source_bundle_sha256'].items():
        if not rel.startswith('package/'):continue
        source=Path('/proj/workarea/yangao/rtl-certified-handoff-0002')/rel.removeprefix('package/')
        if sha(source)!=want:raise ValueError('frozen source changed: '+rel)
        count+=1
    check=inspect_run(target/'accepted_run')
    if not check['dual_clean']:raise ValueError('strict acceptance failed')
    if (target/'work/constraint.sdc').read_bytes()!=(baseline/'constraint.sdc').read_bytes():raise ValueError('clock constraints changed')
    for name in ('6_final.gds','6_drc.lyrdb','drc_signoff.log','netgen_lvs.rpt','netgen_lvs.log'):
        if (target/'accepted_run'/name).stat().st_size<=0:raise ValueError('empty evidence')
    receipt.update(acceptance_audit=check,reconciled_on_216=True,authoritative_reconciliation_pending=False,
                   frozen_source_files_rechecked=count)
    # A prior failed case is evidence too. Publish verified imports separately
    # so no old or active campaign directory ever needs to be replaced.
    publish_receipt(root/'verified_imports/cases',a.design,receipt)
    installed=publish_receipt(root/'campaign/cases',a.design,receipt,preserve_existing=True)
    print(json.dumps(dict(imported=a.design,dual_clean=True,evidence_files=len(files),source_files_rechecked=count,
                          campaign_case_preserved=not installed,verified_index=str(root/'verified_imports/cases'/a.design))))


if __name__=='__main__':main()
