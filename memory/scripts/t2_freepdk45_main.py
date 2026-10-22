#!/usr/bin/env python3
"""Outcome-blind FreePDK45 main-table registration and once-only execution.

R2G first; target/same-IP exclusion BEFORE constructing eight memory methods.
AdaMEM replaces ReMe. Reuses the checked agent worker, evidence importer and
no-retry dispatcher, not the historical deterministic-first repair protocol.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(REPO/'memory/scripts'),str(REPO/'memory')]
import t2_nb_followup as F
import t2_agent_followup as G
import t2_agent_request_resume as R
import t2_agent_dispatch as D
from t2_agent_memories import TM0_THREE_WAY_POLICY
import t2_adamem as ADA

VERSION='freepdk45-main50-adamem-v3'
SEED='freepdk45-main50-20261022-v1'
ARMS=('NB','RET','RECIPE','EXPEL','AWM','AMEM','ADAMEM','REASONINGBANK','TM0')
BUILDS=('EXPEL','AWM','AMEM','REASONINGBANK')
CORPUS=F.N.CORPUS
PLATFORM=Path('/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow/platforms/nangate45')
BASE=Path('/data2/zhangdy/tehm-experiments/t2-tehm-three-way8-20261022-r1/folds/C02786')
OLD_NB=Path('/data2/zhangdy/tehm-experiments/t2-agent9-nb8-20261021-r3')
OLD_QUEUE=Path('/data2/zhangdy/tehm-experiments/t2-agent9-json-resume-20261022-r4')
LC=F.N.EVAL/'t2-lc-v1-20261018c'
SELECTION_BASE=Path('/data2/zhangdy/tehm-experiments/t2-freepdk45-main50-20261022-r2')
# Keep the still-running Sky130 keccak slot (84..91 and SMT siblings)
# completely separate. Baseline and memory phases use the same seven slots.
SLOTS=G.SLOTS[:-1]
sha,load,save=F.sha,F.load,F.save


def canonical_repo(value):
    return value.lower().rstrip('/').removesuffix('.git')


def verify_same_roster(selected):
    """Changing a memory method does not reselect the 50 scientific inputs."""
    previous=load(SELECTION_BASE/'registration.json')
    selection=SELECTION_BASE/'selection.json'
    if previous['files'].get(str(selection))!=sha(selection):raise ValueError('Prior selection receipt changed')
    old=load(selection)['selected']
    if [r['handoff_id'] for r in selected]!=[r['handoff_id'] for r in old]:
        raise ValueError('AdaMEM amendment must retain the frozen 50-design roster')
    origins={p:h for p,h in previous['files'].items()
             if p.startswith(str(CORPUS)+'/') or p.startswith(str(F.N.INITIAL)+'/')}
    F.verify_files(origins)
    return {'selection':str(selection),'sha256':sha(selection),'verified_original_inputs':len(origins)}


def select_candidates(inventory,csv_rows,blocked_ids,count=50):
    """Only declared input metadata; never examine prior pass/fail/PPA values."""
    by_id={r['design'].split('__')[0]:r for r in csv_rows}
    blocked_repos={canonical_repo(r['repository_url']) for r in inventory if r['handoff_id'] in blocked_ids}
    # Previously exposed engineering SOURCE/DEV IP repositories (not all forks
    # of a hosting organisation, nor every module with a generic name).
    blocked_repos.update(map(canonical_repo,(
        'https://github.com/YosysHQ/picorv32','https://github.com/ultraembedded/riscv',
        'https://github.com/alexforencich/verilog-ethernet',
        'https://github.com/secworks/aes','https://github.com/secworks/chacha',
        'https://github.com/secworks/sha256','https://github.com/freecores/des',
        'https://github.com/freecores/i2c')))
    selected=[];decisions=[];repos=set();fingerprints=set()
    ordered=sorted(inventory,key=lambda r:hashlib.sha256((SEED+':'+r['handoff_id']).encode()).hexdigest())
    for item in ordered:
        hid=item['handoff_id'];row=by_id.get(hid);repo=canonical_repo(item['repository_url'])
        reason=None
        if hid in blocked_ids or repo in blocked_repos:reason='previous_experiment_source_repository'
        elif item.get('frontend_route')!='verilog-native':reason='outside_native_verilog_scope'
        elif row is None:reason='no_port_and_constraint_input_metadata'
        elif row.get('clock_kind')!='real' or str(row.get('period')) not in ('5','5.0'):reason='not_real_5ns_clock'
        elif repo in repos:reason='one_design_per_repository'
        elif item.get('structural_fingerprint') in fingerprints:reason='duplicate_structural_fingerprint'
        elif not item.get('structural_fingerprint'):reason='missing_structural_fingerprint'
        if reason:
            decisions.append({'handoff_id':hid,'reason':reason});continue
        selected.append(dict(item,input_run_dir=row['run_dir'],clock_port=row.get('clock_port')))
        repos.add(repo);fingerprints.add(item['structural_fingerprint'])
        if len(selected)==count:break
    if len(selected)!=count:raise ValueError('Insufficient input-eligible independent repositories')
    return selected,decisions


def finite(value,positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>=1e30:
        return None
    if positive and value<=0:return None
    return float(value)


def relative(final,reference):
    a,b=finite(final),finite(reference,True)
    return a/b-1 if a is not None and b is not None else None


def ppa(project,episode):
    """Only the run actually graded at termination; never an older clean run."""
    project=Path(project);tag=(episode.get('final') or {}).get('judged_run')
    result={'status':'UNKNOWN','accepted':episode.get('status')=='ACCEPTED','run_tag':tag,
            'units':{'areas':'um^2','slack':'ns','power':'W'},
            'power_basis':'ORFS/STA vectorless estimate; same library/activity settings, not measured silicon'}
    if not tag or Path(tag).name!=tag:return result
    report=project/'reports/ppa.json';run=project/'backend'/tag
    if not report.exists():return result
    data=load(report)
    if data.get('run_tag')!=tag or Path(data.get('run_dir','')).resolve()!=run.resolve():return result
    geometry=data.get('geometry') or {};summary=data.get('summary') or {}
    values={k:finite(geometry.get(key),True) for k,key in
            [('stdcell_area_um2','stdcell_area_um2'),('core_area_um2','core_area_um2'),('die_area_um2','die_area_um2')]}
    values.update(setup_slack_ns=finite((summary.get('timing') or {}).get('setup_wns')),
                  hold_slack_ns=finite((summary.get('timing') or {}).get('hold_wns')),
                  setup_tns_ns=finite((summary.get('timing') or {}).get('setup_tns')),
                  total_power_w=finite((summary.get('power') or {}).get('total_power_w')))
    ref=project/'reports/initial-area-reference.json'
    if ref.exists():areas=load(ref).get('areas') or {}
    else:
        manifest=load(project/'manifest.json')
        areas={k:v.get('ref') for k,v in manifest.get('area',{}).items()}
    result.update(status='MEASURED_PARTIAL' if any(v is None for v in values.values()) else 'MEASURED',
                  values=values,report=str(report),report_sha256=sha(report),
                  initial_area_relative_change={k:relative(values[k+'_area_um2'],areas.get(k)) for k in ('stdcell','core','die')})
    return result


def configure(registry,slots):
    F.N.IDS=tuple(registry);F.ARMS=ARMS[1:];F.SLOTS=slots;F.collect=collect
    G.ARMS=ARMS[1:];G.BUILDS=BUILDS


def worker_popen(cmd,**kwargs):
    cmd=list(cmd)
    if Path(cmd[4]).name!='t2a_batch.py' or cmd[5]!='episode':raise ValueError('Unexpected worker boundary')
    cmd[4]=str(Path(cmd[4]).with_name('t2_agent_request_resume.py'))
    return subprocess.Popen(cmd,**kwargs)


def integrate_provenance_guards(batch_text,runtime_text):
    """Small, explicit delta on the tested private worker, not a wholesale host replacement."""
    from t2_agent_nb_package import replace_once
    batch_text=replace_once(batch_text,"        record['initial']=initial\n",(
        "        record['initial']=initial\n"
        "        initial_acc=initial.get('acceptance') or {}\n"
        "        if ((initial_acc.get('checks') or {}).get('A11_provenance')=='fail'\n"
        "                or (initial.get('verification_stop') and initial_acc.get('accepted'))):\n"
        "            raise RuntimeError('Technical signoff/provenance failure before repair')\n"))
    runtime_text=replace_once(runtime_text,"    checks=acceptance.get('checks') or {}\n",(
        "    checks=acceptance.get('checks') or {}\n"
        "    if checks.get('A11_provenance')=='fail' or final.get('verification_stop') in (75,76,-9,124,137):\n"
        "        return 'UNVERIFIED_OR_SAFETY_STOP'\n"))
    return batch_text,runtime_text


def collect(root):
    root=Path(root);reg=load(root/'registration.json');registry=load(root/'nb/origin-registry.json')
    records=[]
    for hid,origin in registry.items():
        for arm in ARMS:
            base=root/'nb' if arm=='NB' else root/'folds'/hid
            project=base/'episodes'/(origin['task'].replace('/','__')+'__'+arm+'__r1')
            file=project/'episode.json';e=load(file) if file.exists() else {}
            events=F.rows(project/'reports/resource_ledger.jsonl') if file.exists() else []
            trajectories=e.get('repair_turns') or []
            local=[v for v in events if v.get('type')=='memory_procedure_consumption']
            harmful=sum(bool((v.get('comparison') or {}).get('regressions')) for v in trajectories)
            records.append({'design':hid,'task':origin['task'],'arm':arm,'status':e.get('status','NOT_STARTED'),
                'episode':str(file),'cost':F.ledger_cost(project/'reports/resource_ledger.jsonl') if file.exists() else None,
                'worker_wall_s':e.get('total_worker_wall_s'),'harness_cpu_s':e.get('harness_cpu_s'),
                'harmful_attempts_observed':harmful,
                'harmful_attempts_basis':'explicit run-vector regressions, not all failed/rolled-back trials',
                'rollback_attempts':sum(v.get('rolled_back') is True for v in trajectories),
                'local_memory_trial_receipts':local,'repair_executions':len(trajectories),
                'ppa':ppa(project,e) if file.exists() else {'status':'UNKNOWN'}})
    summaries={}
    for arm in ARMS:
        rr=[r for r in records if r['arm']==arm];paired=[];ppa_pairs=[]
        for r in rr:
            nb=next(x for x in records if x['design']==r['design'] and x['arm']=='NB')
            if r['status'] in ('ACCEPTED','NOT_ACCEPTED') and nb['status'] in ('ACCEPTED','NOT_ACCEPTED'):paired.append((r,nb))
            if r['status']==nb['status']=='ACCEPTED':ppa_pairs.append((r,nb))
        costs=[r['cost'] for r in rr if r['cost'] is not None]
        metrics={}
        for key in ('stdcell_area_um2','core_area_um2','die_area_um2','total_power_w','setup_slack_ns'):
            changes=[]
            for a,b in ppa_pairs:
                x=(a['ppa'].get('values') or {}).get(key);y=(b['ppa'].get('values') or {}).get(key)
                delta=(x-y if x is not None and y is not None else None) if key=='setup_slack_ns' else relative(x,y)
                if delta is not None:changes.append(delta)
            metrics[key]={'n':len(changes),'mean_delta_ns' if key=='setup_slack_ns' else 'mean_relative_change':
                          sum(changes)/len(changes) if changes else None}
        totals={k:sum(c.get(k,0) for c in costs) for k in ('requests','known_tokens','unknown_requests','flows','tool_calls',
            'checker_runs','embedding_operations','embedding_cpu_s','embedding_wall_s')}
        summaries[arm]={'registered':len(rr),'accepted':sum(r['status']=='ACCEPTED' for r in rr),
            'status_counts':dict(Counter(r['status'] for r in rr)), 'measured_pairs':len(paired),
            'G':sum(a['status']=='ACCEPTED' and b['status']=='NOT_ACCEPTED' for a,b in paired),
            'H':sum(a['status']=='NOT_ACCEPTED' and b['status']=='ACCEPTED' for a,b in paired),
            'cost_totals_so_far':totals,'mean_cost_per_registered_episode_so_far':{k:v/len(rr) for k,v in totals.items()},
            'harmful_attempts_observed':sum(r['harmful_attempts_observed'] for r in rr),
            'rollback_attempts':sum(r['rollback_attempts'] for r in rr),
            'repair_executions':sum(r['repair_executions'] for r in rr),
            'mean_worker_wall_s_so_far':sum(r['worker_wall_s'] or 0 for r in rr)/len(rr),
            'PPA_paired_accepted_vs_R2G':metrics,'PPA_pair_count':len(ppa_pairs)}
    construction={hid+'/'+arm:F.ledger_cost(root/'folds'/hid/'controls/construction'/arm/'reports/resource_ledger.jsonl')
                  for hid in registry for arm in BUILDS}
    for hid in registry:
        fold=root/'folds'/hid
        for arm,path in [('RET',fold/'controls/RET.json'),('RECIPE',fold/'controls/RECIPE.json'),
                         ('ADAMEM',fold/'controls/ADAMEM.json'),
                         ('TM0',fold/'agent-store/agent-construction.json')]:
            if path.exists():
                d=load(path);construction[hid+'/'+arm]={'requests':0,'known_tokens':0,
                    'wall_s':d.get('wall_s',d.get('construction_wall_s')),
                    'cpu_s':d.get('cpu_s',d.get('construction_cpu_s')),'receipt':str(path),'receipt_sha256':sha(path)}
        if (fold/'offline-ready.json').exists():
            d=load(fold/'offline-ready.json');construction[hid+'/shared-history-preparation']={
                'wall_s':d.get('preparation_wall_s'),'cpu_s':d.get('preparation_cpu_s'),
                'note':'shared preprocessing includes some local-build work; do not add overlapping intervals twice'}
    prior=Path(reg.get('superseded_technical_run','/nonexistent'))
    technical_costs=[{'episode':str(p),'cost_so_far':F.ledger_cost(p.parent/'reports/resource_ledger.jsonl')}
                     for p in (prior/'nb/episodes').glob('*/episode.json')]
    save(root/'table2-results.json',{'schema':VERSION,'platform':'nangate45','updated':time.time(),
        'registered_episodes':len(records),'arms':summaries,'episodes':records,
        'construction_costs_separate':construction,'ReMe_disabled':True,'AdaMEM_configuration':ADA.CONFIG,
        'superseded_platform_startup_costs_separate':technical_costs,
        'complete':all(r['status'] in ('ACCEPTED','NOT_ACCEPTED') for r in records),
        'all_registered_means_include_unfinished_as_cost_so_far_not_zero_final_cost':True})
    with (root/'table2-matrix.csv').open('w') as out:
        w=csv.writer(out);w.writerow(['design',*['R2G' if a=='NB' else 'TEHM' if a=='TM0' else a for a in ARMS]])
        for hid in registry:w.writerow([hid,*[next(x['status'] for x in records if x['design']==hid and x['arm']==a) for a in ARMS]])
    fields=['design','arm','status','stdcell_area_um2','core_area_um2','die_area_um2','setup_slack_ns','hold_slack_ns','setup_tns_ns','total_power_w']
    with (root/'table2-ppa.csv').open('w') as out:
        w=csv.DictWriter(out,fieldnames=fields);w.writeheader()
        for r in records:w.writerow({**{k:r[k] for k in ('design','arm','status')},**r['ppa'].get('values',{})})


def prepare(root,tests):
    import t2a_task_manifests as TM
    import t2a_envelope as E
    from t2_agent_nb_package import replace_once
    root=Path(root).resolve();tests=Path(tests).resolve()
    if root.exists():raise FileExistsError('Do not overwrite an experiment')
    from t2_tehm_three_way_rerun import passing_tests
    passing_tests(tests)
    R.install_online()
    import t2a_batch as B
    old=load(BASE/'registration.json');F.verify_files({p:h for p,h in old['files'].items() if '/code/' in p})
    inventory=[json.loads(l) for l in (CORPUS/'metadata/INVENTORY.jsonl').read_text().splitlines() if l.strip()]
    csv_rows=list(csv.DictReader((CORPUS.parent/'inventory_sky130hd.csv').open()))
    lc=load(LC/'registration.json');blocked=set(load(OLD_NB/'origin-registry.json'))
    blocked.update(t['task'].split('__')[0] for t in lc['tasks'])
    selected,exclusions=select_candidates(inventory,csv_rows,blocked)
    selection_binding=verify_same_roster(selected)
    root.mkdir(parents=True);(root/'logs').mkdir();(root/'tmp').mkdir()
    (root/'nb/logs').mkdir(parents=True);nb=root/'nb';code=root/'code'
    shutil.copytree(BASE/'code',code,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('t2_freepdk45_main.py','t2_nb_followup.py','t2a_task_manifests.py',
                 't2_adamem.py','t2_agent_memories.py','t2_agent_dispatch.py','t2_agent_runtime.py'):
        shutil.copy2(REPO/'memory/scripts'/name,code/'memory/scripts'/name)
    worker=code/'memory/scripts/t2a_batch.py'
    worker.write_text(replace_once(worker.read_text(),"if reg.get('schema')=='nb-firstlook8-v1':",
                                   "if man.get('area_reference_policy'):"))
    runtime=code/'memory/scripts/t2a_runtime.py'
    wt,rt=integrate_provenance_guards(worker.read_text(),runtime.read_text())
    rt=replace_once(rt,"ARMS = ('NB', 'RET', 'RECIPE', 'EXPEL', 'AWM', 'AMEM', 'TM0', 'REME', 'REASONINGBANK')",
                    'ARMS = '+repr(D.ARMS))
    worker.write_text(wt);runtime.write_text(rt)
    adamem_sources={}
    for src,rel in ADA.upstream_sources():
        dst=code/'upstream'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        adamem_sources[str(src)]=sha(src)
    shutil.copy2(REPO/'r2g-skills/signoff-loop/scripts/flow/run_lvs.sh',
                 code/'r2g-skills/signoff-loop/scripts/flow/run_lvs.sh')
    protocol=REPO/'memory/evaluation/r2g_Table2_FreePDK45_50design_Protocol_20261022.md'
    shutil.copy2(protocol,root/'protocol.md')
    native=Path('/data2/zhangdy/tehm-experiments/t2-freepdk45-lvs-check-20261022-r2/technical-receipt.json')
    if not load(native).get('passed'):raise ValueError('Real frozen-layout LVS replay has not passed')
    shutil.copy2(native,root/'native-lvs-technical-receipt.json')
    shutil.copytree(code,nb/'code');shutil.copy2(tests,root/'offline-tests.xml')
    save(root/'selection.json',{'seed':SEED,'selection_uses_repair_outcomes':False,'selected':selected,
         'unchanged_from_eight_arm_package':selection_binding,
         'preselection_exclusions':exclusions,'excluded_previous_ids':sorted(blocked),
         'eligibility':'native Verilog; real 5ns constraint input; one repository and structural fingerprint each'})
    hook=nb/'code/r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl'
    registry={};tasks=[];inputs={}
    for item in selected:
        hid=item['handoff_id'];certroot=CORPUS/item['directory'];cert=load(certroot/'DESIGN.json')
        original=F.N.INITIAL/item['input_run_dir'];cfg=E.effective_config((original/'config.mk').read_text())
        repo=F.N.checked_inside(CORPUS/'sources',certroot/cert['source_dir'])
        expected={F.N.checked_inside(repo,certroot/f['file']):f['sha256'] for f in cert['source_files']}
        for p,h in expected.items():
            if sha(p)!=h:raise ValueError('Changed certified source: '+str(p))
        def source(path):
            if '/sources/' not in path:raise ValueError('Unresolved source input')
            return F.N.checked_inside(repo,CORPUS/'sources'/path.split('/sources/',1)[1])
        files=[source(p) for p in cfg['VERILOG_FILES'].split()]
        if set(files)!=set(expected) or cfg['DESIGN_NAME']!=cert['top_module']:raise ValueError('Source closure mismatch: '+hid)
        project=nb/'tasks'/(item['name']+'__initial');(project/'constraints').mkdir(parents=True)
        for p in repo.rglob('*'):F.N.checked_inside(repo,p)
        shutil.copytree(repo,project/'rtl')
        cfg.update(PLATFORM='nangate45',CORE_UTILIZATION='20',ABC_AREA='1',SYNTH_MEMORY_MAX_BITS='131072',
            VERILOG_FILES=' '.join(str(project/'rtl'/p.relative_to(repo)) for p in files),
            VERILOG_INCLUDE_DIRS=' '.join(str(project/'rtl'/source(p).relative_to(repo)) for p in cfg['VERILOG_INCLUDE_DIRS'].split()),
            SDC_FILE=str(project/'constraints/constraint.sdc'))
        sdc=original/'constraint.sdc';shutil.copy2(sdc,project/'constraints/source-constraint.sdc.reference')
        (project/'constraints/constraint.sdc').write_text(F.N.annotate_clock_period(sdc.read_text(),5.0))
        config=project/'constraints/config.mk';config.write_text(''.join('export '+k+' = '+v+'\n' for k,v in cfg.items()))
        allow=TM.allowlist(cfg);allow['POST_GLOBAL_PLACE_TCL']={'type':'enum','values':[str(hook)]}
        immutable={'constraints/constraint.sdc':'sha256:'+sha(project/'constraints/constraint.sdc'),
            'constraints/source-constraint.sdc.reference':'sha256:'+sha(sdc),str(hook):'sha256:'+sha(hook),
            **{str(p.relative_to(project)):'sha256:'+sha(p) for p in (project/'rtl').rglob('*') if p.is_file()}}
        task=item['name']+'/initial'
        save(project/'manifest.json',{'schema':'t2a-task-envelope-v1','task':task,'platform':'nangate45',
            'source_project':str(project),'initial_config':cfg,'initial_config_sha256':sha(config),
            'expected_clock_period_ns':5.0,'stratum':'FIXED_INITIAL_INPUT','source_handoff_id':hid,
            'immutable':{'knobs':sorted(set(cfg)-set(allow)),'files':immutable},'allowlist':allow,
            'area':{k:{'ref':None,'max_rel_increase':v,'unit':'um^2'} for k,v in F.N.CAPS.items()},
            'area_reference_policy':F.N.REFERENCE_POLICY,'require_execution_binding':True,
            'floorplan_mode_policy':'explicit_util_or_fixed_rectangles'})
        registry[hid]={'handoff_id':hid,'design_id':cert['design_id'],'task':task,
            'verified_ip_key':canonical_repo(cert['repository']['url'])+'#'+cert['top_module'],
            'owner':cert['repository']['owner'],'repository':cert['repository']['url'],'commit':cert['repository']['commit'],
            'core_rtl_hashes':sorted({h for p,h in expected.items() if p.suffix.lower() in ('.v','.sv')}),
            'source_manifest':str(certroot/'DESIGN.json')}
        inputs.update({str(certroot/'DESIGN.json'):sha(certroot/'DESIGN.json'),str(original/'config.mk'):sha(original/'config.mk'),str(sdc):sha(sdc)})
        tasks.append({'task':task,'template':str(project),'handoff_id':hid})
    matrix={a:{b:F.N.relationship(registry[a],registry[b]) for b in registry} for a in registry}
    if any(matrix[a][b]!=('EXCLUDE' if a==b else 'KEEP') for a in registry for b in registry):
        raise ValueError('Partial/shared RTL needs an explicit source-independence review before execution')
    save(nb/'origin-registry.json',registry);save(nb/'lineage-audit.json',{'matrix':matrix,'status':'PASS','author_or_generic_module_name_not_used':True})
    platform_files=F.frozen(PLATFORM)
    external={p:h for p,h in load(OLD_NB/'registration.json')['external_hashes'].items() if '/platforms/' not in p}
    external.update(platform_files)
    capability=subprocess.run([sys.executable,str(code/'r2g-skills/signoff-loop/scripts/flow/platform_capability.py'),
        '--flow-dir',str(PLATFORM.parents[1]),'--platform','nangate45','--strict'],capture_output=True,text=True,check=True)
    save(root/'platform-capability.json',json.loads(capability.stdout))
    grant={'user_text':'FreePDK45 50design；将之前REME实验臂改为AdaMEM，适配后用于后续正式对比',
        'arms':list(ARMS),'designs':50,'episodes':50*len(ARMS),'model':'deepseek-flash',
        'provider':'https://api.deepseek.com/chat/completions','online_requests_per_episode':3,
        'no_cumulative_cost_cap':True,'request_policy':R.policy(),'implicit_retries':0,'UNKNOWN':'pause',
        'construction_input':'public R2G trajectories excluding target/same-IP before induction; no RTL/SDC/key',
        'truncated_requests':{},'amem_context_policy':R.AMEM_CONTEXT_POLICY,
        'prior_runs_untouched':True,'ReMe_build_and_run_disabled':True,'AdaMEM':ADA.CONFIG}
    save(root/'authorization.json',grant)
    for base,names in ((root,('online','construction')),(nb,('phase',))):
        for name in names:
            save(base/(name+'-budget.json'),{'budget_id':VERSION+'-'+name,'caps':{},'reserve':{},'halt_on_unknown_usage':True})
            directory=base/(name+'-phase') if base==root else base/name
            directory.mkdir();(directory/'resource_ledger.jsonl').touch()
    shutil.copy2(root/'authorization.json',nb/'authorization.json')
    save(nb/'episode-budget.json',D.episode_budget());save(nb/'online-input-authorization.json',grant)
    files={**F.frozen(nb/'code'),**F.frozen(nb/'tasks'),**external,**inputs}
    files.update({str(nb/n):sha(nb/n) for n in ('origin-registry.json','lineage-audit.json','authorization.json',
        'episode-budget.json','phase-budget.json','online-input-authorization.json')})
    nbreg={'schema':VERSION+'-baseline','status':'FROZEN_READY','split':'TEST','paper_data':True,
        'arms':['NB'],'tasks':tasks,'plan':[{'task':t['task'],'arm':'NB','repeat':1} for t in tasks],
        'files':files,'external_hashes':external,'online_policy':R.policy(),'mechanism_policy':None,
        'scheduler':{'cpu_slots':SLOTS,'concurrency':len(SLOTS),'threads_per_episode':8},
        'agent_selected_policy':D.VERSION,'online_total_request_cap':3,'launch_blockers':[],
        'phase_binding':{'budget_file':str(nb/'phase-budget.json'),'budget_sha256':sha(nb/'phase-budget.json'),
                         'ledger_path':str(nb/'phase/resource_ledger.jsonl')},
        'online_entrypoint':str(nb/'code/memory/scripts/t2_agent_request_resume.py')}
    save(nb/'registration.json',nbreg);B.preflight(nb,'TEST')
    q=load(OLD_QUEUE/'queue-registration.json');upstream={**q['external_inputs'],**adamem_sources}
    F.verify_files(upstream)
    frozen={**F.frozen(code),**F.frozen(root/'nb/tasks'),**external,**inputs,
        **{str(root/n):sha(root/n) for n in ('selection.json','authorization.json','offline-tests.xml','platform-capability.json','protocol.md','native-lvs-technical-receipt.json',
                                           'online-budget.json','construction-budget.json')},
        str(nb/'registration.json'):sha(nb/'registration.json')}
    save(root/'queue-registration.json',{'schema':VERSION,'nb':str(nb),'arms':list(ARMS[1:]),'plan_size':50*(len(ARMS)-1),
        'external_inputs':upstream,'embedding_python':q['embedding_python'],'embedding_library_path':q['embedding_library_path']})
    frozen[str(root/'queue-registration.json')]=sha(root/'queue-registration.json')
    save(root/'registration.json',{'schema':VERSION,'status':'FROZEN_READY','files':frozen,'arms':list(ARMS),
        'designs':50,'registered_episodes':50*len(ARMS),'slots':SLOTS,'created':time.time(),
        'history':'new R2G50 only, leave target/same-IP out BEFORE each representation is constructed',
        'inference_scope':'leave-one-IP-out benchmark; not an external-history sealed confirmation',
        'tehm_consumer_policy':TM0_THREE_WAY_POLICY,'online_policy':R.policy(),'AdaMEM':ADA.CONFIG,
        'runtime_base':str(BASE),'runtime_base_registration_sha256':sha(BASE/'registration.json'),
        'superseded_technical_run':'/data2/zhangdy/tehm-experiments/t2-freepdk45-main50-20261022-r1',
        'validation_boundary':'offline regression plus first REGISTERED R2G episode technical gate; no performance tuning'})
    save(root/'dispatch-state.json',{'status':'FROZEN_READY','pending':50*len(ARMS),'running':[]})
    configure(registry,SLOTS);collect(root)
    print(json.dumps({'root':str(root),'designs':50,'episodes':50*len(ARMS),'ReMe':False,'sha256':sha(root/'registration.json')}),flush=True)


def nb_gate(nb):
    state=load(nb/'dispatch-state.json');reg=load(nb/'registration.json')
    if state['status']!='COMPLETED':raise RuntimeError('R2G technical stop blocks construction')
    if len(reg['plan'])!=50 or reg['arms']!=['NB']:raise ValueError('Wrong R2G denominator')
    import t2a_runtime as T
    for row in reg['plan']:
        p=nb/'episodes'/(row['task'].replace('/','__')+'__NB__r1');e=load(p/'episode.json')
        if e['status'] not in ('ACCEPTED','NOT_ACCEPTED') or not e['accounting']['passed']:raise RuntimeError('Incomplete R2G trajectory')
        if e['accounting']['llm_requests']>3:raise ValueError('Online request limit exceeded')
        R.ledger_complete(p/'reports/resource_ledger.jsonl')
        if not T.accounting_check(F.rows(p/'reports/resource_ledger.jsonl'),list((p/'backend').glob('RUN_*')))['passed']:
            raise RuntimeError('Raw flow accounting does not reproduce')


def drain_technical_run(root):
    """Operator-only drain: do not mutate a frozen controller or interrupt tools."""
    import signal
    root=Path(root).resolve();registration=load(root/'registration.json')
    if not registration['schema'].startswith('freepdk45-main50-'):raise ValueError('Wrong technical queue')
    pid=load(root/'launch.json')['pid']
    expected='t2_freepdk45_main.py run --root '+str(root)
    command=Path('/proc/'+str(pid)+'/cmdline').read_bytes().replace(b'\0',b' ').decode()
    if expected not in command:raise RuntimeError('Supervisor identity changed')
    if Path('/proc/'+str(pid)+'/stat').read_text().split()[2]!='T':
        raise RuntimeError('Drain requires an already identity-checked stopped supervisor')
    started=list((root/'nb/episodes').glob('*/episode.json'))
    while True:
        running=[str(p) for p in started if load(p).get('status')=='RUNNING']
        save(root/'operator-technical-pause.json',{'status':'DRAINING_STARTED_EPISODES' if running else 'DRAINED',
            'started_episodes':len(started),'running_episode_records':running,
            'reason':'native LVS re-streamed GDS; provenance fault misclassified as measured failure',
            'old_raw_records_and_ledger_unchanged':True,'pending_new_launch_requires_direction':True,'updated':time.time()})
        if not running:break
        time.sleep(10)
    command=Path('/proc/'+str(pid)+'/cmdline').read_bytes().replace(b'\0',b' ').decode()
    if expected not in command:raise RuntimeError('Supervisor identity changed after drain')
    # SIGTERM is delivered as soon as CONT wakes this stopped operator-owned
    # process; Python does not dispatch another job before its default handler.
    os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
    raw=[]
    for p in started:
        e=load(p);checks=((e.get('final') or {}).get('acceptance') or {}).get('checks') or {}
        raw.append({'episode':str(p),'raw_status':e['status'],
            'reviewed_status':'INFRASTRUCTURE_ERROR' if checks.get('A11_provenance')=='fail' else e['status'],
            'paper_data':False,'cost':F.ledger_cost(p.parent/'reports/resource_ledger.jsonl')})
    save(root/'technical-incident-results.json',{'episodes':raw,'raw_records_unchanged':True,
        'interpretation':'superseded platform startup evidence, not formal mechanism performance',
        'pending_NB_not_started':50-len(started),'memory_episodes_started':0})
    save(root/'dispatch-state.json',{'status':'PAUSED_TECHNICAL_DRAINED','running':[],
        'completed_technical_episodes':len(started),'pending':registration['registered_episodes']-len(started),'updated':time.time(),
        'operator_receipt':str(root/'operator-technical-pause.json')})


def run(root,continuation=None):
    root=Path(root).resolve()
    if Path(__file__).resolve()!=root/'code/memory/scripts'/Path(__file__).name:raise ValueError('Run the frozen private controller')
    lease=(root/'supervisor.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if continuation:
        from t2_freepdk45_recovery import validate_continuation
        validate_continuation(root,continuation)
    elif (root/'launch.json').exists():raise FileExistsError('No implicit resume or rerun')
    reg=load(root/'registration.json');q=load(root/'queue-registration.json');nb=root/'nb'
    F.verify_files(reg['files']);F.verify_files(q['external_inputs'])
    if shutil.disk_usage(root).free<100*1024**3:raise RuntimeError('Need 100 GiB free to start')
    cpus=[c for slot in reg['slots'] for c in slot]
    if len(cpus)!=len(set(cpus)) or not set(cpus)<=os.sched_getaffinity(0):raise RuntimeError('Unavailable/overlapping CPU slots')
    os.environ.update(TMPDIR=str(root/'tmp'),PYTHONDONTWRITEBYTECODE='1',
        R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'])
    registry=load(nb/'origin-registry.json');configure(registry,reg['slots']);R.install_online()
    if not continuation:
        save(root/'launch.json',{'started':time.time(),'pid':os.getpid(),'registration_sha256':sha(root/'registration.json')})
    try:
        jobs=[{'root':str(nb),'task':x['task'],'arm':'NB','design':hid} for hid,x in registry.items()]
        if continuation:
            from t2_freepdk45_recovery import run_initial,never_started
            run_initial(root,continuation,reg['slots'])
            remaining=never_started(jobs)
        else:
            save(root/'dispatch-state.json',{'status':'R2G_FIRST_REGISTERED_TECHNICAL_CHECK','pending':len(ARMS)*50,'running':[]})
            first=F.dispatch(nb,jobs[:1],popen=worker_popen)
            if first['status']!='COMPLETED':raise RuntimeError('First registered R2G episode technical gate failed')
            e=load(nb/'episodes'/(jobs[0]['task'].replace('/','__')+'__NB__r1')/'episode.json')
            for verdict in (e.get('initial') or {},e.get('final') or {}):
                if verdict.get('verification_stop') in (75,76,-9,124,137) or ((verdict.get('acceptance') or {}).get('checks') or {}).get('A11_provenance')=='fail':
                    raise RuntimeError('First registered R2G episode signoff/provenance technical gate failed')
            save(root/'first-episode-integrity.json',first)
            remaining=jobs[1:]
        save(root/'dispatch-state.json',{'status':'R2G_BASELINE_RUNNING','pending':len(ARMS)*50-1,'state':str(nb/'dispatch-state.json')})
        state=F.dispatch(nb,remaining,popen=worker_popen)
        if state['status']!='COMPLETED':raise RuntimeError('R2G queue paused; no hidden retry')
        nb_gate(nb);collect(root)
        units,inputs=F.raw_units(nb,registry)
        for u in units:
            if 'episode_context' in u:
                p=nb/'episodes'/(registry[u['source_handoff_id']]['task'].replace('/','__')+'__NB__r1')
                u['episode_context']['agent_trajectory']=load(p/'episode.json').get('agent_run',{})
        save(root/'nb-terminal-receipt.json',{'units':len(units),'inputs':inputs,'episodes':50})
        folds=[]
        for hid in registry:
            save(root/'dispatch-state.json',{'status':'BUILDING_TARGET_EXCLUDED_HISTORIES','target':hid,'pending':50*(len(ARMS)-1)})
            fold=G.build_fold(root,nb,hid,units,registry,inputs)
            ADA.build(fold/'controls',fold/'shared')
            folds.append(fold)
        # Paid builders use exactly the same request-only transport as online
        # repair, with distinct construction ledgers and no implicit retries.
        pending=[(f,a) for f in folds for a in BUILDS];live=[];failure=None;done=[]
        while pending or live:
            while pending and len(live)<4 and not failure:
                fold,arm=pending.pop(0);log=(root/'logs'/('build-'+fold.name+'-'+arm+'.log')).open('x')
                cmd=[sys.executable,str(root/'code/memory/scripts'/Path(__file__).name),'construct',
                     '--root',str(root),'--controls',str(fold/'controls'),'--arm',arm]
                p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,
                    'R2G_EMBEDDING_METRICS_FILE':str(fold/'controls/construction'/arm/'reports/embedding-calls.jsonl')})
                live.append((p,log,fold,arm))
            for p,log,fold,arm in list(live):
                if p.poll() is None:continue
                log.close();live.remove((p,log,fold,arm));done.append({'target':fold.name,'arm':arm,'returncode':p.returncode})
                if p.returncode:failure=fold.name+'/'+arm+' construction technical stop'
            save(root/'dispatch-state.json',{'status':'PAUSED_DRAINING_BUILDERS' if failure else 'BUILDING_MEMORY',
                'done':done,'pending':50*(len(ARMS)-1),'construction_pending':len(pending),
                'running':[{'pid':p.pid,'target':f.name,'arm':a} for p,l,f,a in live],'reason':failure})
            if failure and not live:raise RuntimeError(failure)
            if pending or live:time.sleep(5)
        for fold in folds:
            F.seal_runtime(root,fold,nb,registry,fold.name)
            r=load(fold/'registration.json');r['files'].update(F.frozen(fold/'agent-store'))
            r.update(schema=VERSION+'-memory',paper_data=True,online_policy=R.policy(),
                tehm_consumer_policy=TM0_THREE_WAY_POLICY,online_total_request_cap=3,
                online_entrypoint=str(fold/'code/memory/scripts/t2_agent_request_resume.py'))
            save(fold/'registration.json',r)
            import t2a_batch as B
            B.preflight(fold,'TEST')
        jobs=[{'root':str(f),'task':registry[f.name]['task'],'arm':a,'design':f.name} for f in folds for a in ARMS[1:]]
        save(root/'frozen-memory-plan.json',{'jobs':jobs,'registrations':{str(f/'registration.json'):sha(f/'registration.json') for f in folds}})
        F.dispatch(root,jobs,popen=worker_popen);collect(root)
    except Exception as exc:
        save(root/'dispatch-state.json',{'status':'PAUSED_TECHNICAL','reason':str(exc),'updated':time.time()});collect(root)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('prepare','run','collect','construct','drain'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--tests',type=Path)
    parser.add_argument('--controls',type=Path);parser.add_argument('--arm')
    parser.add_argument('--continuation',type=Path);args=parser.parse_args()
    if args.command=='prepare':prepare(args.root,args.tests)
    elif args.command=='run':run(args.root,args.continuation)
    elif args.command=='collect':collect(args.root)
    elif args.command=='drain':drain_technical_run(args.root)
    else:
        if args.arm not in BUILDS:raise ValueError('Unregistered construction arm; ReMe is disabled')
        R.construct(args.root.resolve(),args.controls.resolve(),args.arm)


if __name__=='__main__':main()
