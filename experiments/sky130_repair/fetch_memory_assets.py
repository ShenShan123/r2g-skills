#!/usr/bin/env python3
"""Restore only original data blobs from the handoff's exact upstream commits.

Inputs are read-only source identities collected on 216 and complete upstream
Git trees. Every frozen compilation source must match its upstream Git blob.
Downloaded memory contents must match the Git tree blob ID, not just a URL.
"""
import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

ASSETS={
 'nguyenhuydung3007/vga-controller-verilog':['cp866-8x16.hex'],
 'merlionfire/SpinalTetris':[
  'design/IPS/string_draw_engine/rtl/string_draw_engine.v_toplevel_rom.bin',
  'design/IPS/string_draw_engine/rtl/string_draw_engine.v_toplevel_wall_wall_rom.bin'],
 'm13253/verilog-simple-vga':['font/font.hex'],
 'zipcpu/sdr':['rtl/sintable.hex','rtl/amdemod.hex'],
 'utodev/smartrom':['core/common/fuente_hexadecimal_ibm.hex'],
 'qiandawg/riscv-fpu-interrupt':[
  'sc_interrupt/src/Assembly/RISCVscintSwitchLED7Seg/dmem.mem',
  'sc_interrupt/src/Assembly/RISCVscintSwitchLED7Seg/imem.mem'],
}


def blob_id(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--identities',type=Path,required=True)
    p.add_argument('--trees',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();identities=json.loads(a.identities.read_text());groups={}
    for case in identities:
        repo=case['repository'];key=repo['owner']+'/'+repo['name']
        if key in ASSETS:groups.setdefault(key,[]).append(case)
    for key,cases in groups.items():
        repo=cases[0]['repository'];assert all(c['repository']==repo for c in cases)
        tree_doc=json.loads((a.trees/(key.replace('/','__')+'.json')).read_text())
        if tree_doc['repository']!=repo or tree_doc['tree_response'].get('truncated'):
            raise ValueError('tree identity/integrity mismatch: '+key)
        tree={x['path']:x for x in tree_doc['tree_response']['tree'] if x['type']=='blob'}
        for case in cases:
            for f in case['files']:
                if tree.get(f['path'],{}).get('sha')!=f['git_blob_sha1']:
                    raise ValueError('frozen RTL does not match upstream commit: '+case['design']+' '+f['path'])
        target=a.out/cases[0]['source_root'];target.mkdir(parents=True,exist_ok=True)
        manifest=dict(repository=repo,source_root=cases[0]['source_root'],
                      source_identities=cases,files=[],reference_overrides={},
                      policy='original data only; frozen compilation RTL matches upstream Git blobs')
        for path in ASSETS[key]:
            entry=tree[path]
            if repo['host']=='github.com':
                url=f"https://raw.githubusercontent.com/{key}/{repo['commit']}/{urllib.parse.quote(path)}"
            else:
                url=f"https://codeberg.org/{key}/raw/commit/{repo['commit']}/{urllib.parse.quote(path)}"
            request=urllib.request.Request(url,headers={'User-Agent':'R2G-source-provenance-audit'})
            with urllib.request.urlopen(request,timeout=30) as response:data=response.read()
            if blob_id(data)!=entry['sha']:raise ValueError('downloaded Git blob hash mismatch: '+url)
            dest=target/'files'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
            manifest['files'].append(dict(path=path,git_blob_sha1=entry['sha'],
                sha256=hashlib.sha256(data).hexdigest(),size=len(data),url=url))
            # Bind strings actually present in frozen compilation sources. For
            # old absolute developer paths, require a three-component suffix;
            # never choose an unrelated file merely because its basename fits.
            for case in cases:
                for f in case['files']:
                    for ref in f['data_literals']:
                        tail=Path(ref).parts
                        if Path(ref).name!=Path(path).name:continue
                        if (':' in ref or ref.startswith('/')) and Path(path).parts[-3:]!=tail[-3:]:continue
                        previous=manifest['reference_overrides'].get(ref)
                        if previous and previous!=path:raise ValueError('ambiguous data reference: '+ref)
                        manifest['reference_overrides'][ref]=path
        (target/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print(key,len(cases),'designs',len(manifest['files']),'original data files verified')


if __name__=='__main__':main()
