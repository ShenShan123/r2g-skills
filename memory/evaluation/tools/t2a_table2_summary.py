#!/usr/bin/env python3
"""Unified Table 2 audit summary (2026-10-16). Reads raw records only; never edits an episode or ledger.

Every cell gets ONE outcome class:
  ACCEPTED            strict acceptance confirmed
  MEASURED_FAILURE    NOT_ACCEPTED with all repair opportunities actually available
  KNOWN_INCOMPLETE    repair allowance exhausted on a verified, known-incomplete state (a real non-success)
  HARNESS_STOP        restoration could not be verified (e.g. the gcd/u80 stale baseline-proof defect)
  INFRA_BLOCKED       accounting failure (UNKNOWN usage) or a ledger refusal withheld model turns
  TECHNICAL_FAILURE   invalid candidate or crashed diagnose/consultation stopped the episode

G/H vs NB is reported in two views: the frozen T1 collector's (ACCEPTED/NOT_ACCEPTED pairs only) and the
measured view (pairs where both cells are ACCEPTED, MEASURED_FAILURE or KNOWN_INCOMPLETE). Costs are means over
ALL registered episodes of the arm, with UNKNOWN token requests counted separately, never as zero.

  python3 t2a_table2_summary.py --root <package>
"""
import argparse
import json
from pathlib import Path
import sys

MEASURED = ('ACCEPTED', 'MEASURED_FAILURE', 'KNOWN_INCOMPLETE')


def read(path):
    return json.loads(Path(path).read_text())


def episode_name(row):
    return row['task'].replace('/', '__') + '__' + row['arm'] + '__r' + str(row['repeat'])


def classify(data, events):
    """Outcome class of one ended episode from its raw record and ledger events."""
    status, reason = data.get('status'), data.get('terminal_reason') or ''
    if reason == 'technical_failure' or data.get('technical_failures'):
        return 'TECHNICAL_FAILURE'
    refused = any(e.get('type') == 'budget_refused' for e in events) or any(
        isinstance(t, dict) and t.get('status') == 'REQUEST_STOP'
        and (t.get('usage') or t.get('reason') or {}).get('budget_exhausted')
        for t in data.get('repair_turns') or [])
    if status == 'ACCOUNTING_FAILURE' or refused:
        return 'INFRA_BLOCKED'
    if status == 'ACCEPTED':
        return 'ACCEPTED'
    if status == 'NOT_ACCEPTED':
        return 'MEASURED_FAILURE'
    if reason == 'repair_allowance_exhausted_known_incomplete':
        return 'KNOWN_INCOMPLETE'
    return 'HARNESS_STOP'


