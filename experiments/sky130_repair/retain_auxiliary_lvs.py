#!/usr/bin/env python3
"""Repeat unchanged LVS on a copied run, retaining extraction for diagnosis."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('--name',required=True)
    p.add_argument('--checker-overlay',type=Path)
    p.add_argument('--review-slot',action='store_true')
    a=p.parse_args();root=Path('/tmp/sky130_aux208')
    if not a.run.resolve().is_relative_to(root) or not a.name.isidentifier():raise ValueError('invalid diagnostic scope')
    lock=(root/('review_worker.lock' if a.review_slot else 'worker.lock')).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    target=root/'diagnostics'/a.name;target.mkdir(parents=True,exist_ok=False)
    run=target/'runs'/a.run.name;run.mkdir(parents=True)
    for n in ('config.mk','constraint.sdc','6_final.def','result.json'):shutil.copy2(a.run/n,run/n)
    os.environ.update(R2G_AUX208_ROOT=str(root),R2G_AUX208_TOKEN=a.name+'_'+str(os.getpid()),
                      PATH=str(root/'tools/bin')+os.pathsep+os.environ['PATH'])
    spec=importlib.util.spec_from_file_location('retained_lvs',root/'tools/lvs130.py')
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    if a.checker_overlay:
        overlay=a.checker_overlay.resolve()
        if not overlay.is_relative_to(root):raise ValueError('checker overlay outside auxiliary root')
        checker.R2G=overlay
    original=shutil.rmtree;projroot=target/'lvs_projects';project=projroot/run.name
    def keep_project(path,*args,**kwargs):
        if Path(path)==project:return
        return original(path,*args,**kwargs)
    # Only change post-checker deletion behavior. Extraction, comparison deck,
    # command, environment and timeout are identical to the original checker.
    checker.shutil.rmtree=keep_project
    try:result=checker.one_and_record(run,projroot,21600,'sky130hd')
    finally:checker.shutil.rmtree=original
    (target/'diagnostic.json').write_text(json.dumps(dict(source_run=str(a.run),result=result,
        extraction_retained=str(project),checker_overlay=str(a.checker_overlay) if a.checker_overlay else None,
        acceptance='diagnostic only; use strict top-level verdict'),indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
