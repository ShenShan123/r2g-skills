#!/usr/bin/env python3
"""Freeze user-approved numeric envelopes before TEST repair; qualification stays a separate gate.

Reads existing DEV/qualification artifacts only. No EDA, LLM, or outcome-based
task replacement. Qualification review does not become a pass by making a manifest.
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys
import time

import t2a_envelope as E
import t2a_qualify as Q

sys.path.insert(0,str(Q.REPO/'r2g-skills/signoff-loop/knowledge'))
from knob_policy import KNOB_BOUNDS, KNOB_ENUMS

CAPS={'stdcell':0.55,'core':1.0,'die':1.0}
FLOOR_KEYS={k:v.replace('finish__','floorplan__') for k,v in E.AREA_KEYS.items()}
DEV_TASKS=('chacha_core__u72','picorv32__u89','riscv_core__u19')


def areas(path,keys):
    obj=json.loads(path.read_text())
    result={k:E._num(obj.get(v)) for k,v in keys.items()}
    if any(v is None or v<=0 for v in result.values()):
        raise ValueError('Missing/invalid reference or geometry: '+str(path))
    return result


def calibration(dev):
    rows=[]
    for task in DEV_TASKS:
        for arm in ('NB','TM0'):
            project=dev/(task+'__'+arm)
            runs=sorted((project/'backend').glob('RUN_*'))
            if not runs:raise ValueError('Missing DEV attempt: '+str(project))
            refpath=runs[0]/'logs/2_1_floorplan.json';ref=areas(refpath,FLOOR_KEYS)
            flowrows=[]
            for run in runs:
                report=run/'logs/6_report.json'
                if not report.exists():
                    flowrows.append({'run':run.name,'status':'NO_FINISH_GEOMETRY'});continue
                measured=areas(report,E.AREA_KEYS)
                flowrows.append({'run':run.name,'status':'MEASURED','report':str(report),
                                 'sha256':Q.digest(report),'areas':measured,
                                 'relative_change':{k:measured[k]/ref[k]-1 for k in CAPS}})
            rows.append({'task':task,'arm':arm,'reference':str(refpath),'reference_sha256':Q.digest(refpath),
                         'reference_areas':ref,'flows':flowrows})
    maxima={k:max(f['relative_change'][k] for r in rows for f in r['flows'] if f['status']=='MEASURED') for k in CAPS}
    rounded={k:round(math.ceil((v-1e-12)/0.05)*0.05,10) for k,v in maxima.items()}
    if rounded!=CAPS:raise ValueError('DEV-derived proposed caps no longer match the user-approved values')
    return {'all_six_attempts_included':True,'no_success_only_selection':True,'attempts':rows,
            'max_relative_change':maxima,'rounded_up_5_percentage_points':rounded}


def allowlist(config):
    out={}
    for key,bounds in KNOB_BOUNDS.items():
        out[key]=({'type':'number','min':bounds[0],'max':bounds[1]} if bounds is not None
                  else {'type':'rectangle'})
    platform=config.get('PLATFORM','sky130hd')
    layers=([f'metal{i}' for i in range(2,11)] if platform=='nangate45'
            else ['met1','met2','met3','met4','met5'])
    for key in ('MIN_ROUTING_LAYER','MAX_ROUTING_LAYER'):
        out[key]={'type':'enum','values':list(layers)}
    # Existing common catalogue actions, not extra privileges for memory/LLM.
    # One table with knob_policy: these are executable by memory and LLM edits alike.
    for key,values in KNOB_ENUMS.items():
        out[key]={'type':'enum','values':sorted(set(list(values)+([config[key]] if key in config else [])))}
    return out


def task_manifest(root,task):
    project=Path(task['project']);config=project/'constraints/config.mk'
    if Q.digest(config)!=task['config_sha256'] or Q.digest(project/'constraints/constraint.sdc')!=task['sdc_sha256']:
        raise ValueError('Qualification task inputs changed')
    initial=E.effective_config(config.read_text());allow=allowlist(initial)
    sdc=(project/'constraints/constraint.sdc').read_text()
    clock=re.search(r'^\s*set\s+clk_period\s+([0-9.]+)',sdc,re.M)
    if not clock:raise ValueError('Unresolved fixed clock')
    expected_clock=float(clock[1])
    if task.get('expected_clock_period_ns',expected_clock)!=expected_clock:
        raise ValueError('Clock differs from qualification registration')
    runs=sorted((project/'backend').glob('RUN_*'))
    reference=runs[0]/'logs/2_1_floorplan.json'
    measured=areas(reference,FLOOR_KEYS)
    immutable={'constraints/constraint.sdc':'sha256:'+Q.digest(project/'constraints/constraint.sdc')}
    immutable.update({str(p.relative_to(project)):'sha256:'+Q.digest(p) for p in (project/'rtl').rglob('*') if p.is_file()})
    # Absolute hook/include paths remain pinned until per-episode rebasing copies them.
    external={}
    for key in ('POST_GLOBAL_PLACE_TCL','FASTROUTE_TCL','VERILOG_INCLUDE_DIRS'):
        for item in initial.get(key,'').split():
            p=Path(item)
            for f in (sorted(p.rglob('*')) if p.is_dir() else [p]):
                if f.is_file():external[str(f)]='sha256:'+Q.digest(f)
    immutable.update(external)
    return {'schema':'t2a-task-envelope-v1','task':task['design']+'/'+task['variant'],
            'platform':'sky130hd','source_project':str(project),'initial_config':initial,
            'qualification_registration':str(root/'registration.json'),
            'qualification_registration_sha256':Q.digest(root/'registration.json'),
            'initial_config_sha256':Q.digest(config),'expected_clock_period_ns':expected_clock,
            'util_variable':'CORE_UTILIZATION','util_unit':'percent_0_to_100',
            'PLACE_DENSITY_unit':'fraction_0_to_1','stratum':task['stratum'],
            'immutable':{'knobs':sorted(set(initial)-set(allow)),'files':immutable},'allowlist':allow,
            'area':{k:{'ref':measured[k],'ref_stage':'initial_2_1_floorplan','unit':'um^2',
                       'reference_file':str(reference),'reference_sha256':Q.digest(reference),
                       'max_rel_increase':CAPS[k]} for k in CAPS},
            'require_execution_binding':True,'numeric_caps_status':'USER_APPROVED_FROZEN',
            'floorplan_mode_policy':'explicit_util_or_fixed_rectangles',
            'qualification_status':'REVIEW_REQUIRED','formal_launch_allowed':False}


def build(roots,dev,output):
    output.mkdir(parents=True,exist_ok=False);started,cpu=time.time(),time.process_time()
    Q.save(output/'area_calibration.json',calibration(dev))
    rows=[]
    for root in roots:
        reg=json.loads((root/'registration.json').read_text())
        for task in reg['tasks']:
            # Only actually attempted registered tasks; do not invent missing variants.
            if not (root/(task['design']+'__'+task['variant']+'.json')).exists():continue
            path=output/(task['design']+'__'+task['variant']+'.json')
            try:
                man=task_manifest(root,task);Q.save(path,man)
                rows.append({'task':man['task'],'manifest':str(path),'sha256':Q.digest(path),'status':'ENVELOPE_FROZEN_ONLY'})
            except (OSError,ValueError,IndexError) as exc:
                rows.append({'task':task['design']+'/'+task['variant'],'status':'NO_VERIFIED_REFERENCE','reason':str(exc)})
    Q.save(output/'registration.json',{'tasks':rows,'caps':CAPS,'caps_authority':'explicit user approval 2026-10-11',
           'calibration_sha256':Q.digest(output/'area_calibration.json'),'generator_sha256':Q.digest(__file__),
           'formal_test_manifest_frozen':False,'wall_s':time.time()-started,'cpu_s':time.process_time()-cpu,
           'eda_flows':0,'llm_requests':0})
    print(json.dumps({'tasks':rows,'formal_launch_allowed':False}))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--qualification',type=Path,action='append',required=True)
    ap.add_argument('--dev',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();build([r.resolve() for r in a.qualification],a.dev.resolve(),a.output.resolve())
