#!/usr/bin/env python3
"""One explicit shared-harness correction; retain completed data and all costs.

All 50 input configs are checked through native Make resolution BEFORE launch.
Known checker timeouts end an unmeasured cell, not the whole registered queue;
unexpected failures, unbound results and UNKNOWN provider usage still pause.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from t2_nb_firstlook import load,save,sha
import t2a_envelope as E

VERSION='freepdk45-shared-harness-correction-v1'
SCRIPTS=('t2_agent_request_resume.py','t2_agent_verdict.py','t2_nb_followup.py',
         't2_freepdk45_main.py','t2_signoff_integrity.py','t2_freepdk45_corrective.py',
         't2_agent_memories.py','t2a_adapters.py')


def uniform_initial(config):
    if str(config.get('CORE_UTILIZATION'))!='20':raise ValueError('Not the registered uniform 20 percent input')
    out=dict(config);out.pop('CORE_AREA',None);out.pop('DIE_AREA',None)
    return out


def integrate_batch(text):
    needle="        record['initial']=initial\n"
    if text.count(needle)!=1:raise ValueError('Unexpected initial verification boundary')
    text=text.replace(needle,needle+
        "        if initial.get('technical_signoff'):\n"
        "            raise T.TechnicalFailure('Initial signoff was not measured; no model repair of checker failure')\n",1)
    from t2_agent_nb_package import replace_once
    return replace_once(text,"    env.update(TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1')",
        "    env.update(TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1',\n"
        "               R2G_METER_STRICT_CHECKERS='1',LVS_CRASH_RETRIES='1')")


def remaining_jobs(jobs):
    import t2_freepdk45_main as M
    from t2_signoff_integrity import effective_episode
    pending=[]
    for row in jobs:
        p=Path(row['root'])/'episodes'/(row['task'].replace('/','__')+'__'+row['arm']+'__r1')
        if not p.exists():pending.append(row);continue
        if not (p/'episode.json').exists():raise ValueError('Partial episode cannot be retried')
        e=effective_episode(p)
        if e['status'] not in ('ACCEPTED','NOT_ACCEPTED') and not M.registered_checker_stop(p,e):
            raise ValueError('Unresolved episode cannot be silently skipped: '+str(p))
    return pending


def validate_activation(root,revision):
    root,revision=Path(root),Path(revision);a=load(revision/'applied.json')
    if a['root']!=str(root) or a['registration_sha256']!=sha(root/'registration.json'):
        raise ValueError('Unregistered corrective runtime')
    for p,h in a['files'].items():
        if sha(p)!=h:raise ValueError('Corrective runtime drift: '+p)
    with (revision/'activation.json').open('x') as f:
        json.dump({'pid':os.getpid(),'started':time.time(),'registration_sha256':a['registration_sha256']},f)


def passing_tests(path):
    cases=list(ET.parse(path).getroot().iter('testcase'))
    if len(cases)<30 or any(c.find(t) is not None for c in cases for t in ('failure','error')):
        raise ValueError('Passing corrective tests required')


def native_followup_ready(root, revision):
    """A measured checker verdict is required, including for a mismatching layout."""
    root,revision=Path(root),Path(revision)
    grant=load(revision/'registration.json');path=Path(grant['native_probe'])
    if not path.exists():return False
    p=load(path)
    expected=root/'nb/episodes/C01930__floppy__ao486__initial__NB__r1'
    if (p.get('source')!=str(expected) or p.get('passed') is not True
            or p.get('source_unchanged') is not True or p.get('exit_status')!=0
            or p.get('crash') or p.get('timed_out') or p.get('negative_control')
            or p.get('verdict') not in ('MATCH','MISMATCH')
            or p.get('executable_sha256')!=grant['checker_sha256']
            or p.get('model_requests')!=0 or p.get('implementation_flows')!=0):
        raise ValueError('Original-case native LVS validation did not pass')
    if p.get('native_checker_invocations')!=1 or p.get('implicit_retries')!=0:
        raise ValueError('Unexpected checker execution count')
    for name,h in p['inputs'].items():
        if sha(name)!=h:raise ValueError('Native probe input drift: '+name)
    for name in ('input.gds','input.cdl','deck.lylvs'):
        input_path=path.parent/name
        if not input_path.is_file() or sha(input_path) not in p['inputs'].values():
            raise ValueError('Native probe copy drift: '+name)
    if not (path.parent/'result.lvsdb').is_file():raise ValueError('No native LVS database')
    log=(path.parent/'checker.log').read_text(errors='replace')
    if any(s in log for s in ('Signal number','Internal error','compare raised exception','report_lvs failed')):
        raise ValueError('Native checker log contains a technical error')
    marker='CONGRATULATIONS! Netlists match.' if p['verdict']=='MATCH' else "ERROR : Netlists don't match"
    if marker not in log:raise ValueError('Native verdict and log differ')
    return True


def prepare_native_followup(root, revision, tests, probe=None, authorization=None, checker_swap=True):
    """Prepare the already-authorised shared repair while the checker runs.

    probe: the native probe receipt this revision waits for (default: the first probe's path). A new
    revision may name a second, explicitly logged probe of the SAME original case (2026-10-22: the
    first probe used 5,400 s, below this 297,153-instance design's own 28,800 s LVS tier); the first
    receipt is kept, never overwritten. authorization: the user's instruction for this revision.
    checker_swap=False (user decision 2026-10-22, after 0.30.12 also crashed on floppy): stage only the
    shared configuration fix and crash-precedence safeguards; t2a_batch.py is left as it is, so LVS
    keeps the frozen checker of every finished episode, and there is no native-probe gate. The failed
    probe receipts are bound as evidence of why."""
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    passing_tests(tests)
    snapshot=root/'lvs-config-fix-20261023/source-repair-snapshot.json';snap=load(snapshot)
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    if (sha(root/'registration.json')!=snap['root_registration_sha256']
            or sha(root/'nb/registration.json')!=snap['nb_registration_sha256']):
        raise ValueError('Repair snapshot registration changed')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    if checker_swap:M.F.verify_files(snap['checker_candidate_files'])
    M.F.verify_files(snap['receipts'])
    regression=root/'lvs-config-fix-20261023/regression.xml';passing_tests(regression)
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    stages={};checker='/data1/zhangdy/Tools/tehm-toolchain/klayout-0.30.12-isolated/klayout'
    for code in (root/'code',root/'nb/code'):
        for rel,row in snap['changes'].items():
            source=Path(row['source']);dest=code/rel
            if sha(source)!=row['source_sha256']:raise ValueError('Repair source changed: '+rel)
            prior=row['base_nb_runtime_sha256' if code==root/'nb/code' else 'base_runtime_sha256']
            if (sha(dest) if dest.exists() else None)!=prior:raise ValueError('Repair base changed: '+rel)
            staged=revision/'staged'/dest.relative_to(root);staged.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,staged)
            stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),'previous_sha256':prior}
        if not checker_swap:continue
        dest=code/'memory/scripts/t2a_batch.py';text=dest.read_text()
        needle="               R2G_METER_STRICT_CHECKERS='1',LVS_CRASH_RETRIES='1')"
        if text.count(needle)!=1:raise ValueError('Unexpected worker environment')
        text=text.replace(needle,"               R2G_METER_STRICT_CHECKERS='1',LVS_CRASH_RETRIES='1',\n"
            f"               R2G_LVS_KLAYOUT_CMD={checker!r},R2G_LVS_KLAYOUT_SHA256={sha(checker)!r})")
        compile(text,str(dest),'exec');staged=revision/'staged'/dest.relative_to(root)
        staged.parent.mkdir(parents=True,exist_ok=True);staged.write_text(text)
        stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),'previous_sha256':sha(dest)}
    reviews=followup_reviews(root,revision)
    grant={'schema':'freepdk45-native-checker-config-continuation-v1','root':str(root),
        'user_authorization':authorization or '处理 LVS 崩溃与 spi_slave 配置核验问题；监控一下，等到自动接续',
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),'files':stages,
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),
        'checker_sha256':sha(checker) if checker_swap else None,'checker_swap':checker_swap,
        'tool_files':snap['checker_candidate_files'] if checker_swap else {},
        'finished_receipts':{**snap['receipts'],str(regression):sha(regression),
                            **{v['path']:v['sha256'] for v in reviews.values()},
                            **({} if checker_swap else {str(p):sha(p) for p in
                                (root/'lvs-config-fix-20261023').glob('floppy-native-watchdog*/receipt.json')})},
        'native_probe':None if not checker_swap else str(Path(probe).resolve() if probe else
                           root/'lvs-config-fix-20261023/floppy-native-watchdog/receipt.json'),
        'preserve_completed_episodes':True,'dispatch':'never-started-only',
        'reviewed_technical_cells':['C01930','C02305'],'unchanged_online_request_cap':3,
        'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_WAITING_NATIVE_VALIDATION' if checker_swap
                                   else 'PREPARED_CONFIG_ONLY','updated':time.time()})


def followup_reviews(root, revision):
    """Acknowledge the two diagnosed closed technical cells without reusing them."""
    import t2_freepdk45_main as M
    import t2_signoff_integrity as S
    reviews={}
    for task in load(root/'nb/registration.json')['tasks']:
        p=root/'nb/episodes'/(task['task'].replace('/','__')+'__NB__r1')
        if not (p/'episode.json').exists():continue
        e=S.effective_episode(p)
        if e['status'] in ('ACCEPTED','NOT_ACCEPTED') or M.registered_checker_stop(p,e):continue
        if not e.get('accounting',{}).get('passed'):raise ValueError('Accounting unresolved: '+str(p))
        hid=task['handoff_id']
        if hid=='C01930':
            raw=load(p/'reports/lvs.json')
            if (e.get('error')!='Initial signoff was not measured; no model repair of checker failure'
                    or raw.get('status')!='crash' or raw.get('reason')!='klayout_cpp_crash'
                    or 'Signal number: 11' not in (p/'lvs/6_lvs.log').read_text(errors='replace')):
                raise ValueError('LVS technical stop differs')
            reason='Original KLayout native crash; closed unverified cell retained, no repair failure or history evidence'
        elif hid=='C02305':
            failed=[]
            for receipt in p.glob('backend/RUN_*/effective-config.json'):
                errors=load(receipt).get('errors',[])
                if any(v.get('kind')=='execution_config_mismatch' and v.get('knob')=='MAX_ROUTING_LAYER'
                       and v.get('configured')=='metal3' and (v.get('effective') or {}).get('value')=='metal10' for v in errors):
                    failed.append(receipt)
            if (len(failed)!=1 or e.get('error')!='Candidate tool was technically terminated'
                    or not (p/'reports/t2a-workspace-unsafe.json').exists()):
                raise ValueError('Configuration technical stop differs')
            reason='Platform precedence rejected the intended candidate; unsafe closed workspace is retained and never reused'
        elif known_native_lvs_crash(p,e):
            reason=('Native KLayout LVS comparer crash (signal 11) in the initial signoff; closed unverified cell '
                    'retained, no repair failure or history evidence (user decision 2026-10-22: a later LVS crash '
                    'on another design is recorded as a technical stop)')
        else:raise ValueError('Unrelated unresolved technical cell: '+str(p))
        report=revision/'outcome-reviews'/(p.name+'.json')
        records={str(v):sha(v) for v in (p/'reports').rglob('*') if v.is_file()}
        records.update({str(v):sha(v) for v in p.glob('backend/RUN_*/effective-config.json')})
        save(report,{'classification':'UNVERIFIED_OR_SAFETY_STOP','episode_sha256':sha(p/'episode.json'),
            'reports':records,'reason':reason,'not_a_measured_repair_failure':True,'history_admission':False,
            'no_workspace_reuse':True,'no_rerun':True})
        reviews[str(p)]={'path':str(report),'sha256':sha(report)}
    return reviews


def known_native_lvs_crash(p, e):
    """Initial signoff not measured because the frozen KLayout comparer crashed (signal 11)."""
    lvs=p/'reports/lvs.json';log=p/'lvs/6_lvs.log'
    if not lvs.is_file() or not log.is_file():return False
    raw=load(lvs)
    return (e.get('error')=='Initial signoff was not measured; no model repair of checker failure'
            and raw.get('status')=='crash' and raw.get('reason')=='klayout_cpp_crash'
            and 'Signal number: 11' in log.read_text(errors='replace')
            and not (p/'reports/t2a-workspace-unsafe.json').exists())


def prepare_acknowledgement(root, revision, tests, authorization):
    """Acknowledge newly closed native-checker-crash cells and resume; no runtime file changes.

    Bound to the CURRENT registrations (after an earlier installed revision), so it never reuses the
    repair snapshot. The operator is operate_native_followup with an empty file set."""
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    if not authorization:raise ValueError('A user authorization is required')
    passing_tests(tests)
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    reviews=followup_reviews(root,revision)
    if not reviews:raise ValueError('Nothing to acknowledge')
    grant={'schema':'freepdk45-technical-stop-acknowledgement-v1','root':str(root),'user_authorization':authorization,
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),'files':{},
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),'checker_sha256':None,'checker_swap':False,
        'tool_files':{},'finished_receipts':{v['path']:v['sha256'] for v in reviews.values()},'native_probe':None,
        'preserve_completed_episodes':True,'dispatch':'never-started-only',
        'reviewed_technical_cells':sorted(Path(k).name.split('__')[0] for k in reviews),
        'unchanged_online_request_cap':3,'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_ACKNOWLEDGEMENT','updated':time.time()})


MEMORY_RESUME_FILES=('t2_freepdk45_main.py','t2_agent_request_resume.py','t2_agent_new_methods.py')


def prepare_memory_resume(root, revision, tests, authorization, staged, transport=None, standing_transport=False):
    """Install the explicit malformed-output retry (user rule 2026-10-23: 'One explicit retry')
    and resume paid construction with resume-memory; NB, histories and FROZEN builds are reused."""
    root,revision,staged=Path(root).resolve(),Path(revision).resolve(),Path(staged).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    if not authorization:raise ValueError('A user authorization is required')
    passing_tests(tests)
    if load(root/'dispatch-state.json').get('status')!='PAUSED_TECHNICAL':raise ValueError('Queue not technically paused')
    import t2_freepdk45_main as M
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    retries={}
    for response,row in (load(transport) if transport else {}).items():
        response=Path(response).resolve();request=Path(str(response).replace('.try0.response.json','.try0.request.json'))
        auth=Path(row['occupancy_authorization']);grant_sha=sha(auth);a=load(auth)
        if a.get('user_approved') is not True or not any(t['hold_id']==row['hold_id'] for t in a['targets']):
            raise ValueError('Transport retry needs the user-approved occupancy of its hold')
        ev=[json.loads(l) for l in (response.parent/'reports/resource_ledger.jsonl').read_text().splitlines() if l]
        if not any(e.get('type')=='budget_unknown_occupancy' and e.get('hold_id')==row['hold_id'] for e in ev):
            raise ValueError('Occupancy not recorded for '+row['hold_id'])
        retries[str(response)]={'hold_id':row['hold_id'],'response_sha256':sha(response),'request_sha256':sha(request),
            'occupancy_authorization':str(auth),'occupancy_authorization_sha256':grant_sha}
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    stages={}
    for name in MEMORY_RESUME_FILES:
        dest=root/'code/memory/scripts'/name;src=staged/name
        compile(src.read_text(),str(src),'exec')
        out=revision/'staged'/dest.relative_to(root);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
        stages[str(dest)]={'staged':str(out),'sha256':sha(out),'previous_sha256':sha(dest)}
    grant={'schema':'freepdk45-memory-resume-malformed-retry-v1','root':str(root),'user_authorization':authorization,
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),'files':stages,
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),'checker_sha256':None,'checker_swap':False,
        'tool_files':{},'finished_receipts':{},'native_probe':None,'activation_command':'resume-memory',
        'malformed_output_retry':'explicit-malformed-output-retry-1-v1','transport_retries':retries,
        **({'standing_transport_rule':'construction-no-response-occupy-and-retry-once-v1'} if standing_transport else {}),
        'malformed_output_retry_scope':'ReasoningBank item parse errors; A-MEM context type errors; one re-send of the same prompt; strict parsers unchanged; malformed responses and all costs retained',
        'preserve_completed_episodes':True,'reuse_frozen_builds':True,'no_NB_rerun':True,'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_MEMORY_RESUME','updated':time.time()})


def prepare_online_resume(root, revision, tests, authorization, staged):
    """Install the user's standing LVS-crash rule for repair episodes (2026-10-23, after C00517) and
    resume the drained online phase with resume-online. Only the controller changes; folds, frozen
    memories, NB and every started episode are reused, and nothing is rerun."""
    root,revision,staged=Path(root).resolve(),Path(revision).resolve(),Path(staged).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    if not authorization:raise ValueError('A user authorization is required')
    passing_tests(tests)
    state=load(root/'dispatch-state.json')
    if state.get('status')!='PAUSED' or state.get('running'):raise ValueError('Online queue not drained and paused')
    import t2_freepdk45_main as M
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    dest=root/'code/memory/scripts/t2_freepdk45_main.py';src=staged/'t2_freepdk45_main.py'
    compile(src.read_text(),str(src),'exec')
    out=revision/'staged'/dest.relative_to(root);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
    grant={'schema':'freepdk45-online-resume-standing-lvs-crash-v1','root':str(root),'user_authorization':authorization,
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),
        'files':{str(dest):{'staged':str(out),'sha256':sha(out),'previous_sha256':sha(dest)}},
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),'checker_sha256':None,'checker_swap':False,
        'tool_files':{},'finished_receipts':{},'native_probe':None,'activation_command':'resume-online',
        'standing_lvs_crash_rule':'repair-native-lvs-crash-technical-cell-v1',
        'paused_reason':state.get('reason'),'preserve_completed_episodes':True,'reuse_frozen_builds':True,
        'no_NB_rerun':True,'no_episode_rerun':True,'unchanged_online_request_cap':3,'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_ONLINE_RESUME','updated':time.time()})


GATE_ERROR='provider identity/input/usage violation'


def prepare_online_gate_resume(root, revision, tests, authorization, staged):
    """r15 (user, 2026-10-23, after C00584 AMEM): align the online accounting gate with the registered
    request-count-only policy (input_bytes_ceiling None), re-derive the paused episode(s) whose only
    accounting error was the legacy 96,000-byte check, and resume with resume-online. The r14 standing
    LVS-crash rule stays active. Folds, frozen memories, NB and every other episode are unchanged."""
    root,revision,staged=Path(root).resolve(),Path(revision).resolve(),Path(staged).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    if not authorization:raise ValueError('A user authorization is required')
    passing_tests(tests)
    state=load(root/'dispatch-state.json')
    if state.get('status')!='PAUSED' or state.get('running'):raise ValueError('Online queue not drained and paused')
    import t2_freepdk45_main as M
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    rederive=[]
    for p in sorted((root/'folds').glob('*/episodes/*/episode.json')):
        e=load(p)
        if e.get('status')!='ACCOUNTING_FAILURE':continue
        if e.get('accounting',{}).get('errors')!=[GATE_ERROR]:raise ValueError('Other accounting failure: '+str(p))
        rederive.append({'episode':str(p.parent),'episode_sha256':sha(p)})
    if not rederive or not str(state.get('reason')).endswith(Path(rederive[0]['episode']).name):
        raise ValueError('Paused episode is not the byte-gate accounting failure')
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    files={}
    for name in ('t2_freepdk45_main.py','t2_freepdk45_online_gate.py'):
        dest=root/'code/memory/scripts'/name;src=staged/name
        compile(src.read_text(),str(src),'exec')
        out=revision/'staged'/dest.relative_to(root);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
        files[str(dest)]={'staged':str(out),'sha256':sha(out),'previous_sha256':sha(dest) if dest.exists() else None}
    grant={'schema':'freepdk45-online-resume-input-gate-v1','root':str(root),'user_authorization':authorization,
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),'files':files,
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),'checker_sha256':None,'checker_swap':False,
        'tool_files':{},'finished_receipts':{},'native_probe':None,'activation_command':'resume-online',
        'standing_lvs_crash_rule':'repair-native-lvs-crash-technical-cell-v1',
        'input_gate_rule':'registered-request-count-only-input-gate-v1','rederive_episodes':rederive,
        'paused_reason':state.get('reason'),'preserve_completed_episodes':True,'reuse_frozen_builds':True,
        'no_NB_rerun':True,'no_episode_rerun':True,'unchanged_online_request_cap':3,'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_ONLINE_GATE_RESUME','updated':time.time()})


LVS_INTERNAL_SIGNATURE=('dbNetlistCompareCore.cc:1003 bt_count != failed_match was not true',
                        'dbLayoutVsSchematicWriter.cc:151 i != net2id.end () was not true')


def known_native_lvs_internal_error(p, e):
    """Initial signoff not measured because the frozen KLayout comparer hit its internal assertion and
    then crashed in the LVS database writer (C00651, 2026-10-24); DRC unmeasured (124) or clean."""
    lvs,drc,log=p/'reports/lvs.json',p/'reports/drc.json',p/'lvs/6_lvs.log'
    if not lvs.is_file() or not drc.is_file() or not log.is_file():return False
    raw,d,text=load(lvs),load(drc),log.read_text(errors='replace');a=e.get('accounting') or {}
    return (e.get('status')=='UNVERIFIED_OR_SAFETY_STOP'
            and e.get('error')=='Initial signoff was not measured; no model repair of checker failure'
            and raw.get('status')=='crash' and raw.get('reason')=='lvs_writer_crash_after_match'
            and all(s in text for s in LVS_INTERNAL_SIGNATURE) and 'Signal number' not in text
            and (d.get('exit_code')==124 or d.get('status')=='clean')
            and a.get('passed') is True and a.get('llm_requests')==0
            and not (p/'reports/t2a-workspace-unsafe.json').exists())


def prepare_online_standing_resume(root, revision, tests, authorization, staged):
    """r16 (user, 2026-10-24, after C00651: 'Extend standing rule'): repair episodes whose initial
    signoff has no LVS verdict because of the frozen KLayout comparer's internal error are recorded
    like the r14 cells. Only the controller changes; the r14 rule and the r15 input gate stay active."""
    root,revision,staged=Path(root).resolve(),Path(revision).resolve(),Path(staged).resolve()
    if revision.exists():raise FileExistsError('No revision overwrite')
    if not authorization:raise ValueError('A user authorization is required')
    passing_tests(tests)
    state=load(root/'dispatch-state.json')
    if state.get('status')!='PAUSED' or state.get('running'):raise ValueError('Online queue not drained and paused')
    import t2_freepdk45_main as M
    reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
    cells=[]
    for p in sorted((root/'folds').glob('*/episodes/*/episode.json')):
        e=load(p)
        if e.get('status')=='ACCOUNTING_FAILURE':raise ValueError('Accounting failure outstanding: '+str(p))
        if known_native_lvs_internal_error(p.parent,e):cells.append(p.parent.name)
    if not cells or not any(str(state.get('reason')).endswith(c) for c in cells):
        raise ValueError('Paused episode is not the LVS internal-error stop')
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    dest=root/'code/memory/scripts/t2_freepdk45_main.py';src=staged/'t2_freepdk45_main.py'
    compile(src.read_text(),str(src),'exec')
    out=revision/'staged'/dest.relative_to(root);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
    grant={'schema':'freepdk45-online-resume-standing-lvs-internal-v1','root':str(root),'user_authorization':authorization,
        'operator_sha256':sha(__file__),'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),
        'files':{str(dest):{'staged':str(out),'sha256':sha(out),'previous_sha256':sha(dest)}},
        'offline_tests_sha256':sha(revision/'offline-tests.xml'),'checker_sha256':None,'checker_swap':False,
        'tool_files':{},'finished_receipts':{},'native_probe':None,'activation_command':'resume-online',
        'standing_lvs_crash_rule':'repair-native-lvs-crash-technical-cell-v1',
        'standing_lvs_internal_rule':'repair-native-lvs-internal-error-technical-cell-v1',
        'input_gate_rule':'registered-request-count-only-input-gate-v1','rederive_episodes':[],
        'lvs_internal_cells_at_prepare':cells,
        'paused_reason':state.get('reason'),'preserve_completed_episodes':True,'reuse_frozen_builds':True,
        'no_NB_rerun':True,'no_episode_rerun':True,'unchanged_online_request_cap':3,'created':time.time()}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_ONLINE_STANDING_RESUME','updated':time.time()})


def operate_native_followup(root, revision):
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve();grant=load(revision/'registration.json')
    try:
        if grant['root']!=str(root) or grant['operator_sha256']!=sha(__file__):raise ValueError('Operator drift')
        if grant.get('checker_swap',True) is not False and grant.get('native_probe') is None:
            raise ValueError('Checker swap without a native probe')
        while grant.get('checker_swap',True) is not False and not native_followup_ready(root,revision):
            save(revision/'operator.json',{'status':'WAITING_NATIVE_VALIDATION','updated':time.time(),
                'probe':grant['native_probe']});time.sleep(15)
        lease=(root/'supervisor.lock').open('a')
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            reg,nbreg=load(root/'registration.json'),load(root/'nb/registration.json')
            if (sha(root/'registration.json')!=grant['base_registration_sha256']
                    or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']):
                raise ValueError('Registration changed during validation')
            for p in [*(root/'nb/episodes').glob('*/episode.json'),*(root/'folds').glob('*/episodes/*/episode.json')]:
                if load(p).get('status')=='RUNNING':raise ValueError('A worker is still active')
            M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
            M.F.verify_files(grant['tool_files']);M.F.verify_files(grant['finished_receipts'])
            if sha(revision/'offline-tests.xml')!=grant['offline_tests_sha256']:raise ValueError('Test receipt drift')
            passing_tests(revision/'offline-tests.xml')
            for dest,row in grant['files'].items():
                if (sha(dest) if Path(dest).exists() else None)!=row['previous_sha256'] or sha(row['staged'])!=row['sha256']:
                    raise ValueError('Runtime delta drift: '+dest)
            reviews=followup_reviews(root,revision)
            preserved={str(p):sha(p) for p in (root/'nb/episodes').glob('*/episode.json')}
            save(revision/'preserved-episodes.json',preserved)
            for rel in ('registration.json','nb/registration.json','dispatch-state.json','nb/dispatch-state.json'):
                out=revision/'original'/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,out)
            for dest,row in grant['files'].items():
                p=Path(dest)
                if p.exists():
                    old=revision/'original'/p.relative_to(root);old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,old)
            try:install_followup(root,revision,grant,reg,nbreg,reviews,preserved)
            except BaseException:
                rollback_followup(root,revision,grant);raise
        finally:fcntl.flock(lease,fcntl.LOCK_UN);lease.close()
        command=grant.get('activation_command','run')
        if command not in ('run','resume-memory','resume-online'):raise ValueError('Unregistered activation command')
        os.execv(sys.executable,[sys.executable,str(root/'code/memory/scripts/t2_freepdk45_main.py'),
            command,'--root',str(root),'--correction',str(revision)])
    except Exception as exc:
        save(revision/'operator.json',{'status':'BLOCKED_VALIDATION_OR_INTEGRITY','reason':str(exc),'updated':time.time()})
        raise


def rollback_followup(root, revision, grant):
    """Undo a partial install (2026-10-22: a KeyError after the nb registration and runtime files
    were written). Restores the archived originals and removes files the revision added."""
    for dest,row in grant['files'].items():
        p=Path(dest);old=revision/'original'/p.relative_to(root)
        if row['previous_sha256'] is None:
            if p.exists():p.unlink()
        else:shutil.copy2(old,p)
    for rel in ('registration.json','nb/registration.json'):shutil.copy2(revision/'original'/rel,root/rel)
    if (sha(root/'registration.json')!=grant['base_registration_sha256']
            or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']
            or any((sha(p) if Path(p).exists() else None)!=r['previous_sha256'] for p,r in grant['files'].items())):
        raise RuntimeError('Rollback did not restore the base runtime')
    save(revision/'rolled-back.json',{'rolled_back':time.time(),'verified':True})


def install_followup(root, revision, grant, reg, nbreg, reviews, preserved):
    import t2_freepdk45_main as M
    for dest,row in grant['files'].items():shutil.copy2(row['staged'],Path(dest))
    changes={p:v['sha256'] for p,v in grant['files'].items()}
    evidence={**grant['tool_files'],str(revision/'registration.json'):sha(revision/'registration.json'),
        str(revision/'offline-tests.xml'):sha(revision/'offline-tests.xml'),
        **({grant['native_probe']:sha(grant['native_probe'])} if grant.get('native_probe') else {})}
    nbreg.setdefault('acknowledged_technical_stops',{}).update(reviews)
    nbreg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'nb/code')})
    nbreg['files'].update({**evidence,**{v['path']:v['sha256'] for v in reviews.values()}})
    nbreg.setdefault('external_hashes',{}).update(grant['tool_files'])
    nbreg['previous_corrective_revision']=nbreg.get('shared_corrective_revision')
    nbreg['shared_corrective_revision']={'path':str(revision),'sha256':sha(revision/'registration.json')}
    save(root/'nb/registration.json',nbreg)
    reg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'code')})
    reg['files'].update(evidence);reg['files'][str(root/'nb/registration.json')]=sha(root/'nb/registration.json')
    reg.setdefault('external_hashes',{}).update(grant['tool_files'])
    reg['previous_corrective_revision']=reg.get('shared_corrective_revision')
    reg['shared_corrective_revision']=nbreg['shared_corrective_revision'];save(root/'registration.json',reg)
    M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files']);M.F.verify_files(preserved)
    save(revision/'applied.json',{'root':str(root),'applied':time.time(),'registration_sha256':sha(root/'registration.json'),
        'files':{**changes,**evidence},'known_unmeasured_reviews':reviews,'raw_episodes_preserved':True})
    save(revision/'operator.json',{'status':'INSTALLED_VERIFIED_ACTIVATING','updated':time.time()})


def stage_runtime(root,revision):
    stages={}
    for base in (root/'code/memory/scripts',root/'nb/code/memory/scripts'):
        for name in (*SCRIPTS,'t2a_batch.py'):
            dest=base/name
            content=integrate_batch(dest.read_text()) if name=='t2a_batch.py' else (Path(__file__).parent/name).read_text()
            compile(content,str(dest),'exec')
            staged=revision/'staged'/dest.relative_to(root);staged.parent.mkdir(parents=True,exist_ok=True);staged.write_text(content)
            stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),
                               'previous_sha256':sha(dest) if dest.exists() else None}
    repo=Path(__file__).resolve().parents[2]
    for code in (root/'code',root/'nb/code'):
        for name in ('run_strict_signoff.sh','meter_checker.py'):
            rel=Path('r2g-skills/signoff-loop/scripts/flow')/name;dest=code/rel
            source=repo/rel;staged=revision/'staged'/dest.relative_to(root)
            staged.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,staged)
            stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),
                               'previous_sha256':sha(dest) if dest.exists() else None}
    return stages


def refresh(root,revision,tests):
    """Update a NEVER-LAUNCHED preparation; retain its old code and receipts.

    Native Make input receipts can be reused only if every task and receipt
    remains byte-identical. No activation or live worker is ever refreshed.
    """
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if (revision/'applied.json').exists() or (revision/'activation.json').exists():
        raise ValueError('Cannot refresh an installed/activated revision')
    if load(revision/'operator.json')['status']!='PREPARED_VERIFIED':
        raise ValueError('Only an unlaunched preparation may be refreshed')
    passing_tests(tests);grant=load(revision/'registration.json')
    if grant['root']!=str(root) or sha(root/'registration.json')!=grant['base_registration_sha256'] or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']:
        raise ValueError('Live registration changed')
    M.F.verify_files(load(root/'registration.json')['files'])
    M.F.verify_files(load(root/'nb/registration.json')['files'])
    for file,key in [('offline-tests.xml','offline_tests_sha256'),('input-audit.json','input_audit_sha256'),
                     ('new-baseline-definition.json','baseline_definition_sha256')]:
        if sha(revision/file)!=grant[key]:raise ValueError('Prepared evidence drift')
    M.F.verify_files(load(revision/'new-baseline-definition.json')['new_task_files'])
    for row in load(revision/'input-audit.json')['tasks']:
        if sha(row['receipt'])!=row['sha256']:raise ValueError('Input audit receipt drift')
    for dest,change in grant['files'].items():
        if (sha(dest) if Path(dest).exists() else None)!=change['previous_sha256'] or sha(change['staged'])!=change['sha256']:
            raise ValueError('Prepared runtime drift')
    oldsha=sha(revision/'registration.json');archive=revision/'prior-preparations'/oldsha
    archive.mkdir(parents=True,exist_ok=False)
    for name in ('registration.json','operator.json','offline-tests.xml'):
        shutil.copy2(revision/name,archive/name)
    shutil.copytree(revision/'staged',archive/'staged')
    grant['files']=stage_runtime(root,revision)
    shutil.copy2(tests,revision/'offline-tests.xml')
    grant.update(operator_sha256=sha(__file__),offline_tests_sha256=sha(revision/'offline-tests.xml'),
                 previous_preparation_sha256=oldsha,refreshed=time.time(),
                 arm_review_scope='R2G,RET,RECIPE,EXPEL,AWM,AMEM,ADAMEM,REASONINGBANK,TM0; offline tests, not new real-run validation')
    save(revision/'registration.json',grant)


def prepare(root,revision,tests):
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if revision.exists():raise FileExistsError('No overwrite of a corrective revision')
    passing_tests(tests)
    original=load(root/'registration.json');nbreg=load(root/'nb/registration.json')
    M.F.verify_files(original['files']);M.F.verify_files(nbreg['files'])
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    reg=copy.deepcopy(nbreg);registry=load(root/'nb/origin-registry.json');edits=[];new_files={};startups={}
    for task in reg['tasks']:
        old=Path(task['template']);cfg=E.effective_config((old/'constraints/config.mk').read_text())
        normalized=uniform_initial(cfg);hid=task['handoff_id']
        if normalized==cfg:continue
        current=root/'nb/episodes'/(task['task'].replace('/','__')+'__NB__r1')
        if current.exists():
            e=load(current/'episode.json')
            if e.get('error')!='Initial flow safety/accounting stop: 76' or e.get('accounting',{}).get('llm_requests')!=0:
                raise ValueError('Do not redefine a measured/running task: '+hid)
            startups[hid]=str(current)
        new=old.with_name(old.name+'_util20')
        if new.exists():raise FileExistsError('Corrected template already exists')
        shutil.copytree(old,new)
        normalized={k:v.replace(str(old)+'/',str(new)+'/') for k,v in normalized.items()}
        (new/'constraints/config.mk').write_text(''.join('export '+k+' = '+v+'\n' for k,v in normalized.items()))
        man=load(new/'manifest.json');old_task=task['task'];new_task=old_task.rsplit('/',1)[0]+'/initial_util20'
        man.update(task=new_task,source_project=str(new),initial_config=normalized,
                   initial_config_sha256=sha(new/'constraints/config.mk'))
        save(new/'manifest.json',man)
        task.update(task=new_task,template=str(new));registry[hid]['task']=new_task
        reg['plan']=[{**p,'task':new_task} if p['task']==old_task else p for p in reg['plan']]
        new_files.update(M.F.frozen(new))
        edits.append({'design':hid,'old_task':old_task,'new_task':new_task,'old_template':str(old),
            'new_template':str(new),'only_scientific_knobs_removed':{k:cfg[k] for k in ('CORE_AREA','DIE_AREA') if k in cfg},
            'clock_sha256_unchanged':sha(old/'constraints/constraint.sdc')==sha(new/'constraints/constraint.sdc'),
            'startup_episode_retained':str(current) if current.exists() else None})
    audit=[]
    flow=Path('/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow')
    resolver=root/'nb/code/r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
    for task in reg['tasks']:
        project=Path(task['template']);man=load(project/'manifest.json');cfg=E.effective_config((project/'constraints/config.mk').read_text())
        check=E.check_config(man,cfg,env={},project=project)
        if not check['ok']:raise ValueError('Static input contract failed: '+task['task']+': '+str(check['violations']))
        out=revision/'input-audit'/task['handoff_id'];out.mkdir(parents=True)
        env=dict(os.environ)
        for k in set(man['initial_config'])|set(man['allowlist'])|set(man['immutable']['knobs']):env.pop(k,None)
        env.update(R2G_TASK_MANIFEST=str(project/'manifest.json'),PYTHONDONTWRITEBYTECODE='1')
        cmd=[sys.executable,str(resolver),'--project',str(project),'--backend',str(out),'--flow',str(flow),
             '--design-config',str(project/'constraints/config.mk'),'--variant',project.name]
        p=subprocess.run(cmd,env=env,cwd=flow,capture_output=True,text=True,timeout=90)
        if p.returncode:raise ValueError('Native Make input audit failed: '+task['task']+' '+p.stderr[-500:])
        receipt=out/'effective-config.json'
        audit.append({'design':task['handoff_id'],'status':load(receipt)['status'],'receipt':str(receipt),'sha256':sha(receipt)})
    save(revision/'input-audit.json',{'status':'PASS','tasks':audit,'EDA_flows':0,'model_requests':0})
    stages=stage_runtime(root,revision)
    save(revision/'new-baseline-definition.json',{'registration':reg,'registry':registry,
         'new_task_files':new_files,'task_definition_corrections':edits,'technical_startup_episodes':startups})
    grant={'schema':VERSION,'root':str(root),'created':time.time(),
        'user_text':'你再修复下，然后仔细检查下看看还有哪里可能会出现问题，一起优化下，然后继续正式实验',
        'base_registration_sha256':sha(root/'registration.json'),'base_nb_registration_sha256':sha(root/'nb/registration.json'),
        'operator_sha256':sha(__file__),'files':stages,'offline_tests_sha256':sha(revision/'offline-tests.xml'),
        'input_audit_sha256':sha(revision/'input-audit.json'),
        'baseline_definition_sha256':sha(revision/'new-baseline-definition.json'),
        'scientific_boundaries':{'tasks':50,'arms':9,'clock_ns':5,'initial_utilization_percent':20,
            'online_requests_per_episode':3,'area_caps':[.55,1.,1.],'cumulative_cost_cap':None,
            'native_full_DRC_deck_unchanged':True,'existing_outcomes_not_repeated':True},
        'technical_timeout_policy':'end one unmeasured cell, retain all 450 denominators and cost; no positive/negative repair evidence',
        'unexpected_errors_accounting_and_UNKNOWN_provider_usage':'pause queue, no implicit retry',
        'history_policy':'identically exclude non-measured technical outcomes from ALL memory constructions; retain measured failures',
        'original_raw_records_unchanged':True,'prepare_wall_safety':'no EDA or model calls'}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_VERIFIED','tasks_audited':len(audit),'task_definitions_corrected':len(edits)})


def operate(root,revision):
    import t2_freepdk45_main as M
    import t2_signoff_integrity as S
    root,revision=Path(root).resolve(),Path(revision).resolve();grant=load(revision/'registration.json')
    if grant['root']!=str(root) or grant['operator_sha256']!=sha(__file__):raise ValueError('Operator identity drift')
    lease=(root/'supervisor.lock').open('a')
    while True:
        running=[str(p) for p in (root/'nb/episodes').glob('*/episode.json') if load(p).get('status')=='RUNNING']
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
        except BlockingIOError:locked=False
        if locked and not running:break
        if locked:fcntl.flock(lease,fcntl.LOCK_UN)
        save(revision/'operator.json',{'status':'WAITING_FOR_STARTED_WORKERS','running':running,'updated':time.time()})
        time.sleep(15)
    try:
        reg=load(root/'registration.json');nbreg=load(root/'nb/registration.json')
        if sha(root/'registration.json')!=grant['base_registration_sha256'] or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']:
            raise ValueError('Base changed before corrective installation')
        M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
        for file,key in [('offline-tests.xml','offline_tests_sha256'),('input-audit.json','input_audit_sha256'),
                         ('new-baseline-definition.json','baseline_definition_sha256')]:
            if sha(revision/file)!=grant[key]:raise ValueError('Corrective evidence drift')
        definition=load(revision/'new-baseline-definition.json');M.F.verify_files(definition['new_task_files'])
        for rel in ('registration.json','nb/registration.json','nb/origin-registry.json','dispatch-state.json','nb/dispatch-state.json'):
            dest=revision/'original'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,dest)
        reviews={}
        for task in definition['registration']['tasks']:
            p=root/'nb/episodes'/(task['task'].replace('/','__')+'__NB__r1')
            if not (p/'episode.json').exists():continue
            e=S.effective_episode(p)
            if e['status'] in ('ACCEPTED','NOT_ACCEPTED'):continue
            verdict=e.get('reviewed_verdict') or {};technical=verdict.get('technical_signoff') or {}
            drc=p/'reports/drc.json';d=load(drc) if drc.exists() else {}
            checks=(verdict.get('acceptance') or {}).get('checks') or {}
            if (not technical or d.get('exit_code')!=124 or d.get('run_tag')!=verdict.get('judged_run')
                    or checks.get('A11_provenance')!='pass' or not e.get('accounting',{}).get('passed')):
                raise ValueError('Unrelated unresolved technical cell blocks activation: '+str(p))
            report=revision/'outcome-reviews'/(p.name+'.json')
            report.parent.mkdir(parents=True,exist_ok=True)
            save(report,{'classification':'UNVERIFIED_OR_SAFETY_STOP','raw_status':load(p/'episode.json')['status'],
                'episode_sha256':sha(p/'episode.json'),'reports':{v['report']:v['report_sha256'] for v in technical['issues']},
                'reason':'verified checker timeout, including the legacy aggregate-deadline side effects',
                'not_a_measured_repair_failure':True,'history_admission':False})
            reviews[str(p)]={'path':str(report),'sha256':sha(report)}
        for dest,change in grant['files'].items():
            path=Path(dest)
            if (sha(path) if path.exists() else None)!=change['previous_sha256'] or sha(change['staged'])!=change['sha256']:
                raise ValueError('Runtime delta drift: '+dest)
        for dest,change in grant['files'].items():
            path=Path(dest)
            if path.exists():
                old=revision/'original'/path.relative_to(root);old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,old)
            shutil.copy2(change['staged'],path)
        changes={p:d['sha256'] for p,d in grant['files'].items()}
        save(root/'nb/origin-registry.json',definition['registry'])
        updated=definition['registration'];updated['acknowledged_technical_stops']=reviews
        updated['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'nb/code')})
        updated['files'].update(definition['new_task_files'])
        updated['files'][str(root/'nb/origin-registry.json')]=sha(root/'nb/origin-registry.json')
        updated['files'].update({v['path']:v['sha256'] for v in reviews.values()})
        updated['shared_corrective_revision']={'path':str(revision),'sha256':sha(revision/'registration.json')}
        save(root/'nb/registration.json',updated)
        reg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'code')})
        reg['files'].update(definition['new_task_files']);reg['files'][str(root/'nb/registration.json')]=sha(root/'nb/registration.json')
        reg['technical_startup_episodes']=definition['technical_startup_episodes'];reg['shared_corrective_revision']=updated['shared_corrective_revision']
        save(root/'registration.json',reg)
        M.F.verify_files(reg['files']);M.F.verify_files(updated['files'])
        save(revision/'applied.json',{'root':str(root),'applied':time.time(),'registration_sha256':sha(root/'registration.json'),
             'files':changes,'known_unmeasured_reviews':reviews,'raw_episodes_preserved':True})
        save(revision/'operator.json',{'status':'INSTALLED_VERIFIED_ACTIVATING','updated':time.time()})
    finally:fcntl.flock(lease,fcntl.LOCK_UN);lease.close()
    os.execv(sys.executable,[sys.executable,str(root/'code/memory/scripts/t2_freepdk45_main.py'),
        'run','--root',str(root),'--correction',str(revision)])


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','refresh','operate','prepare-native-followup','operate-native-followup','prepare-acknowledgement','prepare-memory-resume','prepare-online-resume','prepare-online-gate-resume','prepare-online-standing-resume'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--revision',type=Path,required=True)
    ap.add_argument('--tests',type=Path);ap.add_argument('--probe',type=Path);ap.add_argument('--authorization')
    ap.add_argument('--no-checker-swap',action='store_true');ap.add_argument('--staged',type=Path)
    ap.add_argument('--transport-retries',type=Path);ap.add_argument('--standing-transport-rule',action='store_true')
    a=ap.parse_args()
    if a.command=='prepare-memory-resume':
        prepare_memory_resume(a.root,a.revision,a.tests,a.authorization,a.staged,a.transport_retries,a.standing_transport_rule);return
    if a.command=='prepare-online-resume':
        prepare_online_resume(a.root,a.revision,a.tests,a.authorization,a.staged);return
    if a.command=='prepare-online-gate-resume':
        prepare_online_gate_resume(a.root,a.revision,a.tests,a.authorization,a.staged);return
    if a.command=='prepare-online-standing-resume':
        prepare_online_standing_resume(a.root,a.revision,a.tests,a.authorization,a.staged);return
    if a.command=='prepare-acknowledgement':prepare_acknowledgement(a.root,a.revision,a.tests,a.authorization)
    elif a.command=='prepare-native-followup':
        prepare_native_followup(a.root,a.revision,a.tests,a.probe,a.authorization,not a.no_checker_swap)
    elif a.command=='operate-native-followup':operate_native_followup(a.root,a.revision)
    elif a.command=='prepare':prepare(a.root,a.revision,a.tests)
    elif a.command=='refresh':refresh(a.root,a.revision,a.tests)
    else:operate(a.root,a.revision)


if __name__=='__main__':main()