def summarize(root, T):
    root = Path(root)
    reg = read(root / 'registration.json')
    cells, missing = {}, []
    for row in reg['plan']:
        project = root / 'episodes' / episode_name(row)
        if not (project / 'episode.json').exists():
            missing.append(row)
            continue
        data = read(project / 'episode.json')
        if 'ended' not in data:
            missing.append(row)
            continue
        ledger = project / 'reports/resource_ledger.jsonl'
        events = [json.loads(l) for l in ledger.read_text().splitlines() if l] if ledger.exists() else []
        usage = T.request_token_totals(events)
        cells[(row['task'], row['repeat'], row['arm'])] = {
            **row, 'status': data.get('status'), 'terminal_reason': data.get('terminal_reason'),
            'outcome_class': classify(data, events),
            'flows': sum(e.get('type') == 'flow_run' for e in events),
            'requests': usage.get('llm_requests', 0), 'repair_requests': usage.get('repair_requests', 0),
            'auxiliary_requests': usage.get('auxiliary_requests', 0),
            'known_tokens': usage.get('known_input_tokens', 0) + usage.get('known_output_tokens', 0),
            'unknown_usage_requests': usage.get('unknown_usage_requests', 0),
            'tool_calls': sum(e.get('type') == 'tool_call' for e in events),
            'checker_runs': sum(e.get('type') == 'checker_run' for e in events),
            'worker_wall_s': data.get('total_worker_wall_s')}
    arms = reg['arms']
    tasks = [t['task'] for t in reg['tasks']]
    by_arm = {}
    for arm in arms:
        rows = [c for (t, r, a), c in cells.items() if a == arm]
        denom = sum(1 for p in reg['plan'] if p['arm'] == arm)
        frozen = {'G': 0, 'H': 0, 'eligible': 0}
        measured = {'G': 0, 'H': 0, 'eligible': 0, 'excluded_infra_or_harness': 0}
        for task in tasks:
            for rep in range(1, reg['repeats'] + 1):
                x, nb = cells.get((task, rep, arm)), cells.get((task, rep, 'NB'))
                if arm == 'NB' or not x or not nb:
                    continue
                ok_x, ok_nb = x['outcome_class'] == 'ACCEPTED', nb['outcome_class'] == 'ACCEPTED'
                if x['status'] in ('ACCEPTED', 'NOT_ACCEPTED') and nb['status'] in ('ACCEPTED', 'NOT_ACCEPTED'):
                    frozen['eligible'] += 1
                    frozen['G'] += int(x['status'] == 'ACCEPTED' and nb['status'] != 'ACCEPTED')
                    frozen['H'] += int(nb['status'] == 'ACCEPTED' and x['status'] != 'ACCEPTED')
                if x['outcome_class'] in MEASURED and nb['outcome_class'] in MEASURED:
                    measured['eligible'] += 1
                    measured['G'] += int(ok_x and not ok_nb)
                    measured['H'] += int(ok_nb and not ok_x)
                else:
                    measured['excluded_infra_or_harness'] += 1
        walls = [c['worker_wall_s'] for c in rows]
        complete = len(rows) == denom
        by_arm[arm] = {
            'registered': denom, 'ended': len(rows),
            'accepted': sum(c['outcome_class'] == 'ACCEPTED' for c in rows),
            'outcome_classes': {k: sum(c['outcome_class'] == k for c in rows)
                                for k in ('ACCEPTED', 'MEASURED_FAILURE', 'KNOWN_INCOMPLETE', 'HARNESS_STOP',
                                          'INFRA_BLOCKED', 'TECHNICAL_FAILURE')},
            'means_over_all_registered': None if not complete else {
                'flows': sum(c['flows'] for c in rows) / denom,
                'requests': sum(c['requests'] for c in rows) / denom,
                'repair_requests': sum(c['repair_requests'] for c in rows) / denom,
                'known_tokens': sum(c['known_tokens'] for c in rows) / denom,
                'tool_calls': sum(c['tool_calls'] for c in rows) / denom,
                'worker_wall_s': (sum(walls) / denom) if all(isinstance(w, (int, float)) for w in walls) else 'UNKNOWN'},
            'unknown_usage_requests': sum(c['unknown_usage_requests'] for c in rows),
            'paired_vs_NB_frozen_view': None if arm == 'NB' else frozen,
            'paired_vs_NB_measured_view': None if arm == 'NB' else measured}
    return {'schema': 't2a-table2-audit-summary-v1', 'registered': len(reg['plan']), 'ended': len(cells),
            'missing': [episode_name(r) for r in missing], 'complete': not missing,
            'classes_note': __doc__.split('G/H')[0].strip(), 'by_arm': by_arm,
            'cells': [cells[k] for k in sorted(cells)],
            'raw_records_modified': False}


def write(root):
    root = Path(root)
    sys.path.insert(0, str(root / 'code/memory/scripts'))
    import t2a_runtime as T
    result = summarize(root, T)
    out = root / 'table2-audit-summary.json'
    tmp = Path(str(out) + '.tmp')
    tmp.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + '\n')
    tmp.replace(out)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', type=Path, required=True)
    r = write(p.parse_args().root.resolve())
    print(json.dumps({k: r[k] for k in ('registered', 'ended', 'complete')}))
