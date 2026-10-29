#!/usr/bin/env python3
"""User-authorized transport-UNKNOWN quarantine, not a retry or measured failure.

Only this uncapped online phase changes. Per-episode three-request limits,
provider identity checks, flow correspondence and raw usage remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

import t2_freepdk45_accounting_resume as A

RULE = 'freepdk45-transport-unknown-quarantine-no-retry-v1'


def snapshot(path):
    with Path(path).open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return stream.read()


def uncapped_continue(budget):
    return (budget.get('caps') == {} and budget.get('reserve', {}) == {}
            and budget.get('combined_tokens_cap') is None
            and budget.get('halt_on_unknown_usage') is False)


def validate_calls(events):
    """Unknown transport is distinct from wrong model or missing known-response usage."""
    calls = [e for e in events if e.get('type') == 'llm_request']
    if len(calls) > 3:
        raise ValueError('Online request cap exceeded')
    unknown = []
    for e in calls:
        if e.get('model_mismatch'):
            raise ValueError('Provider identity mismatch')
        if (e.get('model_requested') != 'deepseek-flash'
                or e.get('endpoint') != 'https://api.deepseek.com/chat/completions'):
            raise ValueError('Unregistered provider/model')
        usage = e.get('usage_reported')
        if usage == 'UNKNOWN':
            if (e.get('status') != -1 or e.get('request_id') is not None
                    or e.get('model_reported') is not None):
                raise ValueError('Not a no-response transport UNKNOWN')
            unknown.append(e)
        else:
            import math
            if (e.get('status') != 200 or e.get('model_reported') not in ('deepseek-flash', 'deepseek-v4.1-flash')
                    or not isinstance(usage, dict)
                    or any(isinstance(usage.get(k), bool) or not isinstance(usage.get(k), (int, float))
                           or not math.isfinite(usage[k]) or usage[k] < 0
                           for k in ('prompt_tokens', 'completion_tokens'))):
                raise ValueError('Invalid known response identity/usage')
    return unknown


def standing_skip(root, project, episode, grant_path):
    root, project = Path(root).resolve(), Path(project).resolve()
    grant_path = Path(grant_path).resolve()
    if not project.is_relative_to(root / 'folds') or project.parent.name != 'episodes':
        return False
    grant = A.load(grant_path)
    if grant.get('unknown_skip_rule') != RULE or grant.get('root') != str(root):
        return False
    if not uncapped_continue(A.load(root / 'online-budget.json')):
        raise ValueError('UNKNOWN continuation needs the registered uncapped online policy')
    if episode.get('error') not in (A.UNKNOWN_ERROR, A.BLOCKED_ERROR):
        return False
    events = A.rows(project / 'reports/resource_ledger.jsonl')
    try:
        validate_calls(events)
        raw = snapshot(root / 'online-phase/resource_ledger.jsonl')
        phase = [json.loads(line) for line in raw.splitlines() if line.strip()]
        derived = A.classify(project, phase)
    except (ValueError, KeyError):
        return False  # any other accounting fault still pauses the queue
    # Independently reproduce the worker's existing accounting, including flow outcomes.
    import t2a_runtime as T
    import t2_freepdk45_online_gate as OG
    OG.install(T)
    checked = T.accounting_check(events, list((project / 'backend').glob('RUN_*')))
    raw_episode = A.load(project / 'episode.json')
    if checked != raw_episode.get('accounting'):
        return False
    allowed = [] if derived['classification'] == 'INFRASTRUCTURE_BLOCKED' else [A.USAGE_ERROR]
    if checked.get('errors') != allowed:
        return False
    report = grant_path.parent / 'unknown-reviews' / (project.name + '.json')
    files = {str(p): A.sha(p) for p in [project / 'episode.json',
             project / 'reports/resource_ledger.jsonl', *project.glob('*.try*.request.json'),
             *project.glob('*.try*.response.json')]}
    review = {'rule': RULE, 'classification': derived['classification'], 'project': str(project),
              'files': files, 'raw_status': raw_episode['status'], 'actual_usage': 'UNKNOWN' if allowed else 'recorded',
              'no_rerun': True, 'no_retry': True, 'measured_repair_outcome': False,
              'phase_prefix_bytes': len(raw), 'phase_prefix_sha256': hashlib.sha256(raw).hexdigest(),
              'authorization': str(grant_path), 'authorization_sha256': A.sha(grant_path),
              'recorded': time.time()}
    if report.exists():
        old = A.load(report)
        if (old.get('rule') != RULE or old.get('files') != files
                or old.get('authorization_sha256') != A.sha(grant_path)
                or hashlib.sha256(raw[:old['phase_prefix_bytes']]).hexdigest() != old['phase_prefix_sha256']):
            raise ValueError('UNKNOWN review drift')
    else:
        A.save(report, review)
        log = report.parent / 'incidents.jsonl'
        with log.open('a') as stream:
            stream.write(json.dumps({'project': str(project), 'classification': derived['classification'],
                                     'review': str(report), 'sha256': A.sha(report)}) + '\n')
    return True


def main_delta(text):
    old = ("    from t2_freepdk45_accounting_resume import reviewed_stop\n"
           "    root,reviews=STANDING_LVS\n"
           "    return reviewed_stop(root,project,episode,reviews.parent/'registration.json')")
    new = ("    from t2_freepdk45_unknown_skip import standing_skip\n"
           "    root,reviews=STANDING_LVS\n"
           "    return standing_skip(root,project,episode,reviews.parent/'registration.json')")
    call = 'F.dispatch(root,pending,popen=worker_popen,terminal_policy=online_terminal_policy)'
    if text.count(old) != 1 or text.count(call) != 1:
        raise ValueError('Unexpected r17 controller boundary')
    return text.replace(old, new, 1).replace(call,
        call[:-1] + ',accounting_policy=reviewed_accounting_stop)', 1)


def dispatch_delta(text):
    old = 'def dispatch(root,jobs,popen=None,sleep=time.sleep,terminal_policy=None):'
    new = 'def dispatch(root,jobs,popen=None,sleep=time.sleep,terminal_policy=None,accounting_policy=None):'
    guard = "            if p.returncode or not valid_terminal or not e.get('accounting',{}).get('passed'):"
    replacement = ("            accounting_valid=(e.get('accounting',{}).get('passed') or\n"
                   "                (accounting_policy is not None and accounting_policy(file.parent,e)))\n"
                   "            if p.returncode or not valid_terminal or not accounting_valid:")
    if text.count(old) != 1 or text.count(guard) != 1:
        raise ValueError('Unexpected dispatcher boundary')
    return text.replace(old, new, 1).replace(guard, replacement, 1)


def policy_rebind(root, grant, reg):
    """Only an existing shared budget reference may change inside sealed folds."""
    root = Path(root).resolve()
    expected = {str(root / 'online-budget.json'), str(root / 'frozen-memory-plan.json')}
    expected |= {str(p) for p in (root / 'folds').glob('*/registration.json')}
    binds = grant.get('policy_rebinds') or {}
    if set(binds) != expected or len(expected) != 52:
        raise ValueError('Policy rebind must name the budget, plan and exactly 50 fold registrations')
    for p, h in binds.items():
        row = grant['files'].get(p)
        if not row or row['sha256'] != h or A.sha(row['staged']) != h:
            raise ValueError('Unregistered policy rebind')
    reg['files'].update(binds)


def operator_module():
    path = Path(__file__).with_name('t2_freepdk45_corrective.py')
    spec = importlib.util.spec_from_file_location('unknown_skip_corrective', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare(root, revision, tests, authorization):
    root, revision = Path(root).resolve(), Path(revision).resolve()
    if not authorization or revision.exists():
        raise ValueError('Need explicit standing authorization and a fresh revision')
    scripts = root / 'code/memory/scripts'
    sys.path[:0] = [str(scripts), str(root / 'code/memory')]
    import t2_freepdk45_main as M
    O = operator_module()
    O.passing_tests(tests)
    budget = A.load(root / 'online-budget.json')
    if budget.get('caps') != {} or budget.get('reserve', {}) or budget.get('combined_tokens_cap') is not None:
        raise ValueError('This policy cannot disable accounting under a cumulative cap')
    phase = A.rows(root / 'online-phase/resource_ledger.jsonl')
    if A.open_holds(phase):
        raise ValueError('Phase must be drained')
    incidents = []
    for p in sorted((root / 'folds').glob('*/episodes/*/episode.json')):
        e = A.load(p)
        if e.get('error') in (A.UNKNOWN_ERROR, A.BLOCKED_ERROR):
            validate_calls(A.rows(p.parent / 'reports/resource_ledger.jsonl'))
            incidents.append((str(p.parent), A.classify(p.parent, phase)['classification']))
    staging = revision.with_name(revision.name + '-stage')
    staging.mkdir(exist_ok=False)
    (staging / 't2_freepdk45_main.py').write_text(main_delta((scripts / 't2_freepdk45_main.py').read_text()))
    O.prepare_online_resume(root, revision, tests, authorization, staging)
    grant = A.load(revision / 'registration.json')

    def stage(dest, data=None, source=None):
        dest = Path(dest)
        out = revision / 'staged' / dest.relative_to(root)
        out.parent.mkdir(parents=True, exist_ok=True)
        if source is not None:
            shutil.copy2(source, out)
        elif isinstance(data, str):
            out.write_text(data)
        else:
            out.write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')
        grant['files'][str(dest)] = {'staged': str(out), 'sha256': A.sha(out),
                                     'previous_sha256': A.sha(dest) if dest.exists() else None}
        return A.sha(out)

    stage(scripts / Path(__file__).name, source=__file__)
    stage(scripts / 't2_nb_followup.py', dispatch_delta((scripts / 't2_nb_followup.py').read_text()))
    budget['halt_on_unknown_usage'] = False
    budget_sha = stage(root / 'online-budget.json', budget)
    binds = {str(root / 'online-budget.json'): budget_sha}
    plan = A.load(root / 'frozen-memory-plan.json')
    for path, oldsha in plan['registrations'].items():
        if A.sha(path) != oldsha:
            raise ValueError('Frozen fold drift')
        fold = A.load(path)
        if fold['files'].get(str(root / 'online-budget.json')) != grant['files'][str(root / 'online-budget.json')]['previous_sha256']:
            raise ValueError('Fold does not bind the shared online budget')
        updated = copy.deepcopy(fold)
        updated['files'][str(root / 'online-budget.json')] = budget_sha
        digest = stage(path, updated)
        plan['registrations'][path] = digest
        binds[path] = digest
    binds[str(root / 'frozen-memory-plan.json')] = stage(root / 'frozen-memory-plan.json', plan)
    grant.update(schema=RULE, unknown_skip_rule=RULE, policy_operator_sha256=A.sha(__file__),
                 policy_rebinds=binds, known_incidents=incidents,
                 standing_lvs_internal_rule=M.STANDING_LVS_INTERNAL_RULE,
                 input_gate_rule=M.INPUT_GATE_RULE, rederive_episodes=[],
                 raw_costs_and_episode_records_unchanged=True, actual_unknown_kept=True,
                 implicit_retries=0, online_requests_per_episode=3)
    (revision / 'registration.json').write_text(json.dumps(grant, indent=1, sort_keys=True) + '\n')
    preserved = {str(p): A.sha(p) for p in [*(root / 'nb/episodes').glob('*/episode.json'),
                 *(root / 'folds').glob('*/episodes/*/episode.json'), root / 'online-phase/resource_ledger.jsonl']}
    A.save(revision / 'preserved-raw-records.json', preserved)
    print(json.dumps({'revision': str(revision), 'known_incidents': incidents,
                      'runtime_files': 3, 'policy_rebinds': len(binds), 'zero_flows_or_model_requests': True}))


def operate(root, revision):
    root, revision = Path(root).resolve(), Path(revision).resolve()
    grant = A.load(revision / 'registration.json')
    if grant.get('unknown_skip_rule') != RULE or grant.get('policy_operator_sha256') != A.sha(__file__):
        raise ValueError('Policy operator identity drift')
    O = operator_module()
    original = O.install_followup

    def install(root, revision, grant, reg, nbreg, reviews, preserved):
        policy_rebind(root, grant, reg)
        return original(root, revision, grant, reg, nbreg, reviews, preserved)

    O.install_followup = install
    # Existing installer checks a drained queue, pins, test receipt, and rolls
    # back ALL staged files and registrations if installation fails.
    O.operate_native_followup(root, revision)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare', 'operate'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--revision', type=Path, required=True)
    p.add_argument('--tests', type=Path)
    p.add_argument('--authorization')
    a = p.parse_args()
    if a.command == 'prepare':
        prepare(a.root, a.revision, a.tests, a.authorization)
    else:
        operate(a.root, a.revision)
