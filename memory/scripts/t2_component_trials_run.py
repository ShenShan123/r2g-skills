#!/usr/bin/env python3
"""Execute the hash-authorised GRT component-trial contract (v3). No memory, no model, no expansion.

  prepare --root R --contract C --authorization A   copy code/inputs, build state projects, pre-manifests
  run --root R                                      one dispatcher; never resumes or retries

Per state: one baseline flow (must reproduce GRT-0116 at route, else NOT_REPRODUCED and the state
is skipped -- no other utilisation is tried); the area manifest is frozen from the baseline's own
floorplan areas; then each component alone from the same state: config restored byte-for-byte,
one edit applied, rerun from floorplan under an explicit action id, effective-config receipt,
signoff + strict acceptance when the flow completes, envelope (config + area caps), and a chain-v1
fix-log row. The first trial is reality-checked (raw links + ann-v2 gate) before any further flow.
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'memory'))
sys.path.insert(0, str(REPO / 'memory' / 'scripts'))
import t2_component_trials as CT  # noqa: E402
import t2_history_collection as HC  # noqa: E402

FLOOR_KEYS = {'stdcell': 'floorplan__design__instance__area__stdcell', 'core': 'floorplan__design__core__area',
              'die': 'floorplan__design__die__area'}
FLOW_TIMEOUT_S = 9000


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def hashes(root: Path) -> dict:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def authorized(contract: Path, auth: Path) -> dict:
    c, a = load(contract), load(auth)
    if a.get('contract_sha256') != sha(contract) or a.get('user_approved') is not True:
        raise ValueError('contract hash is not the authorised hash, or no user approval')
    errs = CT.validate(c)
    if errs:
        raise ValueError('invalid contract: ' + '; '.join(errs))
    if a.get('caps') != c['caps'] or a.get('automatic_expansion') is not False or a.get('llm_requests') != 0:
        raise ValueError('authorised caps/scope differ from the contract')
    if a.get('states') != [f"{s['design']}@u{s['CORE_UTILIZATION']}" for s in c['states']]:
        raise ValueError('authorised states differ')
    return c


def project_name(state: dict) -> str:
    return f"{state['design']}__u{state['CORE_UTILIZATION']}"


def set_knobs(text: str, edits: dict, drop=()) -> str:
    """config.mk with each edited knob set exactly once (prior assignments removed)."""
    for k in list(edits) + list(drop):
        text = re.sub(r'^\s*export\s+' + re.escape(k) + r'\s*[?:]?=.*\n?', '', text, flags=re.M)
    if not text.endswith('\n'):
        text += '\n'
    return text + ''.join(f'export {k} = {v}\n' for k, v in edits.items())


def util_band(u: int) -> str:
    return 'le12' if u <= 12 else '13-30' if u <= 30 else '31-60' if u <= 60 else 'gt60'


def reproduced(measure: dict) -> bool:
    st = measure['stage']
    return bool(st.get('measured') and st.get('present') and st['fields']['stage'].get('value') == 'route'
                and st['fields']['error_code'].get('value') == 'GRT-0116')


def trial_row(state, comp, action_id, base_tag, post_tag, base_eff, verdict, step_check, before, after) -> dict:
    edit = comp.get('edit') or {}
    before_cfg = {k: (base_eff.get(k) or {}).get('value') for k in edit}
    cum = {k: (base_eff.get(k) or {}).get('value') for k in ('CORE_UTILIZATION', 'ROUTING_LAYER_ADJUSTMENT')}
    cum.update(edit)
    return {'fix_session_id': f"ctrial-{project_name(state)}-{comp['id']}", 'iter': 1, 'strategy': comp['id'],
            'check': 'orfs_stage', 'violation_class': 'route', 'from_stage': 'floorplan',
            'config_before': {k: v for k, v in before_cfg.items() if v is not None},
            'config_delta': json.dumps(edit, sort_keys=True), 'cumulative_config': json.dumps(cum, sort_keys=True),
            'situation': {'check': 'orfs_stage', 'violation_class': 'route', 'error_code': 'GRT-0116',
                          'platform': 'sky130hd', 'die_mode': 'auto', 'timeout': False,
                          'util_band': util_band(int(state['CORE_UTILIZATION'])), 'v': 'sit-v2'},
            'before': before, 'after': after, 'verdict': verdict,
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'chain_version': 1, 'action_id': action_id, 'parent_action': None,
            'pre_run_tag': base_tag, 'post_run_tag': post_tag, 'step_check': step_check,
            'rolled_back': False, 'rollback_reason': None, 'protocol_restored': True,
            'component_kind': comp.get('kind'), 'evidence_tier': 'component_trial_v3'}


def prepare(root: Path, contract: Path, auth: Path) -> dict:
    sys.path.insert(0, str(REPO / 'memory' / 'scripts'))
    import t2a_envelope as E
    from t2a_task_manifests import allowlist
    c = authorized(contract, auth)
    root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(contract, root / 'contract.json')
    shutil.copy2(auth, root / 'authorization.json')
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.git', 'tests', 'docs', 'evaluation',
                                    'upstream')
    for rel in ('memory', 'r2g-skills/signoff-loop', 'r2g-skills/def-graph', 'tools'):
        shutil.copytree(REPO / rel, root / 'code' / rel, ignore=ignore)
    ref = root / 'reference'
    ref.mkdir()
    eval_root = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
    shutil.copytree(eval_root / 'p1c-export/SOURCE-v1', ref / 'SOURCE-v1')
    shutil.copy2(eval_root / 't2-candidate-inventory-20261017/t2-source-lock.json', ref / 't2-source-lock.json')
    states = []
    for s in c['states']:
        base = Path(s['base'])
        inp = root / 'inputs' / s['design']
        shutil.copytree(base / 'constraints', inp / 'constraints')
        cfg = (inp / 'constraints/config.mk').read_text()
        rewrites = {str(base) + '/': str(inp) + '/'}
        for i, src in enumerate(sorted({str(Path(p).parent) for p in s['immutable']['rtl']})):
            shutil.copytree(src, inp / f'rtl{i}')
            rewrites[src + '/'] = str(inp / f'rtl{i}') + '/'
        for i, hook in enumerate(sorted(s['immutable']['hooks'])):
            dst = inp / 'hooks' / f'{i}_{Path(hook).name}'
            dst.parent.mkdir(exist_ok=True)
            shutil.copy2(hook, dst)
            rewrites[hook] = str(dst)
        for old, new in rewrites.items():
            cfg = cfg.replace(old, new)
        p = root / 'projects' / project_name(s)
        shutil.copytree(inp / 'constraints', p / 'constraints')
        cfg = cfg.replace(str(inp / 'constraints') + '/', str(p / 'constraints') + '/')
        cfg = set_knobs(cfg, {'CORE_UTILIZATION': str(s['CORE_UTILIZATION'])}, drop=('DIE_AREA', 'CORE_AREA'))
        (p / 'constraints/config.mk').write_text(cfg)
        (p / 'reports').mkdir(parents=True, exist_ok=True)
        shutil.copy2(p / 'constraints/config.mk', root / 'projects' / (p.name + '.state_config.mk'))
        initial = E.effective_config(cfg)
        imm = {'constraints/constraint.sdc': 'sha256:' + sha(p / 'constraints/constraint.sdc')}
        for f in sorted((inp).rglob('*')):
            if f.is_file() and ('rtl' in f.parent.name or f.parent.name == 'hooks'):
                imm[str(f)] = 'sha256:' + sha(f)
        manifest = {'schema': 't2a-task-envelope-v1', 'task': p.name, 'platform': 'sky130hd',
                    'source_project': str(p), 'initial_config': initial, 'initial_config_sha256': sha(p / 'constraints/config.mk'),
                    'expected_clock_period_ns': 10.0, 'util_variable': 'CORE_UTILIZATION',
                    'immutable': {'knobs': sorted(set(initial) - set(allowlist(initial))), 'files': imm},
                    'allowlist': allowlist(initial), 'require_execution_binding': True,
                    'floorplan_mode_policy': 'explicit_util_or_fixed_rectangles',
                    'area': {k: {'ref': None, 'ref_stage': 'baseline_2_1_floorplan', 'unit': 'um^2',
                                 'max_rel_increase': c['area']['caps'][k]} for k in FLOOR_KEYS},
                    'stage': 'PRE_BASELINE (area references set after the baseline)'}
        write(root / 'manifests' / (p.name + '.pre.json'), manifest)
        states.append({'project': p.name, 'state_config_sha256': sha(root / 'projects' / (p.name + '.state_config.mk'))})
    stores = {str(q): sha(q) for q in REPO.rglob('knowledge.sqlite') if '.git' not in q.parts}
    stores.update({str(q): sha(q) for q in REPO.rglob('heuristics.json') if '.git' not in q.parts})
    freeze = {'schema': 't2-grt-component-trials-freeze-v1', 'contract_sha256': sha(contract),
              'authorization_sha256': sha(auth), 'code_hashes': hashes(root / 'code'),
              'input_hashes': hashes(root / 'inputs'), 'reference_hashes': hashes(ref), 'tracked_stores': stores,
              'states': states, 'flows_total': c['caps']['flows_total'], 'model_requests_allowed': 0,
              'safety': {'ORFS_TIMEOUT_s': 5400, 'flow_watchdog_s': FLOW_TIMEOUT_S, 'threads': 8}}
    write(root / 'freeze.json', freeze)
    write(root / 'state.json', {'status': 'READY', 'records': [], 'flows_spent': 0, 'chain_checked': False})
    return freeze


def immutable_ok(root: Path) -> None:
    f = load(root / 'freeze.json')
    if hashes(root / 'code') != f['code_hashes'] or hashes(root / 'inputs') != f['input_hashes']:
        raise RuntimeError('snapshot code or inputs changed')
    if hashes(root / 'reference') != f['reference_hashes']:
        raise RuntimeError('reference evidence changed')
    for p, h in f['tracked_stores'].items():
        if sha(p) != h:
            raise RuntimeError('tracked store changed: ' + p)
    if sha(root / 'authorization.json') != f['authorization_sha256'] or sha(root / 'contract.json') != f['contract_sha256']:
        raise RuntimeError('contract or authorisation changed')


def flows_spent(root: Path) -> float:
    total = 0.0
    for led in (root / 'projects').glob('*/reports/resource_ledger.jsonl'):
        for line in led.read_text().splitlines():
            e = json.loads(line)
            if e.get('type') == 'budget_charge' and e.get('resource') == 'flows':
                if not isinstance(e.get('amount'), (int, float)):
                    raise RuntimeError('UNKNOWN flow amount in ' + str(led))
                total += e['amount']
            if e.get('type') == 'budget_charge' and e.get('resource') == 'llm_requests' and e.get('amount'):
                raise RuntimeError('a model request was charged')
    return total


def environment(root: Path, project: Path, manifest: Path, extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('R2G_', 'TEHM_', 'DEEPSEEK_', 'OPENAI_', 'ANTHROPIC_', 'ORFS_'))
           and k not in ('FROM_STAGE', 'PYTHONPATH') and 'API_KEY' not in k}
    skill = root / 'code/r2g-skills/signoff-loop'
    env.update(R2G_MEMORY_BACKEND='none', R2G_MEMORY_READ_ONLY_EVAL='1',
               R2G_KNOWLEDGE_DB=str(skill / 'knowledge/knowledge.sqlite'),
               R2G_LEGACY_KNOWLEDGE_DIR=str(skill / 'knowledge'), R2G_JOURNAL_DB=str(root / 'journal.sqlite'),
               R2G_BUDGET_FILE=str(project / 'budget.json'), R2G_BUDGET_COMPONENT='initial',
               R2G_LEDGER_PATH=str(project / 'reports/resource_ledger.jsonl'), R2G_HERMETIC='1',
               R2G_REQUIRE_LLM_BUDGET='1', R2G_TASK_MANIFEST=str(manifest),
               ORFS_TIMEOUT='5400', ORFS_MAX_CPUS='8', NUM_CORES='8', PYTHONDONTWRITEBYTECODE='1',
               LD_LIBRARY_PATH='/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib',
               PATH=str(Path(sys.executable).parent) + ':' + env.get('PATH', '/usr/bin:/bin'))
    env.update(extra)
    return env


def harness(root: Path):
    spec = importlib.util.spec_from_file_location('trial_harness', root / 'code/memory/scripts/r2g_memory_eval.py')
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    h.EVAL = root
    h.SKILL = root / 'code/r2g-skills/signoff-loop'
    h.FLOW = h.SKILL / 'scripts/flow'
    h.CASES = root / 'projects'
    h.CFG = {'platform': 'sky130hd'}
    return h


def run(root: Path) -> None:
    root = root.resolve()
    sys.path.insert(0, str(root / 'code/memory'))
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    import t2a_envelope as E
    from ann_v2 import annotate as AN, effects as EF, fields as FD
    with (root / 'dispatcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load(root / 'state.json')
        if state['status'] != 'READY':
            raise RuntimeError('not a fresh READY campaign; no implicit resume')
        c = authorized(root / 'contract.json', root / 'authorization.json')
        state.update(status='RUNNING', pid=os.getpid(), started_epoch=time.time())
        write(root / 'state.json', state)
        h = harness(root)
        run_orfs = root / 'code/r2g-skills/signoff-loop/scripts/flow/run_orfs.sh'
        cap = c['caps']['flows_total']

        def launch(p, manifest, extra, label):
            immutable_ok(root)
            spent = flows_spent(root)
            if spent + 1 > cap:
                raise RuntimeError(f'flow cap {cap} would be exceeded ({spent} spent)')
            mine = sum(1 for _ in (p / 'backend').glob('RUN_*')) if (p / 'backend').exists() else 0
            write(p / 'budget.json', {'budget_id': 'ctrial-' + p.name, 'reserve': {}, 'halt_on_unknown_usage': True,
                                      'caps': {'flows': mine + 1, 'llm_requests': 0, 'tokens_in': 0,
                                               'tokens_out': 0, 'repair_attempts': 0}})
            env = environment(root, p, manifest, extra)
            os.environ.update(env)
            h._EPISODE = {'ledger': p}
            state['current'] = {'project': p.name, 'step': label, 'started_epoch': time.time()}
            write(root / 'state.json', state)
            rc = h.run(['bash', run_orfs, p, 'sky130hd'], env, p / 'trials.log', FLOW_TIMEOUT_S)
            if rc in (-9, 75, 76):
                raise RuntimeError(f'{label}: watchdog/budget/accounting stop rc={rc}')
            return rc, env

        def signoff(p, env):
            if not ((p / 'backend').exists() and any((p / 'backend').glob('RUN_*/final/6_final.gds'))):
                return None
            h.run(['python3', h.SKILL / 'scripts/extract/extract_ppa.py', p, p / 'reports/ppa.json'], env,
                  p / 'trials.log', 600)
            h.run(['python3', h.SKILL / 'scripts/reports/check_timing.py', p], env, p / 'trials.log', 600)
            rc = h.final_signoff(p, env, p / 'trials.log')
            if rc in ('accounting_error', 'budget_exhausted', -9):
                raise RuntimeError('final checker technical stop: ' + str(rc))
            h.run(['python3', h.SKILL / 'scripts/reports/acceptance_check.py', p, '--expected-clock-period', '10.0'],
                  env, p / 'trials.log', 600)
            acc = load(p / 'reports/acceptance.json')
            return {**acc, 'signoff_rc': rc}

        try:
            for s in c['states']:
                p = root / 'projects' / project_name(s)
                state_cfg = (root / 'projects' / (p.name + '.state_config.mk')).read_bytes()
                if (p / 'constraints/config.mk').read_bytes() != state_cfg:
                    raise RuntimeError('state config changed before the baseline')
                pre_manifest = root / 'manifests' / (p.name + '.pre.json')
                rc, env = launch(p, pre_manifest, {}, 'baseline')
                base = sorted((p / 'backend').glob('RUN_*'))[-1]
                base_m = FD.measure(base)
                rec = {'project': p.name, 'step': 'baseline', 'run': base.name, 'orfs_rc': rc,
                       'stage': {k: (base_m['stage']['fields'].get(k) or {}).get('value')
                                 for k in ('stage', 'error_code', 'timeout', 'grt_overflow')}}
                if not reproduced(base_m):
                    rec['status'] = 'NOT_REPRODUCED'
                    state['records'].append(rec)
                    write(root / 'state.json', state)
                    continue
                rec['status'] = 'REPRODUCED'
                base_eff = (load(base / 'effective-config.json') if (base / 'effective-config.json').exists() else {})
                if base_eff.get('status') != 'PASS':
                    raise RuntimeError('baseline effective-config receipt missing or not PASS')
                floor = load(base / 'logs/2_1_floorplan.json')
                man = load(pre_manifest)
                for k, key in FLOOR_KEYS.items():
                    v = floor.get(key)
                    if not isinstance(v, (int, float)) or v <= 0:
                        raise RuntimeError('baseline floorplan area missing: ' + key)
                    man['area'][k]['ref'] = v
                    man['area'][k]['reference_file'] = str(base / 'logs/2_1_floorplan.json')
                    man['area'][k]['reference_sha256'] = sha(base / 'logs/2_1_floorplan.json')
                man['stage'] = 'FROZEN_AFTER_BASELINE'
                frozen = root / 'manifests' / (p.name + '.json')
                write(frozen, man)
                rec['manifest_sha256'] = sha(frozen)
                state['records'].append(rec)
                write(root / 'state.json', state)
                target = AN._target_from_run('stage_abort', base_m)
                for comp in s['components']:
                    (p / 'constraints/config.mk').write_bytes(state_cfg)          # same start, every time
                    edit = comp.get('edit') or {}
                    if edit:
                        (p / 'constraints/config.mk').write_text(set_knobs(state_cfg.decode(), edit))
                    aid = f"ctrial-{p.name}-{comp['id']}#1"
                    extra = {'FROM_STAGE': 'floorplan', 'R2G_ACTION_ID': aid, 'R2G_BUDGET_COMPONENT': 'catalogue',
                             'R2G_RERUN_REASON': f"component trial: strategy={comp['id']} rerun_from=floorplan"}
                    rc, env = launch(p, frozen, extra, comp['id'])
                    named = [d for d in (p / 'backend').glob('RUN_*')
                             if (d / 'r2g_action.json').exists() and load(d / 'r2g_action.json').get('action_id') == aid]
                    if len(named) != 1:
                        raise RuntimeError(f"{comp['id']}: {len(named)} runs name the action")
                    post = named[0]
                    post_m = FD.measure(post)
                    trial_eff = load(post / 'effective-config.json') if (post / 'effective-config.json').exists() else {}
                    effect = EF.step_effect(target, base_m, post_m)
                    acc = signoff(p, env) if post_m['stage'].get('present') is False else None
                    envl = E.envelope(man, p, post, env)
                    base_vals = {k: v.get('value') for k, v in (base_eff.get('effective') or {}).items()}
                    trial_vals = ({k: v.get('value') for k, v in trial_eff['effective'].items()}
                                  if trial_eff.get('status') == 'PASS' else None)
                    out = CT.classify(comp, base_vals, trial_vals,
                                      effect, {k: (post_m['stage']['fields'].get(k) or {}).get('value')
                                               for k in ('stage', 'error_code', 'timeout')}
                                      | {'present': post_m['stage'].get('present')}, acc, envl)
                    verdict = ('stop_control_rerun' if comp.get('kind') == 'control' else
                               'cleared' if effect == 'CLEARED' else
                               'regression' if effect == 'REGRESSED' else
                               'applied' if effect == 'IMPROVED' else 'no_improvement' if effect == 'NO_CHANGE'
                               else 'inconclusive')
                    ov0 = (base_m['stage']['fields'].get('grt_overflow') or {}).get('value')
                    ov1 = (post_m['stage']['fields'].get('grt_overflow') or {}).get('value')
                    step_check = {'check': 'route', 'graded_run_tag': post.name, 'effect': effect,
                                  'post_stage': (post_m['stage']['fields'].get('stage') or {}).get('value'),
                                  'post_error_code': (post_m['stage']['fields'].get('error_code') or {}).get('value'),
                                  'grt_overflow_before': ov0, 'grt_overflow_after': ov1}
                    row = trial_row(s, comp, aid, base.name, post.name, base_eff.get('effective') or {}, verdict,
                                    step_check, ov0, ov1)
                    with (p / 'reports/fix_log.jsonl').open('a') as f:
                        f.write(json.dumps(row, sort_keys=True) + '\n')
                    (p / 'constraints/config.mk').write_bytes(state_cfg)          # protocol restore
                    state['records'].append({'project': p.name, 'step': comp['id'], 'run': post.name, 'orfs_rc': rc,
                                             'trial': out, 'step_check': step_check,
                                             'acceptance': acc, 'envelope': envl,
                                             'finished_epoch': time.time()})
                    state['flows_spent'] = flows_spent(root)
                    write(root / 'state.json', state)
                    if not state['chain_checked'] and comp.get('kind') == 'action':
                        out_dir = root / 'first-trial-chain'
                        out_dir.mkdir(exist_ok=False)
                        chk = HC.reality_check(p, root / 'reference/SOURCE-v1', root / 'reference/t2-source-lock.json',
                                               out_dir)
                        if not chk['ok']:
                            raise RuntimeError('first trial chain gate failed: ' + str(chk['problems']))
                        state['chain_checked'] = True
                        write(root / 'state.json', state)
            state.update(status='COMPLETED', flows_spent=flows_spent(root), ended_epoch=time.time())
            state.pop('current', None)
            write(root / 'state.json', state)
            gate(root)
        except Exception as exc:
            state.update(status='PAUSED', suspended=str(exc), ended_epoch=time.time())
            write(root / 'state.json', state)
            raise


def gate(root: Path) -> dict:
    """Annotate (labels v2) and verify every trial row; never merge into any store."""
    from ann_v2 import annotate as AN, dev_export as DX, verify as VF
    out = root / 'candidate-history'
    if out.exists():
        raise RuntimeError('candidate output exists; no overwrite')
    lock = load(root / 'reference/t2-source-lock.json')
    DX.build(root, ['projects'], root / 'reference/SOURCE-v1', '', set(),
             {e['design'] for e in lock.get('entries', []) if e.get('design')}, out / 'source')
    AN.main(['--source', str(out / 'source'), '--out', str(out / 'ann')])
    rep = VF.verify(out / 'ann/ann_v2_dev.jsonl', out / 'source')
    write(out / 'verification.json', rep)
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['prepare', 'run'])
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--contract', type=Path)
    ap.add_argument('--authorization', type=Path)
    a = ap.parse_args()
    if a.command == 'prepare':
        print(json.dumps(prepare(a.root, a.contract, a.authorization)['states'], indent=1))
    else:
        run(a.root)


if __name__ == '__main__':
    main()
