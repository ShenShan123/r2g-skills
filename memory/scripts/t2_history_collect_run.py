#!/usr/bin/env python3
"""Execute only the hash-authorized, deterministic T2 historical collection.

Separate prepare/run commands; immutable code/input receipts, single dispatcher,
no resume/retry, no memory/model phase, and a mandatory first-episode chain gate.
The six repair-flow allowance is additional to the once-billed exploration flow,
as specified by the JSON contract's billing.reuse and total (15 + 10*6 <= 80).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import t2_history_collection as HC

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'memory'))
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
AUTH_HASH = 'c3f9af417d8526938da1d5feb0e3b32d93e0495afb1a1d6b5233bbaf1f526dfd'


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def authorized(contract, auth):
    c, a = load(contract), load(auth)
    if HC.sha256(contract) != AUTH_HASH or a.get('contract_sha256') != AUTH_HASH:
        raise ValueError('contract hash is not the explicitly authorized hash')
    if a.get('user_approved') is not True or HC.validate(c):
        raise ValueError('missing authorization or invalid contract')
    if a.get('designs') != [d['design'] for d in c['designs']]:
        raise ValueError('authorized design list differs')
    required = ('flows_total', 'repair_episodes', 'flows_per_episode', 'llm_requests')
    if any(a.get('caps', {}).get(k) != c['caps'][k] for k in required):
        raise ValueError('authorized caps differ')
    if a.get('automatic_expansion') is not False or a.get('formal_table2_launch') is not False:
        raise ValueError('scope must exclude automatic expansion and formal dispatch')
    return c


def hashes(root):
    return {str(p.relative_to(root)): HC.sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def immutable_ok(root):
    receipt = load(root / 'freeze.json')
    if hashes(root / 'code') != receipt['code_hashes']:
        raise RuntimeError('snapshot runtime changed')
    if hashes(root / 'inputs') != receipt['input_hashes']:
        raise RuntimeError('snapshot input changed')
    if hashes(root / 'reference') != receipt['reference_hashes']:
        raise RuntimeError('reference evidence changed')
    for p, sha in receipt['tracked_stores'].items():
        if HC.sha256(p) != sha:
            raise RuntimeError('tracked store changed: ' + p)
    authorized(root / 'contract.json', root / 'authorization.json')
    if HC.sha256(root / 'authorization.json') != receipt['authorization_sha256']:
        raise RuntimeError('authorization changed')
    if receipt.get('continuation_sha256'):
        if HC.sha256(root / 'continuation.json') != receipt['continuation_sha256']:
            raise RuntimeError('continuation receipt changed')
        cont = load(root / 'continuation.json')
        for p, sha in cont['prior_evidence_hashes'].items():
            if HC.sha256(p) != sha:
                raise RuntimeError('prior collection evidence changed: ' + p)
        if HC.sha256(root / 'resume-authorization.json') != cont['resume_authorization_sha256']:
            raise RuntimeError('resume authorization changed')


def flow_guard(root, project):
    """Before a flow, refuse modified source/SDC or a changed SDC path."""
    immutable_ok(root)
    never_started(root, project.name)
    design = project.name.split('__')[0]
    sdc = project / 'constraints/constraint.sdc'
    if HC.sha256(sdc) != HC.sha256(root / 'inputs' / design / 'constraints/constraint.sdc'):
        raise RuntimeError('immutable shipped SDC changed before flow')
    cfg = (project / 'constraints/config.mk').read_text()
    match = re.search(r'^\s*export\s+SDC_FILE\s*=\s*(.*?)\s*$', cfg, re.M)
    if not match or match[1] != str(sdc):
        raise RuntimeError('SDC_FILE was redirected')


def project_name(design, step):
    return design + '__' + step


def prepare(root, contract, auth):
    c = authorized(contract, auth)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(contract, root / 'contract.json')
    shutil.copy2(auth, root / 'authorization.json')
    code = root / 'code'
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.git', 'tests', 'docs',
                                   'evaluation', 'upstream')
    for rel in ('memory', 'r2g-skills/signoff-loop', 'r2g-skills/def-graph', 'tools'):
        shutil.copytree(REPO / rel, code / rel, ignore=ignore)
    (code / 'memory/scripts/t2_history_flow_guard.sh').chmod(0o755)
    ref = root / 'reference'
    ref.mkdir()
    shutil.copytree(EVAL / 'p1c-export/SOURCE-v1', ref / 'SOURCE-v1')
    shutil.copy2(EVAL / 't2-candidate-inventory-20261017/t2-source-lock.json', ref / 't2-source-lock.json')
    # Canonicalize only paths; a hook with different bytes is copied as its own input.
    paths = []
    for d in c['designs']:
        base = Path(d['base'])
        cfg = (base / 'constraints/config.mk').read_text()
        rtl_matches = re.findall(r'(/[^\s\\]+\.(?:v|sv|vh|svh))', cfg)
        if not rtl_matches:
            raise ValueError('no RTL paths: ' + d['design'])
        source_dirs = sorted({str(Path(x).parent) for x in rtl_matches})
        inp = root / 'inputs' / d['design']
        shutil.copytree(base / 'constraints', inp / 'constraints')
        rewrites = {str(base) + '/': str(inp) + '/'}
        for i, source in enumerate(source_dirs):
            dst = inp / ('rtl' + str(i))
            shutil.copytree(source, dst)
            rewrites[source + '/'] = str(dst) + '/'
        hooks = re.findall(r'^\s*export\s+\w+_TCL\s*=\s*(/[^\s]+\.tcl)', cfg, re.M)
        for i, hook in enumerate(hooks):
            dst = inp / 'hooks' / (str(i) + '_' + Path(hook).name)
            dst.parent.mkdir(exist_ok=True)
            shutil.copy2(hook, dst)
            rewrites[hook] = str(dst)
        for old, new in rewrites.items():
            cfg = cfg.replace(old, new)
        (inp / 'constraints/config.mk').write_text(cfg)
        paths.append({'design': d['design'], 'source': str(base), 'rewrites': rewrites,
                      'source_sdc_sha256': HC.sha256(base / 'constraints/constraint.sdc')})
        for step in HC.LADDER:
            p = root / 'projects' / project_name(d['design'], step)
            shutil.copytree(inp / 'constraints', p / 'constraints')
            text = cfg.replace(str(inp / 'constraints') + '/', str(p / 'constraints') + '/')
            if step != 'default':
                text = re.sub(r'^\s*export\s+(DIE_AREA|CORE_AREA)\s*=.*\n', '', text, flags=re.M)
                text += '\nexport CORE_UTILIZATION = ' + step[1:] + '\n'
            (p / 'constraints/config.mk').write_text(text)
    # Mutable knowledge is never the source repository's knowledge.
    stores = {str(p): HC.sha256(p) for p in REPO.rglob('knowledge.sqlite')
              if '.git' not in p.parts}
    stores.update({str(p): HC.sha256(p) for p in REPO.rglob('heuristics.json')
                   if '.git' not in p.parts})
    freeze = {'schema': 't2-history-runtime-freeze-v1', 'contract_sha256': AUTH_HASH,
              'authorization_sha256': HC.sha256(auth), 'code_hashes': hashes(code),
              'input_hashes': hashes(root / 'inputs'), 'reference_hashes': hashes(ref), 'tracked_stores': stores,
              'initial_project_hashes': hashes(root / 'projects'),
              'paths': paths, 'bounds': HC.worst_case(c), 'paper_data': False,
              'model_requests_allowed': 0, 'formal_launch_allowed': False,
              'safety': {'ORFS_TIMEOUT_s': 5400, 'exploration_watchdog_s': 6000,
                         'repair_watchdog_s': 28800, 'threads': 8}}
    write(root / 'freeze.json', freeze)
    write(root / 'state.json', {'status': 'READY', 'records': [], 'flows_spent': 0,
                               'chain_checked': False, 'suspended': None})
    immutable_ok(root)
    return freeze


def prior_root(root):
    path = root / 'continuation.json'
    return Path(load(path)['prior_root']) if path.exists() else None


def never_started(root, name):
    prior = prior_root(root)
    if prior:
        p = prior / 'projects' / name
        if list(p.glob('backend/RUN_*')) or (p / 'reports/resource_ledger.jsonl').exists():
            raise RuntimeError('task already started on prior runtime: ' + name)


def prepare_continuation(root, prior, contract, auth, resume_auth):
    prior = prior.resolve()
    approval = load(resume_auth)
    if (approval.get('user_approved') is not True or approval.get('prior_root') != str(prior)
            or approval.get('prior_freeze_sha256') != HC.sha256(prior / 'freeze.json')
            or approval.get('contract_sha256') != AUTH_HASH
            or approval.get('never_started_only') is not True
            or approval.get('increase_caps') is not False):
        raise ValueError('missing exact continuation authority')
    old = load(prior / 'state.json')
    if old['status'] != 'PAUSED' or old.get('chain_checked') is not False:
        raise ValueError('continuation requires the failed first-gate pause')
    if (prior / 'continuation.json').exists():
        raise ValueError('no automatic repeated continuation')
    immutable_ok(prior)
    costs = accounting(prior)
    if costs['total_known'] != costs['flow_outcomes']:
        raise ValueError('prior flow accounting incomplete')
    explored = {project_name(r['design'], r['step']) for r in old['records'] if r['op'] == 'explore'}
    started = {p.parent.parent.name for p in (prior / 'projects').glob('*/backend/RUN_*')}
    # RUN path parent is backend; its parent is the project.
    if started != explored:
        raise ValueError('prior has unrecorded or missing executions')
    freeze = prepare(root, contract, auth)
    # Independent copies must retain the original RTL/SDC/hook bytes, not new inputs.
    for rel, sha in load(prior / 'freeze.json')['input_hashes'].items():
        if rel.endswith('/config.mk'):
            old_text = (prior / 'inputs' / rel).read_text().replace(str(prior), str(root))
            if old_text != (root / 'inputs' / rel).read_text():
                raise RuntimeError('input config changed across continuation')
        elif HC.sha256(root / 'inputs' / rel) != sha:
            raise RuntimeError('input bytes changed across continuation: ' + rel)
    evidence = [prior / 'freeze.json', prior / 'state.json', prior / 'contract.json',
                prior / 'first-episode-chain/reality_check.json']
    evidence += list((prior / 'projects').glob('*/reports/resource_ledger.jsonl'))
    evidence += list((prior / 'projects').glob('*/reports/fix_log.jsonl'))
    evidence += list((prior / 'projects').glob('*/exploration.json'))
    evidence += list((prior / 'projects').glob('*/repair.json'))
    cont = {'schema': 't2-history-continuation-v1', 'prior_root': str(prior),
            'prior_evidence_hashes': {str(p): HC.sha256(p) for p in evidence},
            'carried_costs': costs, 'carried_repair_episodes': sum(r['op'] == 'repair' for r in old['records']),
            'never_started_only': True, 'old_failed_episode_admitted': False,
            'caps_unchanged': True, 'resume_authorization_sha256': HC.sha256(resume_auth)}
    shutil.copy2(resume_auth, root / 'resume-authorization.json')
    write(root / 'continuation.json', cont)
    freeze['continuation_sha256'] = HC.sha256(root / 'continuation.json')
    freeze['delta_from_prior'] = {p: {'before': load(prior / 'freeze.json')['code_hashes'].get(p), 'after': sha}
                                for p, sha in freeze['code_hashes'].items()
                                if load(prior / 'freeze.json')['code_hashes'].get(p) != sha}
    write(root / 'freeze.json', freeze)
    state = load(root / 'state.json')
    state.update(records=old['records'], flows_spent=costs['total_known'],
                 chain_gate_record_offset=len(old['records']), chain_checked=False,
                 carried_from=str(prior), old_episode_history_status='EXCLUDED_CHAIN_FAILURE')
    write(root / 'state.json', state)
    immutable_ok(root)
    return cont


def environment(root, project, component):
    # Do not inherit keys, endpoints, backend selectors, scripts, or budget bypasses.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('R2G_', 'TEHM_', 'DEEPSEEK_', 'OPENAI_', 'ANTHROPIC_', 'ORFS_'))
           and k not in ('FROM_STAGE', 'PYTHONPATH') and 'API_KEY' not in k}
    skill = root / 'code/r2g-skills/signoff-loop'
    env.update(R2G_MEMORY_BACKEND='none', R2G_MEMORY_READ_ONLY_EVAL='1',
               R2G_KNOWLEDGE_DB=str(skill / 'knowledge/knowledge.sqlite'),
               R2G_LEGACY_KNOWLEDGE_DIR=str(skill / 'knowledge'),
               R2G_JOURNAL_DB=str(root / 'journal.sqlite'),
               R2G_BUDGET_FILE=str(project / 'budget.json'), R2G_BUDGET_COMPONENT=component,
               R2G_LEDGER_PATH=str(project / 'reports/resource_ledger.jsonl'),
               R2G_HERMETIC='1', R2G_REQUIRE_LLM_BUDGET='1',
               R2G_FIX_EXCLUDE='period_relax',
               R2G_HISTORY_ROOT=str(root),
               R2G_RUN_ORFS=str(root / 'code/memory/scripts/t2_history_flow_guard.sh'),
               R2G_LOOP_RUN_FLOW=str(root / 'code/memory/scripts/t2_history_flow_guard.sh'),
               ORFS_TIMEOUT='5400', ORFS_MAX_CPUS='8', NUM_CORES='8',
               PYTHONDONTWRITEBYTECODE='1',
               LD_LIBRARY_PATH='/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib',
               PATH=str(Path(sys.executable).parent) + ':' + env.get('PATH', '/usr/bin:/bin'))
    return env


def budget(project, total):
    write(project / 'budget.json', {'budget_id': 'history-' + project.name,
          'caps': {'flows': total, 'llm_requests': 0, 'tokens_in': 0, 'tokens_out': 0,
                   'repair_attempts': 0}, 'reserve': {}, 'halt_on_unknown_usage': True})


def accounting(root):
    paths = sorted((root / 'projects').glob('*/reports/resource_ledger.jsonl'))
    prior = prior_root(root)
    if prior:
        paths += sorted((prior / 'projects').glob('*/reports/resource_ledger.jsonl'))
    totals = HC.account(paths)
    totals.update(requests=0, flow_outcomes=0, tool_call_records=0)
    for path in paths:
        for row in path.read_text().splitlines():
            e = json.loads(row)
            if e.get('type') == 'budget_charge' and e.get('resource') == 'llm_requests':
                totals['requests'] += e['amount']
            if e.get('type') == 'budget_hold':
                amounts = e.get('needs', e.get('amounts', e.get('reserved', {})))
                if amounts.get('llm_requests'):
                    totals['requests'] += amounts['llm_requests']
            totals['flow_outcomes'] += e.get('type') == 'flow_run'
            totals['tool_call_records'] += e.get('type') == 'tool_call'
    if totals['requests'] or totals['unknown_amounts'] or totals['unclassified'] or totals['total_known'] > 80:
        raise RuntimeError('cost authorization invariant failed: ' + str(totals))
    return totals


def completed_accounting(root, project):
    a = accounting(root)
    if a['total_known'] != a['flow_outcomes']:
        raise RuntimeError('a launched flow lacks exactly one outcome')
    events = [json.loads(x) for x in (project / 'reports/resource_ledger.jsonl').read_text().splitlines()]
    runs = list((project / 'backend').glob('RUN_*'))
    outcomes = [e for e in events if e.get('type') == 'flow_run']
    if len(runs) != len(outcomes) or len({e.get('run_dir') for e in outcomes}) != len(outcomes):
        raise RuntimeError('flow outcome/run-directory mismatch')
    if any(e.get('component') == 'memory' for e in events):
        raise RuntimeError('memory execution in memory-disabled collection')
    return a


def harness(root):
    spec = importlib.util.spec_from_file_location('history_harness', root / 'code/memory/scripts/r2g_memory_eval.py')
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    h.EVAL = root
    h.SKILL = root / 'code/r2g-skills/signoff-loop'
    h.FLOW = h.SKILL / 'scripts/flow'
    h.LOOP = h.SKILL / 'scripts/loop/engineer_loop.py'
    h.CASES = root / 'projects'
    h.CFG = {'platform': 'sky130hd'}
    return h


def history_gate(root):
    """Validate all collected records; never merge them into a knowledge store."""
    from ann_v2 import annotate as an, dev_export as dx, verify as vf
    out = root / 'candidate-history'
    if out.exists():
        raise RuntimeError('candidate output already exists; no overwrite')
    lock = load(root / 'reference/t2-source-lock.json')
    dx.build(root, ['projects'], root / 'reference/SOURCE-v1', '', set(),
             {e['design'] for e in lock.get('entries', []) if e.get('design')}, out / 'source')
    an.main(['--source', str(out / 'source'), '--out', str(out / 'ann')])
    report = vf.verify(out / 'ann/ann_v2_dev.jsonl', out / 'source')
    write(out / 'verification.json', report)
    if report['gate'] != 'PASS':
        raise RuntimeError('whole-collection verification gate failed')
    return report


def verdict(h, p, env):
    log = p / 'collection.log'
    runs = sorted((p / 'backend').glob('RUN_*'))
    run = runs[-1] if runs else None
    if run is None:
        raise RuntimeError('admitted flow produced no run directory')
    h.run(['python3', h.SKILL / 'scripts/extract/extract_ppa.py', p, p / 'reports/ppa.json'], env, log, 600)
    rec = {'run_tag': run.name, 'accepted': False}
    if (run / 'final/6_final.odb').exists() or (run / 'final/6_final.gds').exists():
        h.run(['python3', h.SKILL / 'scripts/reports/check_timing.py', p], env, log, 600)
        rc = h.final_signoff(p, env, log)
        rec['signoff_rc'] = rc
        if rc in ('accounting_error', 'budget_exhausted', -9):
            raise RuntimeError('final checker technical stop: ' + str(rc))
        clock = re.search(r'set\s+clk_period\s+([\d.]+)', (p / 'constraints/constraint.sdc').read_text())
        if not clock:
            raise RuntimeError('cannot recover shipped clock period')
        arc = h.run(['python3', h.SKILL / 'scripts/reports/acceptance_check.py', p,
                     '--expected-clock-period', clock[1]], env, log, 600)
        rec['acceptance_rc'] = arc
        rec['acceptance'] = load(p / 'reports/acceptance.json')
        rec['accepted'] = rc == 0 and rec['acceptance'].get('accepted') is True
    from ann_v2.fields import measure
    measured = measure(run)
    cats = load(p / 'reports/drc.json').get('categories', {}) if (p / 'reports/drc.json').exists() else {}
    rec['measurement'] = measured
    rec.update(HC.classify(measured, cats))
    if rec['outcome'] == 'clean' and not rec['accepted']:
        rec.update(outcome='infra', detail='no strict acceptance despite no typed failure; unverified, not clean')
    return rec


def execute(root):
    root = root.resolve()
    # Cross-process exclusion held for the entire run; an interrupted dispatch never auto-resumes.
    with (root / 'dispatcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load(root / 'state.json')
        if state['status'] != 'READY':
            raise RuntimeError('not a fresh READY campaign; no implicit resume')
        c = authorized(root / 'contract.json', root / 'authorization.json')
        state.update(status='RUNNING', pid=os.getpid(), started_epoch=time.time())
        write(root / 'state.json', state)
        h = harness(root)
        try:
            while True:
                immutable_ok(root)
                a = accounting(root)
                state['flows_spent'] = a['total_known']
                write(root / 'accounting.json', a)
                action = HC.next_action(c, state)
                state['current_action'] = action
                write(root / 'state.json', state)
                if action['op'] == 'stop':
                    if action['reason'] == 'queue complete':
                        state['history_verifier'] = history_gate(root)
                    state.update(status='COMPLETED', stop_reason=action['reason'], ended_epoch=time.time())
                    break
                p = root / 'projects' / project_name(action['design'], action['step'])
                never_started(root, p.name)
                log = p / 'collection.log'
                if action['op'] == 'check_chain':
                    out = root / 'first-episode-chain'
                    out.mkdir(exist_ok=False)
                    check = HC.reality_check(p, root / 'reference/SOURCE-v1', root / 'reference/t2-source-lock.json', out)
                    if not check['ok']:
                        raise RuntimeError('first episode chain gate failed: ' + str(check['problems']))
                    state['chain_checked'] = True
                    state['records'].append({**action, 'ok': True})
                    write(root / 'state.json', state)
                    continue
                component = 'initial' if action['op'] == 'explore' else 'catalogue'
                env = environment(root, p, component)
                # Helpers that consult os.environ must see this attempt's budget too.
                os.environ.update(env)
                h._EPISODE = {'ledger': p}
                pre_sdc = HC.sha256(p / 'constraints/constraint.sdc')
                if action['op'] == 'explore':
                    frozen = load(root / 'freeze.json')['initial_project_hashes']
                    prefix = str(p.relative_to(root / 'projects')) + '/'
                    expected = {rel[len(prefix):]: sha for rel, sha in frozen.items() if rel.startswith(prefix)}
                    if hashes(p) != expected:
                        raise RuntimeError('initial project changed before exploration')
                    budget(p, 1)
                    rc = h.run(['bash', env['R2G_RUN_ORFS'], p, 'sky130hd'], env, log, 6000)
                    rec = verdict(h, p, env)
                    rec.update(orfs_rc=rc)
                    if rc in (-9, 75, 76):
                        raise RuntimeError('exploration watchdog/accounting stop: ' + str(rc))
                    write(p / 'exploration.json', rec)
                else:
                    exploration = load(p / 'exploration.json')
                    # Preserve the original charge. All inner callers share this enlarged,
                    # explicitly bounded allowance; the initial flow is not dispatched again.
                    budget(p, 1 + action['flows_cap'])
                    ledger = p / 'engineer.jsonl'
                    cmd = ['python3', h.LOOP, 'add', '--ledger', ledger, '--project', p,
                           '--platform', 'sky130hd', '--reuse-flow-returncode', str(exploration['orfs_rc']),
                           '--replay-evidence', p / 'exploration.json']
                    if h.run(cmd, env, log, 600) != 0:
                        raise RuntimeError('engineer add failed')
                    rc = h.run(['python3', h.LOOP, 'run', '--ledger', ledger, '--max', '1', '--workers', '1', '--no-learn'],
                               env, log, 28800)
                    if rc != 0:
                        raise RuntimeError('engineer technical stop: ' + str(rc))
                    rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
                    if any(r.get('reason') == 'technical_failure' or r.get('status') == 'error' for r in rows):
                        raise RuntimeError('engineer recorded a technical failure')
                    rec = verdict(h, p, env)
                    rec['loop_rc'] = rc
                    write(p / 'repair.json', rec)
                if HC.sha256(p / 'constraints/constraint.sdc') != pre_sdc:
                    raise RuntimeError('shipped SDC changed')
                state['records'].append({**action, **rec, 'finished_epoch': time.time()})
                completed_accounting(root, p)
                write(root / 'state.json', state)
        except Exception as exc:
            state.update(status='PAUSED', suspended=str(exc), ended_epoch=time.time())
            write(root / 'state.json', state)
            raise
        finally:
            write(root / 'state.json', state)
            try:
                write(root / 'accounting.json', accounting(root))
            except Exception as exc:
                write(root / 'accounting-failure.json', {'error': str(exc)})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['prepare', 'prepare-continuation', 'run', 'guard'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--contract', type=Path, default=REPO / 'memory/evaluation/t2_history_collection_contract.json')
    ap.add_argument('--authorization', type=Path, default=REPO / 'memory/evaluation/r2g_T2_history_collection_authorization_20261018.json')
    ap.add_argument('--project', type=Path)
    ap.add_argument('--prior', type=Path)
    ap.add_argument('--resume-authorization', type=Path)
    args = ap.parse_args()
    if args.command == 'prepare':
        prepare(args.root, args.contract, args.authorization)
    elif args.command == 'prepare-continuation':
        if not args.prior or not args.resume_authorization:
            ap.error('prepare-continuation requires --prior and --resume-authorization')
        prepare_continuation(args.root, args.prior, args.contract, args.authorization, args.resume_authorization)
    elif args.command == 'guard':
        if not args.project:
            ap.error('guard requires --project')
        flow_guard(args.root, args.project)
    else:
        execute(args.root)


if __name__ == '__main__':
    main()
