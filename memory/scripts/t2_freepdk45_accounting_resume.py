#!/usr/bin/env python3
"""One incident-scoped continuation; UNKNOWN costs and raw episodes never change.

This is an operator/controller delta, not a memory-method change. A new UNKNOWN
or any unregistered technical failure still stops the queue. No request is retried.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

RULE = 'freepdk45-reviewed-online-accounting-incident-v1'
BLOCKED_ERROR = 'Admission stopped outside the online opportunity limit'
UNKNOWN_ERROR = 'Provider response or usage is unknown/invalid'
USAGE_ERROR = 'provider identity/input/usage violation'
PAUSE_REASON = 'usage is UNKNOWN; queue paused for review (halt_on_unknown_usage)'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(data, stream, indent=1, sort_keys=True, allow_nan=False)
        stream.write('\n')


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def bounds(events, hold):
    h = [e for e in events if e.get('type') == 'budget_hold' and e.get('hold_id') == hold]
    s = [e for e in events if e.get('type') == 'budget_settle' and e.get('hold_id') == hold]
    if len(h) != 1 or len(s) != 1:
        raise ValueError('Expected one hold and settlement')
    if {k for k, v in s[0]['actual'].items() if v == 'UNKNOWN'} != {'tokens_in', 'tokens_out'}:
        raise ValueError('Not an UNKNOWN token settlement')
    result = {k: h[0]['needs'][k] for k in ('tokens_in', 'tokens_out')}
    if any(s[0]['booked'].get(k) != v for k, v in result.items()):
        raise ValueError('Original reservation changed')
    return result


def open_holds(events):
    return ({e['hold_id'] for e in events if e.get('type') == 'budget_hold'}
            - {e['hold_id'] for e in events if e.get('type') == 'budget_settle'})


def classify(project, phase_events):
    """Only a no-response failure or a demonstrable downstream admission refusal."""
    project = Path(project)
    e = load(project / 'episode.json')
    events = rows(project / 'reports/resource_ledger.jsonl')
    calls = [v for v in events if v.get('type') == 'llm_request']
    a = e.get('accounting') or {}
    if open_holds(events) or len(calls) > 3 or a.get('llm_requests') != len(calls):
        raise ValueError('Unsettled or inconsistent request accounting')
    if e.get('terminal_reason') not in ('accounting_failure', 'episode_exception'):
        raise ValueError('Not an incident terminal')
    unknown = [v for v in calls if not isinstance(v.get('usage_reported'), dict)]
    if (e.get('status') == 'ACCOUNTING_FAILURE' and e.get('error') == UNKNOWN_ERROR
            and a.get('passed') is False and a.get('errors') == [USAGE_ERROR]):
        if (len(unknown) != 1 or calls[-1] != unknown[0] or unknown[0].get('status') != -1
                or unknown[0].get('request_id') is not None
                or unknown[0].get('model_reported') is not None):
            raise ValueError('Not a single last no-response request')
        call = unknown[0]
        local = bounds(events, call['hold_id'])
        shared = bounds(phase_events, call['phase_hold_id'])
        if local != shared:
            raise ValueError('Local/shared reservations differ')
        return {'classification': 'ACCOUNTING_FAILURE', 'call': call, 'upper_bound': local}
    if (e.get('status') != 'UNVERIFIED_OR_SAFETY_STOP' or e.get('error') != BLOCKED_ERROR
            or not a.get('passed') or a.get('errors') or unknown):
        raise ValueError('Not a reviewed ledger-blocked episode')
    intents = [v for v in events if v.get('type') == 'agent_request_intent']
    settlements = [v for v in events if v.get('type') == 'budget_settle']
    if not intents or not settlements or settlements[-1].get('actual') != {'repair_attempts': 0.0}:
        raise ValueError('No unexecuted request opportunity')
    prefix = Path(intents[-1]['prefix'])
    if not prefix.resolve().is_relative_to(project.resolve()) or list(prefix.parent.glob(prefix.name + '.try*')):
        raise ValueError('Refused request has provider artifacts or wrong owner')
    refusals = [v for v in phase_events if v.get('type') == 'budget_refused'
                and v.get('reason') == PAUSE_REASON
                and intents[-1]['ts'] <= v['ts'] <= settlements[-1]['ts'] <= e['ended']]
    if len(refusals) != 1:
        raise ValueError('No unique phase refusal inside the unspent opportunity')
    earlier = [v for v in phase_events if v.get('type') == 'budget_settle'
               and v['ts'] < refusals[0]['ts'] and 'UNKNOWN' in v.get('actual', {}).values()]
    if not earlier:
        raise ValueError('Refusal has no preceding UNKNOWN incident')
    return {'classification': 'INFRASTRUCTURE_BLOCKED', 'phase_refusal': refusals[0],
            'request_not_sent': True}


def reviewed_stop(root, project, episode, grant_path):
    """Exact hash-bound, explicitly listed cells only. Never authorizes a retry."""
    root, project, grant_path = Path(root).resolve(), Path(project).resolve(), Path(grant_path).resolve()
    if not project.is_relative_to(root / 'folds') or project.parent.name != 'episodes':
        return False
    grant = load(grant_path)
    if grant.get('accounting_incident_rule') != RULE or grant.get('root') != str(root):
        return False
    binding = grant.get('accounting_incident_reviews', {}).get(str(project))
    if not binding:
        return False
    review_path = Path(binding['path']).resolve()
    if not review_path.is_relative_to(grant_path.parent) or sha(review_path) != binding['sha256']:
        raise ValueError('Incident review drift')
    review = load(review_path)
    if (review.get('rule') != RULE or review.get('project') != str(project)
            or review.get('raw_status') != episode.get('status')
            or review.get('raw_error') != episode.get('error')
            or not review.get('no_rerun') or review.get('measured_repair_outcome') is not False):
        raise ValueError('Incident review does not describe this raw episode')
    for p, h in review['files'].items():
        if sha(p) != h:
            raise ValueError('Incident evidence drift: ' + p)
    phase = Path(review['phase_ledger'])
    if phase.resolve() != root / 'online-phase/resource_ledger.jsonl':
        raise ValueError('Wrong phase ledger')
    with phase.open('rb') as stream:
        prefix = stream.read(review['phase_prefix_bytes'])
    if hashlib.sha256(prefix).hexdigest() != review['phase_prefix_sha256']:
        raise ValueError('Phase ledger prefix changed')
    derived = classify(project, [json.loads(line) for line in prefix.splitlines() if line.strip()])
    if derived['classification'] != review['classification']:
        raise ValueError('Incident classification drift')
    return True


def controller_delta(text):
    """Preserve r14/r15/r16 and all scientific worker code; alter two controller guards."""
    old = "def online_terminal_policy(project,episode):\n    return (registered_checker_stop(project,episode)"
    new = ("def reviewed_accounting_stop(project,episode):\n"
           "    if STANDING_LVS is None:return False\n"
           "    from t2_freepdk45_accounting_resume import reviewed_stop\n"
           "    root,reviews=STANDING_LVS\n"
           "    return reviewed_stop(root,project,episode,reviews.parent/'registration.json')\n\n\n"
           "def online_terminal_policy(project,episode):\n"
           "    return (reviewed_accounting_stop(project,episode) or registered_checker_stop(project,episode)")
    guard = "            if not e.get('accounting',{}).get('passed'):raise RuntimeError('Accounting unresolved: '+str(p))"
    replacement = ("            if not e.get('accounting',{}).get('passed') and not reviewed_accounting_stop(p,e):\n"
                   "                raise RuntimeError('Accounting unresolved: '+str(p))")
    if text.count(old) != 1 or text.count(guard) != 1:
        raise ValueError('Unexpected controller boundary')
    out = text.replace(old, new, 1).replace(guard, replacement, 1)
    compile(out, 'accounting_incident_controller', 'exec')
    return out


def prepare(root, revision, tests, authorization):
    """Read-only diagnosis, then incident occupancy and a registered controller delta."""
    root, revision = Path(root).resolve(), Path(revision).resolve()
    if not authorization or revision.exists():
        raise ValueError('Need authorization and a new revision')
    scripts = root / 'code/memory/scripts'
    sys.path[:0] = [str(scripts), str(root / 'code/memory')]
    import t2_freepdk45_main as M
    state = load(root / 'dispatch-state.json')
    if state.get('status') != 'PAUSED' or state.get('running'):
        raise ValueError('Queue not drained and paused')
    M.F.verify_files(load(root / 'registration.json')['files'])
    phase = root / 'online-phase/resource_ledger.jsonl'
    events = rows(phase)
    if open_holds(events):
        raise ValueError('Open phase holds')
    incidents = []
    for p in sorted((root / 'folds').glob('*/episodes/*/episode.json')):
        e = load(p)
        if e.get('error') in (UNKNOWN_ERROR, BLOCKED_ERROR):
            incidents.append((p.parent, classify(p.parent, events)))
    failures = [(p, d) for p, d in incidents if d['classification'] == 'ACCOUNTING_FAILURE']
    if len(failures) != 1 or len(incidents) != 4:
        raise ValueError('This revision covers one known failure and its three blocked cells only')
    project, failure = failures[0]
    unoccupied = {v['hold_id'] for v in events if v.get('type') == 'budget_settle'
                  and 'UNKNOWN' in v.get('actual', {}).values()}
    unoccupied -= {v['hold_id'] for v in events if v.get('type') == 'budget_unknown_occupancy'}
    if unoccupied != {failure['call']['phase_hold_id']}:
        raise ValueError('Another UNKNOWN requires its own decision')
    # Use the already tested transactional installer. Its source is hash-bound.
    operator = Path(__file__).with_name('t2_freepdk45_corrective.py')
    spec = importlib.util.spec_from_file_location('incident_corrective_operator', operator)
    O = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(O)
    O.passing_tests(tests)
    staging = revision.with_name(revision.name + '-stage')
    staging.mkdir(exist_ok=False)
    (staging / 't2_freepdk45_main.py').write_text(controller_delta((scripts / 't2_freepdk45_main.py').read_text()))
    O.prepare_online_resume(root, revision, tests, authorization, staging)
    grant = load(revision / 'registration.json')
    # Occupancy is not a usage estimate, new quota, retry grant, or future standing rule.
    targets = [{'ledger_path': str(project / 'reports/resource_ledger.jsonl'),
                'hold_id': failure['call']['hold_id'], 'upper_bound': failure['upper_bound']},
               {'ledger_path': str(phase), 'hold_id': failure['call']['phase_hold_id'],
                'upper_bound': failure['upper_bound']}]
    auth = revision / 'unknown-occupancy-authorization.json'
    save(auth, {'schema': 'r2g-unknown-occupancy-authorization-v1', 'user_approved': True,
                'user_text': authorization, 'scope': 'this existing incident only; no retry or future grant',
                'targets': targets, 'meaning': 'original bound for admission only; actual usage remains UNKNOWN'})
    ledger_tool = root / 'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    for target in targets:
        budget = project / 'episode-budget.json' if Path(target['ledger_path']) != phase else root / 'online-budget.json'
        if not budget.exists():
            budget = project.parent.parent / 'episode-budget.json'
        subprocess.run([sys.executable, str(ledger_tool), 'occupy-unknown', str(project),
                        '--hold-id', target['hold_id'], '--authorization', str(auth),
                        '--authorization-sha256', sha(auth)], check=True, capture_output=True, text=True,
                       env={**os.environ, 'R2G_LEDGER_PATH': target['ledger_path'], 'R2G_BUDGET_FILE': str(budget)})
    phase_raw = phase.read_bytes()
    reviews = {}
    for p, derived in incidents:
        files = {str(f): sha(f) for f in [p / 'episode.json', p / 'reports/resource_ledger.jsonl',
                                         *p.glob('*.try*.request.json'), *p.glob('*.try*.response.json')]}
        review = revision / 'accounting-reviews' / (p.name + '.json')
        save(review, {'rule': RULE, 'project': str(p), 'classification': derived['classification'],
                      'raw_status': load(p / 'episode.json')['status'],
                      'raw_error': load(p / 'episode.json')['error'], 'files': files,
                      'phase_ledger': str(phase), 'phase_prefix_bytes': len(phase_raw),
                      'phase_prefix_sha256': hashlib.sha256(phase_raw).hexdigest(),
                      'no_rerun': True, 'measured_repair_outcome': False, 'actual_usage': 'UNKNOWN' if p == project else 'recorded'})
        reviews[str(p)] = {'path': str(review), 'sha256': sha(review)}
    dest = scripts / Path(__file__).name
    staged = revision / 'staged/code/memory/scripts' / dest.name
    shutil.copy2(__file__, staged)
    grant['files'][str(dest)] = {'staged': str(staged), 'sha256': sha(staged),
                                'previous_sha256': sha(dest) if dest.exists() else None}
    grant.update(schema=RULE, accounting_incident_rule=RULE, accounting_incident_reviews=reviews,
                 standing_lvs_internal_rule=M.STANDING_LVS_INTERNAL_RULE,
                 input_gate_rule=M.INPUT_GATE_RULE, rederive_episodes=[])
    grant['tool_files'].update({str(auth): sha(auth), **{v['path']: v['sha256'] for v in reviews.values()}})
    preserved = {str(p): sha(p) for p in [*(root / 'nb/episodes').glob('*/episode.json'),
                                         *(root / 'folds').glob('*/episodes/*/episode.json')]}
    save(revision / 'all-preserved-episodes.json', preserved)
    # Preparation may amend its own not-yet-activated grant; never a runtime/episode.
    (revision / 'registration.json').write_text(json.dumps(grant, indent=1, sort_keys=True) + '\n')
    for p, _ in incidents:
        if not reviewed_stop(root, p, load(p / 'episode.json'), revision / 'registration.json'):
            raise ValueError('Incident receipt did not verify')
    print(json.dumps({'revision': str(revision), 'registered_incidents': len(reviews),
                      'preserved_episodes': len(preserved), 'upper_bound_only': sum(failure['upper_bound'].values()),
                      'actual_usage': 'UNKNOWN', 'zero_requests_or_flows': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--revision', type=Path, required=True)
    parser.add_argument('--tests', type=Path, required=True)
    parser.add_argument('--authorization', required=True)
    args = parser.parse_args()
    prepare(args.root, args.revision, args.tests, args.authorization)
