#!/usr/bin/env python3
"""Read-only export of frozen inputs for explicitly selected dev cases."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


def main():
    p=argparse.ArgumentParser();p.add_argument('cases',nargs='+');p.add_argument('--include-gds',action='store_true')
    p.add_argument('--omit-baseline-def',action='store_true',help='Skip large baseline placement geometry when only RTL/netlist repair is being prepared')
    a=p.parse_args()
    root=Path('/proj/workarea/yangao');package=root/'rtl-certified-handoff-0002'
    inv=json.loads((root/'sky130_repair_20261026/tools/inventory_audited.json').read_text())
    ids=set(a.cases);files={};cases={};size=0
    def add(path,rel):
        nonlocal size
        if rel in files:return
        raw=path.read_bytes();size+=len(raw)
        if size>100*1024*1024:raise ValueError('bundle exceeds 100 MiB')
        files[rel]=dict(data=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
    for case in inv['failures']:
        cid=case['design'].split('__')[0]
        if cid not in ids:continue
        run=Path(case['run']);cfg=(run/'config.mk').read_text()
        cases[cid]=dict(original_run=str(run),run_name=run.name)
        for name in ('config.mk','constraint.sdc','result.json','6_final.v','6_final.def','netgen_lvs.rpt','netgen_lvs.log','6_drc.lyrdb','drc_signoff.log'):
            if name=='6_final.def' and a.omit_baseline_def:continue
            if (run/name).exists():add(run/name,'baseline/'+run.name+'/'+name)
        if a.include_gds:add(run/'6_final.gds','baseline/'+run.name+'/6_final.gds')
        inputs=re.search(r'^export VERILOG_FILES\s*=\s*(.*)$',cfg,re.M).group(1).split()
        if any(name.startswith('/work/_src/') for name in inputs):
            # Reconstruct the frozen driver's deterministic aliases without
            # creating any files or links on the source host.
            meta_path=package/'certified'/case['design']/'DESIGN.json'
            meta=json.loads(meta_path.read_text());add(meta_path,'package/'+str(meta_path.relative_to(package)))
            spec=importlib.util.spec_from_file_location('frozen_export_driver',root/'orfs_spec.py')
            driver=importlib.util.module_from_spec(spec);sys.modules[spec.name]=driver;spec.loader.exec_module(driver)
            original_inputs=driver._dedupe([str((meta_path.parent/f['file']).resolve()) for f in meta['source_files']])
            incs=driver._dedupe([str((meta_path.parent/i).resolve()) for i in meta.get('include_dirs',[])])
            original_inputs=driver._drop_included(driver._hdl_only(original_inputs),incs)
            if not all(Path(p).is_relative_to(package/'sources') for p in original_inputs+incs):raise ValueError('alias source escapes package')
            links={}
            def safe(paths):
                out=[]
                for path in paths:
                    parts=Path(path).parts;bad=[i for i,c in enumerate(parts) if driver._MAKE_HOSTILE.search(c)]
                    if not bad:out.append(path);continue
                    i=bad[-1];target=str(Path(*parts[:i+1]));alias=links.setdefault(target,'d'+str(len(links)))
                    out.append('/'.join(['/work/_src',alias,*parts[i+1:]]))
                return out
            if safe(original_inputs)!=inputs:raise ValueError('exported source aliases differ from baseline')
            match=re.search(r'^export VERILOG_INCLUDE_DIRS\s*=\s*(.*)$',cfg,re.M)
            if safe(incs)!=(match.group(1).split() if match else []):raise ValueError('include aliases differ from baseline')
            cases[cid]['source_aliases']={alias:target for target,alias in links.items()}
            cases[cid]['alias_config_sha256']=hashlib.sha256(cfg.encode()).hexdigest()
            inputs=original_inputs
        repos=set()
        for name in inputs:
            path=Path(name)
            if not path.is_relative_to(package/'sources'):raise ValueError('requires explicit source-alias reconstruction: '+case['design'])
            rel=path.relative_to(package);add(path,'package/'+str(rel));repos.add(package/rel.parts[0]/rel.parts[1])
        for repo in repos:
            for path in repo.rglob('*'):
                if path.is_file() and path.suffix.lower() in ('.v','.sv','.vh','.svh','.vi','.inc',
                        '.hex','.mem','.mif','.bin','.dat','.list','.init','.coe','.rom','.data'):
                    if not path.resolve().is_relative_to(package):raise ValueError('source escapes package')
                    add(path,'package/'+str(path.relative_to(package)))
    if set(cases)!=ids:raise ValueError('requested case missing from dev failure inventory')
    print(json.dumps(dict(cases=cases,files=files)))


if __name__=='__main__':main()
