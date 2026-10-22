#!/usr/bin/env python3
"""Prepare a fresh eight-design NB stage for the nine-arm agent-selected study.

Copies only frozen code/input files, never old repaired workspaces or results.
Preparation is offline. Launch uses the already tested four-worker dispatcher.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET

REPO=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(REPO/'memory/scripts'),str(REPO/'memory')]
import t2_nb_firstlook as N
import t2_agent_dispatch as D


def replace_once(text,old,new):
    if text.count(old)!=1:raise ValueError('Unexpected frozen runtime boundary: '+old[:80])
    return text.replace(old,new,1)


def integrate_batch(text):
    start="        if not initial['accepted']:\n"
    end="        final=T.terminal_verdict(H,project,env);record['final']=final\n"
    if text.count(start)!=1 or text.count(end)!=1:raise ValueError('Episode boundary changed')
    a,b=text.index(start),text.index(end)
    text=text[:a]+(
        "        if arm != 'NB':raise RuntimeError('This first stage registers NB only')\n"
        "        if not initial['accepted']:\n"
        "            from t2_agent_runtime import run as run_agent\n"
        "            key=H.llm_key('/data1/zhangdy/.llm_env')\n"
        "            record['agent_run']=run_agent(H,project,arm,env,key,initial,mark_engineer_restore)\n"
        "            record['protocol_stop_reason']=record['agent_run']['stop_reason']\n"
        "            record['repair_turns']=[d['execution'] for d in record['agent_run']['decisions'] if 'execution' in d]\n"
    )+text[b:]
    old="'stopping_rule':'strict_acceptance_or_three_repair_generations',"
    if text.count(old)!=2:raise ValueError('Preparation/episode stopping descriptions changed')
    text=text.replace(old,"'stopping_rule':'agent_selected_with_three_total_online_requests',")
    text=replace_once(text,"        if not record['accounting']['passed']:record['status']='ACCOUNTING_FAILURE'",
        "        if record['accounting']['llm_requests']>3:\n"
        "            record['accounting']['passed']=False\n"
        "            record['accounting']['errors'].append('three total online requests exceeded')\n"
        "        if not record['accounting']['passed']:record['status']='ACCOUNTING_FAILURE'")
    text=replace_once(text,'import t2a_runtime as T','import t2a_runtime as T\nimport t2_agent_verdict as AV')
    text=replace_once(text,'final=T.terminal_verdict(H,project,env);record[\'final\']=final',
        'final=AV.terminal_verdict(H,project,env);record[\'final\']=final')
    text=replace_once(text,"record['status']=T.terminal_status(final)","record['status']=AV.terminal_status(final)")
    text=replace_once(text,"else T.terminal_reason(","else AV.terminal_reason(")
    return text


def prepare(root,base,tests):
    root,base,tests=Path(root).resolve(),Path(base).resolve(),Path(tests).resolve()
    if root.exists():raise ValueError('No overwrite of an existing package')
    suites=ET.parse(tests).getroot()
    cases=list(suites.iter('testcase'))
    if len(cases)<22 or any(c.find('failure') is not None or c.find('error') is not None
                            or c.find('skipped') is not None for c in cases):
        raise ValueError('Passing dispatch/runtime tests required')
    prior=N.load(base/'registration.json')
    if prior['schema']!='nb-firstlook8-v1' or len(prior['tasks'])!=8:
        raise ValueError('Wrong eight-design source')
    # Check pinned baseline inputs before making any copy.
    for p,want in prior['files'].items():
        if N.sha(p)!=want:raise ValueError('Original input changed: '+p)
    root.mkdir(parents=True)
    shutil.copytree(base/'code',root/'code')
    batch=root/'code/memory/scripts/t2a_batch.py'
    batch.write_text(integrate_batch(batch.read_text()))
    runtime=root/'code/memory/scripts/t2a_runtime.py'
    runtime.write_text(replace_once(runtime.read_text(),"'repair_generations':3,",
        "'repair_generations':3,'total_online_requests':3,'scheduler':'agent_selected',"))
    for name in ('t2_agent_dispatch.py','t2_agent_runtime.py','t2_agent_nb_package.py',
                 't2_agent_tools.py','t2_agent_verdict.py','t2_agent_journal.py'):
        shutil.copy2(REPO/'memory/scripts'/name,root/'code/memory/scripts'/name)
    shutil.copy2(tests,root/'offline-tests.xml')
    for name in ('origin-registry.json','lineage-audit.json','online-input-authorization.json'):
        shutil.copy2(base/name,root/name)
    tasks=[]
    for task in prior['tasks']:
        src=Path(task['template']);dst=root/'tasks'/src.name
        shutil.copytree(src,dst)
        cfg=dst/'constraints/config.mk'
        cfg.write_text(cfg.read_text().replace(str(src)+'/',str(dst)+'/'))
        man=N.load(dst/'manifest.json')
        import t2a_envelope as E
        man.update(source_project=str(dst),initial_config=E.effective_config(cfg.read_text()),
                   initial_config_sha256=N.sha(cfg))
        N.save(dst/'manifest.json',man)
        tasks.append({**task,'template':str(dst)})
    N.save(root/'episode-budget.json',D.episode_budget())
    N.save(root/'phase-budget.json',{'budget_id':'agent-selected-nine-arm-NB-stage',
        'caps':{},'reserve':{},'halt_on_unknown_usage':True})
    (root/'phase').mkdir();(root/'phase/resource_ledger.jsonl').touch(exist_ok=False)
    N.save(root/'authorization.json',{
        'user_requested':'Run the same eight designs with agent-selected tools and add ReMe and ReasoningBank.',
        'user_confirmed_online_limit':'全部在线请求合计最多 3 次',
        'stage':'fresh NB first, then target-excluded shared memory construction',
        'all_arms_planned':list(D.ARMS),'episodes_this_stage':8,'model':'deepseek-flash',
        'no_cumulative_cost_cap':True,'implicit_retries':0,
        'prior_results_preserved':True,'no_automatic_memory_arm_launch':True})
    files={str(p):N.sha(p) for p in root.rglob('*') if p.is_file()
           and 'phase' not in p.relative_to(root).parts and '__pycache__' not in p.parts}
    files.update({p:h for p,h in prior['files'].items() if not Path(p).is_relative_to(base)})
    reg={**prior,'registered_at':time.time(),'tasks':tasks,'files':files,
         'online_policy':{**prior['online_policy'],'total_online_requests':3,'scheduler':'agent_selected'},
         'paper_data':False,'agent_selected_policy':D.VERSION,
         'online_total_request_cap':3,'launch_blockers':[],
         'version_boundary':'agent-selected host; observed eight designs; revised exploratory comparison',
         'dev_validation_boundary':'OFFLINE_GATEWAY_AND_HOST_TESTS_NOT_REAL_NINE_ARM_DEV',
         'phase_binding':{'budget_file':str(root/'phase-budget.json'),
             'budget_sha256':N.sha(root/'phase-budget.json'),'ledger_path':str(root/'phase/resource_ledger.jsonl')},
         'stopping_rule':'agent_choice_or_strict_acceptance_or_three_total_online_requests',
         'runtime_delta':{'source_registration':str(base/'registration.json'),
             'source_sha256':N.sha(base/'registration.json'),
             'files':['memory/scripts/t2a_batch.py','memory/scripts/t2a_runtime.py','memory/scripts/t2_agent_dispatch.py',
                      'memory/scripts/t2_agent_runtime.py','memory/scripts/t2_agent_nb_package.py',
                      'memory/scripts/t2_agent_tools.py','memory/scripts/t2_agent_verdict.py','memory/scripts/t2_agent_journal.py']}}
    N.save(root/'registration.json',reg)
    N.save(root/'dispatch-state.json',{'status':'FROZEN_READY','pending':8,'running':[],
                                      'completed':[],'eda_flows':0,'model_requests':0})
    print(json.dumps({'status':'FROZEN_READY','root':str(root),'registration_sha256':N.sha(root/'registration.json')}))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['prepare','run'])
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--base',type=Path)
    ap.add_argument('--tests',type=Path);a=ap.parse_args()
    if a.command=='prepare':prepare(a.root,a.base,a.tests)
    else:
        reg=N.load(a.root/'registration.json')
        if reg.get('agent_selected_policy')!=D.VERSION:raise ValueError('Wrong scheduling registration')
        if Path(__file__).resolve()!=a.root/'code/memory/scripts/t2_agent_nb_package.py':
            raise ValueError('Launch the frozen dispatcher')
        N.run(a.root.resolve())


if __name__=='__main__':main()
