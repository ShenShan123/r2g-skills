"""Opt-in three-way consumption of independently verified procedural evidence.

This grants a checked *trial*, never causal/production authority or a prediction
of success. Direction-only matches remain advice. No design, action name or
repair parameter is prescribed here. Existing frozen policies are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from tehm_v2.evidence_index import ORFS_DEFAULTS

VERSION = 'tehm-three-way-evidence-gated-v1'
MIN_SOURCE_GROUPS = 2  # conservative versioned policy, not a measured optimum


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value(v):
    if isinstance(v, bool): return ('literal', str(v))
    try:
        n = float(v)
        if math.isfinite(n): return ('number', n)
    except (TypeError, ValueError):
        pass
    return ('literal', str(v))


def edits(raw):
    delta = raw.get('config_delta') or {}
    if isinstance(delta, str): delta = json.loads(delta)
    return {k: v.get('after') if isinstance(v, dict) else v for k, v in delta.items()}


def same_edits(a, b):
    return bool(a) and {k: value(v) for k, v in a.items()} == {k: value(v) for k, v in b.items()}


def load_evidence(root, env):
    """Reuse the independent ann-v2 import authority, not a success-like counter.

    Every input and label is hash-bound. Missing/mutated proof is a technical
    error, not a reason to silently run without memory.
    """
    from tehm.shared_import import verified_label
    from t2_shared_history import tehm_view
    root = Path(root)
    manifest = json.loads((root / 'shared/manifest.json').read_text())
    source = root / 'shared/shared_units.jsonl'
    if sha(source) != manifest['units_sha256']:
        raise ValueError('Three-way shared history hash changed')
    shared = [json.loads(s) for s in source.read_text().splitlines() if s.strip()]
    exclusion = json.loads((root / 'exclusions.json').read_text())
    ids = [u['unit_id'] for u in shared]
    if len(set(ids)) != len(ids) or set(ids) != set(exclusion['kept_unit_ids']):
        raise ValueError('Three-way exclusion/unit identity mismatch')
    if any(u.get('source_handoff_id') in exclusion['excluded_origins'] for u in shared):
        raise ValueError('Excluded source in three-way history')
    exported = [json.loads(s) for s in (root / 'tehm-import/source_export.jsonl').read_text().splitlines() if s.strip()]
    projected = {u['unit_id']: u for u in tehm_view(shared)}
    if (len(exported) != len(shared) or {u['unit_id'] for u in exported} != set(projected)):
        raise ValueError('Incomplete three-way import proof')
    for unit in exported:
        if {k: v for k, v in unit.items() if k != 'shared_evidence_proof'} != projected.get(unit['unit_id']):
            raise ValueError('Three-way import differs from shared history')
        verified_label(unit)
    policy_path = Path(env['R2G_TEHM_V2_POLICY'])
    policy = json.loads(policy_path.read_text())
    index_path = Path(policy['index'])
    if sha(index_path) != policy['index_sha256']:
        raise ValueError('Three-way evidence index changed')
    index = json.loads(index_path.read_text())
    refs = {'shared_units_sha256': sha(source), 'index_sha256': sha(index_path),
            'policy_sha256': sha(policy_path), 'exclusions_sha256': sha(root / 'exclusions.json'),
            'import_sha256': sha(root / 'tehm-import/source_export.jsonl')}
    return shared, index, {policy['task_ip_family']} if policy.get('task_ip_family') else set(), refs


def choose(*, plan, situation, cfg, units, index, exclude_families, used):
    """Only the producer's head can qualify; do not bypass its vetoes/ranking.

    Advice/abstention never removes an ordinary Agent tool or starts a flow.
    Local execution needs replicated full-task successes for the complete,
    exact action and edited-knob pre-state, not independent knob directions.
    Any matching adverse/uncertain outcome leaves the choice to the Agent.
    """
    candidates = plan.get('strategies') or []
    result = {'policy': VERSION, 'choice': 'experience_assisted_online' if candidates else 'no_experience_online',
              'reason': 'no_reliable_local_procedure', 'candidate': None, 'witness_ids': [],
              'causal_authority_granted': False, 'write_back': False,
              'minimum_source_groups': MIN_SOURCE_GROUPS}
    def abstain(reason):
        return {**result, 'reason': reason}
    if not candidates: return abstain('no_candidate')
    candidate = candidates[0]
    state = (plan.get('tehm_v2') or {}).get('state') or {}
    signature = state.get('signature')
    if not signature or state.get('timing_unconstrained') or state.get('failures') != [signature]:
        return abstain('unknown_unconstrained_or_multiple_failures')
    decision = next((d for d in (plan.get('tehm_v2') or {}).get('decisions', [])
                     if d.get('id') == candidate.get('id')), {})
    if decision.get('decision') != 'kept' or decision.get('deferred'):
        return abstain('native_policy_not_kept_or_deferred')
    if candidate.get('id') in used: return abstain('already_tried')
    action = candidate.get('config_edits') or {}
    if (not candidate.get('auto_apply') or not action or candidate.get('sdc_edits') or candidate.get('env_edits')
            or candidate.get('rerun_from') not in ('synth', 'floorplan', 'place', 'cts', 'route')):
        return abstain('not_a_bound_config_only_action')
    if candidate.get('recheck') != ('route' if situation.get('check') == 'orfs_stage' else situation.get('check')):
        return abstain('recheck_mismatch')
    fields = ['platform', 'check', 'die_mode', 'util_band', 'timeout']
    if situation.get('check') in ('drc', 'orfs_stage'): fields.append('violation_class')
    if situation.get('check') == 'orfs_stage': fields.append('error_code')
    if any(situation.get(k) is None for k in fields): return abstain('unknown_applicability')
    before = {k: cfg.get(k, ORFS_DEFAULTS.get(k)) for k in action}
    if any(v is None for v in before.values()): return abstain('unknown_action_precondition')
    if all(value(before[k]) == value(v) for k, v in action.items()): return abstain('no_effect')
    # A direction-only negative may warn against exact replay. It never bans
    # the Agent from adapting that direction or choosing the public tool.
    from tehm_v2.policy import evidence_summary, moves
    profile = evidence_summary({**candidate, '_moves': moves(action, cfg)}, index, signature, exclude_families)
    if any(profile[k] for k in ('harm', 'ineffective', 'unconfirmed', 'partial')):
        return abstain('conflicting_or_incomplete_historical_evidence')
    groups, witnesses = set(), []
    for unit in units:
        lab, raw = unit.get('shared_label') or {}, unit.get('raw_row') or {}
        if lab.get('signature') != signature: continue
        sit = raw.get('situation') or {}
        if any(sit.get(k) != situation[k] for k in fields): continue
        if raw.get('from_stage') != candidate['rerun_from'] or not same_edits(edits(raw), action): continue
        if any(value(raw.get('config_before', {}).get(k, ORFS_DEFAULTS.get(k))) != value(v)
               for k, v in before.items()): continue
        matching = [e for e in index.get('entries', []) if e.get('unit_id') == unit['unit_id']
                    and e.get('signature') == signature and e.get('ip_family') not in exclude_families]
        identities = {(e.get('source_group'), e.get('ip_family')) for e in matching}
        if len(identities) != 1: continue
        group, family = next(iter(identities))
        if not group or not family or not unit.get('source_eligible'): continue
        rb = lab.get('rolled_back') or {}; bound = lab.get('binding') or {}
        verdict = raw.get('acceptance_result') or {}; acceptance = verdict.get('acceptance') or {}
        positive = (lab.get('admitted') is True and lab.get('outcome_class') == 'admitted_positive'
                    and bound.get('pre') == bound.get('post') == 'KNOWN'
                    and raw.get('pre_run_tag') and raw.get('post_run_tag')
                    and rb.get('status') == 'KNOWN' and rb.get('value') is False and raw.get('rolled_back') is False
                    and verdict.get('accepted') is True and acceptance.get('accepted') is True
                    and acceptance.get('failed') == [] and acceptance.get('unknown') == []
                    and (verdict.get('envelope') or {}).get('status') == 'PASS')
        if not positive: return abstain('matching_trial_not_verified_whole_task_success')
        groups.add(group); witnesses.append(unit['unit_id'])
    if len(groups) < MIN_SOURCE_GROUPS: return abstain('insufficient_independent_exact_action_support')
    return {**result, 'choice': 'local_experience', 'reason': 'replicated_verified_procedure_trial',
            'candidate': candidate, 'witness_ids': sorted(set(witnesses)), 'source_groups': sorted(groups),
            'authority': 'verified procedural trial; NOT validated causal mechanism',
            'fresh_full_acceptance_required': True}
