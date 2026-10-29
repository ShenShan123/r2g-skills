#!/usr/bin/env python3
"""Read-only memory-construction cost report for a FreePDK45 main50 run.

Source of truth is every build's append-only resource ledger, so failed and retried attempts are
included. Nothing in the run is modified; outputs go to <root>/construction-cost/.

Usage tokens are exact provider-reported values. A request without reported usage stays UNKNOWN
(never zero) and is counted separately. CNY is not reported per request by the provider; the
balance log gives account-level readings (shared with any other experiment on the same key), so
any CNY attribution from it is an estimate and is labelled as such.

  t2_construction_cost.py report  --root ROOT
  t2_construction_cost.py balance --root ROOT [--note TEXT]   # free balance endpoint, no tokens
"""
import argparse
import csv
import json
import time
from pathlib import Path

BUILDS = ('EXPEL', 'AWM', 'AMEM', 'REASONINGBANK')
FIELDS = ('requests', 'measured_requests', 'unknown_requests', 'non_200_requests', 'prompt_tokens',
          'cache_hit_tokens', 'cache_miss_tokens', 'completion_tokens', 'malformed_retries',
          'transport_retries', 'attempts')


def rows(path):
    p = Path(path)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def build_cost(root, fold, arm):
    project = fold/'controls/construction'/arm
    out = dict.fromkeys(FIELDS, 0)
    for e in rows(project/'reports/resource_ledger.jsonl'):
        if e.get('type') != 'llm_request':
            continue
        out['requests'] += 1
        if e.get('status') != 200:
            out['non_200_requests'] += 1
        u = e.get('usage_reported')
        if isinstance(u, dict) and all(isinstance(u.get(k), (int, float)) for k in ('prompt_tokens', 'completion_tokens')):
            out['measured_requests'] += 1
            hit = u.get('prompt_cache_hit_tokens', 0) or 0
            out['prompt_tokens'] += u['prompt_tokens']
            out['cache_hit_tokens'] += hit
            out['cache_miss_tokens'] += u.get('prompt_cache_miss_tokens', u['prompt_tokens'] - hit)
            out['completion_tokens'] += u['completion_tokens']
        else:
            out['unknown_requests'] += 1
    out['malformed_retries'] = len(list(project.glob('*.malformed-retry-1.claim.json')))
    out['transport_retries'] = len(list(project.glob('*.transport-retry-1.claim.json')))
    out['attempts'] = len(list((root/'logs').glob('build-%s-%s*.log' % (fold.name, arm))))
    built = fold/'controls'/(arm+'.json')
    d = json.loads(built.read_text()) if built.exists() else {}
    out['status'] = d.get('status', 'NOT_BUILT')
    out['final_attempt_wall_s'] = d.get('wall_s', d.get('construction_wall_s'))
    out['final_attempt_cpu_s'] = d.get('cpu_s', d.get('construction_cpu_s'))
    return out


def report(root):
    root = Path(root).resolve()
    folds = sorted(p for p in (root/'folds').iterdir() if p.is_dir())
    per, totals = [], {a: dict.fromkeys(FIELDS, 0) for a in BUILDS}
    for fold in folds:
        for arm in BUILDS:
            c = build_cost(root, fold, arm)
            per.append({'design': fold.name, 'arm': arm, **c})
            for k in FIELDS:
                totals[arm][k] += c[k]
            totals[arm]['built'] = totals[arm].get('built', 0) + (c['status'] == 'FROZEN')
    grand = {k: sum(t[k] for t in totals.values()) for k in FIELDS}
    phase = rows(root/'construction-phase/resource_ledger.jsonl')
    occupied = [e['hold_id'] for e in phase if e.get('type') == 'budget_unknown_occupancy']
    shares = {a: (totals[a]['cache_miss_tokens']/grand['cache_miss_tokens'] if grand['cache_miss_tokens'] else None)
              for a in BUILDS}
    balance = rows(root/'construction-cost/provider-balance-log.jsonl')
    out = {'schema': 't2-construction-cost-report-v1', 'root': str(root), 'generated': time.time(),
           'designs': len(folds), 'builds_registered': len(folds)*len(BUILDS),
           'arm_totals': totals, 'grand_total': grand, 'cache_miss_share_by_arm': shares,
           'unknown_usage_holds_occupied_in_phase_ledger': occupied,
           'notes': ['Exact provider-reported tokens; UNKNOWN requests counted separately, never as zero.',
                     'Includes every attempt (failed builds, explicit retries) from the append-only ledgers.',
                     'Cached input tokens are billed far below uncached ones; cost is driven by cache_miss and completion tokens.',
                     'final_attempt_wall_s/cpu_s cover only the attempt that produced the frozen memory.',
                     'Balance readings are account-level and shared with other experiments using the same key; any CNY split is an estimate.'],
           'provider_balance_readings': balance}
    dest = root/'construction-cost'
    dest.mkdir(exist_ok=True)
    (dest/'construction-cost.json').write_text(json.dumps({**out, 'per_build': per}, indent=1)+'\n')
    with (dest/'construction-cost-per-build.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['design', 'arm', 'status', *FIELDS, 'final_attempt_wall_s', 'final_attempt_cpu_s'])
        w.writeheader()
        w.writerows(per)
    print(json.dumps({'arm_totals': {a: {k: t[k] for k in ('built', 'requests', 'cache_miss_tokens', 'cache_hit_tokens',
                                                          'completion_tokens', 'unknown_requests', 'malformed_retries',
                                                          'transport_retries')} for a, t in totals.items()},
                      'grand_total': grand}, indent=1))


def balance(root, note=None):
    """Free account-balance endpoint (no model request, no tokens). The key is never printed."""
    import importlib.util
    import urllib.request
    root = Path(root).resolve()
    spec = importlib.util.spec_from_file_location('harness', root/'code/memory/scripts/r2g_memory_eval.py')
    H = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(H)
    req = urllib.request.Request('https://api.deepseek.com/user/balance',
                                 headers={'Authorization': 'Bearer '+H.llm_key('/data1/zhangdy/.llm_env')})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    row = {'time': time.time(), 'iso': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'is_available': d.get('is_available'),
           'balances': [{k: b.get(k) for k in ('currency', 'total_balance', 'granted_balance', 'topped_up_balance')}
                        for b in d.get('balance_infos', [])], 'note': note}
    dest = root/'construction-cost'
    dest.mkdir(exist_ok=True)
    with (dest/'provider-balance-log.jsonl').open('a') as f:
        f.write(json.dumps(row)+'\n')
    print(json.dumps(row))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('report', 'balance'))
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--note')
    a = ap.parse_args()
    report(a.root) if a.command == 'report' else balance(a.root, a.note)
