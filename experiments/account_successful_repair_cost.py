#!/usr/bin/env python3
"""Count one earliest eligible successful API attempt per platform/design.

Failure, calibration and engineering costs remain visible in attempts.csv but
never enter accepted repair totals. Normal retries *inside* an accepted attempt
are all counted. Read-only toward runtime data; writes only the ledger output.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import io
import json
from pathlib import Path
import time

COUNTERS = ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_input_tokens',
            'uncached_input_tokens', 'cache_creation_input_tokens', 'reasoning_tokens')


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content)
    temp.replace(path)


def json_write(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def csv_write(path, rows):
    stream = io.StringIO()
    fields = list(dict.fromkeys(k for row in rows for k in row))
    if fields:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    write(path, stream.getvalue())


def assess(folder, case, source):
    state = read(folder / 'state.json', {})
    s = read(folder / 'summary.json', {})
    row = {'case_id': case['id'], 'platform': case['platform'],
           'attempt': str(folder), 'session_id': state.get('session_id'),
           'created': state.get('created', ''), 'code_commit': source.get('code_commit'),
           'host': source.get('host'), 'status': state.get('status', 'not_submitted'),
           'tokens': ((s.get('usage') or {}).get('known_reported_totals') or {}).get('total_tokens'),
           'orfs_wrapper_calls': s.get('orfs_wrapper_calls'),
           'orfs_make_launches': s.get('orfs_make_launches'),
           'eligible': False, 'reason': '', 'classification': ''}
    def reject(reason):
        row['reason'] = reason
        return row, None
    if not source.get('eligible', False):
        return reject(source.get('exclusion_reason', 'development_or_calibration'))
    if not s or s.get('status') != 'complete' or state.get('status') != 'complete':
        return reject(state.get('error') or 'attempt_not_closed')
    if any(state.get(k) for k in ('error', 'initial_transport_error', 'explanation_transport_error', 'chat_errors')):
        return reject('api_transport_or_agent_error')
    if s.get('session_id') != state.get('session_id'):
        return reject('session_identity_conflict')
    if source.get('cores_per_design') != 8 or not source.get('code_commit'):
        return reject('missing_frozen_code_or_eight_core_receipt')
    launch = read(folder.parent.parent / 'launch_parameters.json', {})
    if launch.get('cores_per_design') != 8 or launch.get('fixed_cores') is not True:
        return reject('missing_fixed_eight_core_launch_receipt')
    usage = s.get('usage', {})
    calls = usage.get('calls') or []
    if (not calls or usage.get('model_calls') != len(calls)
            or any(c.get('usage_reported') is not True or c.get('stream_complete') is not True
                   or c.get('total_tokens') is None for c in calls)
            or usage.get('totals', {}).get('total_tokens') is None):
        return reject('incomplete_provider_usage')
    if sum(c['total_tokens'] for c in calls) != usage['totals']['total_tokens']:
        return reject('provider_usage_total_conflict')
    if (s.get('orfs_audit_coverage') != 'paired_observed_events'
            or s.get('orfs_audit_errors') or any((s.get('orfs_audit_gaps') or {}).values())
            or not isinstance(s.get('orfs_wrapper_calls'), int) or s['orfs_wrapper_calls'] < 1
            or not isinstance(s.get('orfs_make_launches'), int) or s['orfs_make_launches'] < 1):
        return reject('incomplete_orfs_audit')
    audit = read(folder / 'orfs_audit.json', {})
    if (audit.get('coverage') != 'paired_observed_events'
            or audit.get('orfs_wrapper_calls') != s['orfs_wrapper_calls']
            or audit.get('orfs_make_launches') != s['orfs_make_launches']):
        return reject('orfs_audit_receipt_conflict')
    ds = s.get('designs') or []
    if len(ds) != 1 or len(s.get('job_ids', [])) != 1:
        return reject('not_one_fresh_design_attempt')
    d = ds[0]
    job = read(folder / (d['job_id'] + '.job.json'), {})
    if job.get('spec', {}).get('session_id') != s['session_id'] or job.get('spec', {}).get('platform') != case['platform']:
        return reject('job_session_or_platform_conflict')
    if job.get('spec', {}).get('params'):
        return reject('nondefault_or_forced_parameters')
    receipt = read(folder / 'input_receipt.json', {})
    expected = case.get('source_sha256') or {}
    evidence = read(folder / 'signoff_evidence.json', {})
    if not evidence:
        return reject('awaiting_signoff_evidence_export')
    if (not expected or receipt.get('historical_metadata_removed') is not True
            or any(receipt.get('sha256', {}).get(k) != v for k, v in expected.items())
            or evidence.get('source_input_sha256') != receipt.get('sha256')
            or evidence.get('session_id') != s['session_id']):
        return reject('original_input_hash_or_identity_conflict')
    ps = evidence.get('projects') or []
    if len(ps) != 1 or ps[0]['job_id'] != d['job_id'] or ps[0]['project'] != d['project']:
        return reject('project_evidence_identity_conflict')
    proof = ps[0]
    drc = proof['reports'].get('drc.json', {})
    lvs = proof['reports'].get('lvs.json', {})
    if ((d.get('signoff') or {}).get('dual_clean') is not True or drc.get('status') != 'clean'
            or drc.get('total_violations') != 0 or drc.get('drc_mode') != 'full'
            or drc.get('exit_code') != 0 or lvs.get('status') != 'clean'
            or lvs.get('mismatch_count') != 0):
        detail = (job.get('designs') or [{}])[0].get('detail') or {}
        return reject(detail.get('reason') or 'not_full_drc_lvs_clean')
    confirm = proof['reports'].get('signoff_manifest.json', {}).get('confirming_run', {})
    gds = proof.get('artifact_sha256', {}).get('gds')
    deff = proof.get('artifact_sha256', {}).get('def')
    if (not gds or not deff or not drc.get('run_tag') or drc.get('run_tag') != lvs.get('run_tag')
            or drc.get('gds_sha256') != gds or lvs.get('gds_sha256') != gds
            or confirm.get('consensus') is not True or confirm.get('gds_sha256') != gds
            or confirm.get('def_sha256') != deff or confirm.get('run_tag') != drc['run_tag']):
        return reject('signoff_not_bound_to_same_archived_layout')
    manifest_reports = proof['reports']['signoff_manifest.json'].get('reports', {})
    if any(manifest_reports.get(n, {}).get('sha256') != proof['report_sha256'].get(n)
           for n in ('drc.json', 'lvs.json')):
        return reject('signoff_report_digest_conflict')
    detail = (job.get('designs') or [{}])[0].get('detail') or {}
    verify = detail.get('verify') or {}
    if verify.get('total') == 0:
        return reject('verifier_infrastructure_failure_zero_checks')
    front = detail.get('frontend_fixes') or proof.get('frontend_fixes') or []
    flow = detail.get('flow_fixes') or proof.get('flow_fixes') or []
    applied = [f for f in detail.get('repair', [])
               if f.get('strategy') not in (None, 'none', 'stop_clean')
               and f.get('verdict') == 'cleared']
    # SPEF encoding changes parser representation, not physical design.
    physical = applied + [f for f in flow if (f.get('strategy') or f.get('recipe')) != 'spef_name_encoding']
    repaired = bool(front or flow or applied)
    kind = ('physical_and_frontend' if physical and front else 'physical' if physical else
            'frontend_or_backend_compatibility' if repaired else 'already_clean')
    strategies = sorted({str(f.get('strategy') or f.get('recipe') or f.get('kind'))
                         for f in front + flow + applied if f.get('strategy') or f.get('recipe') or f.get('kind')})
    row.update(eligible=True, reason='', classification=kind, tokens=usage['totals']['total_tokens'])
    accepted = {**row, 'job_ids': s['job_ids'], 'repair_observed': repaired,
                'strategies': strategies, 'provider_usage': usage['totals'],
                'dispatch_usage': s.get('dispatch_usage', {}).get('totals'),
                'explanation_usage': s.get('explanation_usage', {}).get('totals'),
                'model_calls': usage['model_calls'], 'orfs_make_kind_counts': s.get('orfs_make_kind_counts'),
                'dataset_verify': verify, 'confirming_run': confirm,
                'evidence_sha256': {n: sha(folder / n) for n in
                                    ('summary.json', 'input_receipt.json', 'orfs_audit.json',
                                     'signoff_evidence.json', 'state.json', d['job_id'] + '.job.json')}}
    return row, accepted


def render(config_path):
    config = read(config_path)
    out = Path(config['output'])
    population = {}
    for name in config['manifests']:
        for c in read(Path(name))['candidates']:
            if c['id'] in population:
                raise ValueError('Duplicate master case: ' + c['id'])
            population[c['id']] = c
    attempts, eligible = [], {}
    for source in config['sources']:
        for folder in sorted((Path(source['path']) / 'cases').glob('*')):
            case = population.get(folder.name)
            if case is None:
                continue  # calibration controls are outside the fixed population
            row, accepted = assess(folder, case, source)
            attempts.append(row)
            if accepted:
                eligible.setdefault(case['id'], []).append(accepted)
    selected = {cid: min(values, key=lambda a: (a['created'], a['attempt']))
                for cid, values in eligible.items()}
    prior = read(out / 'accepted_attempts.json', {})
    for cid, old in prior.items():
        if selected.get(cid) != old:
            raise ValueError('Accepted attempt changed or lost evidence; review required: ' + cid)
    json_write(out / 'accepted_attempts.json', selected)
    for row in attempts:
        accepted = selected.get(row['case_id'])
        row['counted'] = bool(accepted and accepted['attempt'] == row['attempt'])
        if row['eligible'] and not row['counted']:
            row['reason'] = 'later_success_excluded_first_eligible_success_retained'
    csv_write(out / 'attempts.csv', attempts)
    totals = {}
    for platform in sorted({c['platform'] for c in population.values()}):
        cases = [c for c in population.values() if c['platform'] == platform]
        success = [a for a in selected.values() if a['platform'] == platform]
        repaired = [a for a in success if a['repair_observed']]
        direct = [a for a in success if not a['repair_observed']]
        def aggregate(values):
            return {'cases': len(values), 'model_calls': sum(a['model_calls'] for a in values),
                    'orfs_wrapper_calls': sum(a['orfs_wrapper_calls'] for a in values),
                    'orfs_make_launches': sum(a['orfs_make_launches'] for a in values),
                    'provider_usage': {k: sum(a['provider_usage'][k] for a in values)
                                       if all(a['provider_usage'].get(k) is not None for a in values) else None
                                       for k in COUNTERS}}
        totals[platform] = {'population': len(cases), 'accepted_dual_clean': len(success),
                            'pending_or_unsuccessful': len(cases) - len(success),
                            'successful_repair_only': aggregate(repaired),
                            'already_clean_separate': aggregate(direct),
                            'repair_classes': dict(Counter(a['classification'] for a in repaired))}
        csv_write(out / f'{platform}_successful_cases.csv',
                  [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                    for k, v in a.items() if k != 'evidence_sha256'} for a in success])
    json_write(out / 'platform_success_totals.json', totals)
    pending = [{'case_id': cid, 'platform': c['platform'],
                'attempts': [{k: r[k] for k in ('attempt', 'session_id', 'status', 'reason')}
                             for r in attempts if r['case_id'] == cid]}
               for cid, c in population.items() if cid not in selected]
    json_write(out / 'pending_cases.json', pending)
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    lines = ['# 成功 API 修复的正式成本', '', f'更新：{now}（北京时间）。', '',
             '每例采用最早合格的完整成功会话。首次成功保留；排障、校准、失败轮次另存 attempts.csv，排除在正式费用之外。',
             '最终成功会话内正常自动重试产生的全部 token、ORFS 调用均计入。跨案例、跨平台不拼接调用或签核证据。',
             '主要成功条件是同一归档版图的完整 DRC clean、LVS clean。数据集检查单独记录；0/0 的环境故障不入账。',
             '这是成功条件下的成本，不是包含失败和研发费用的全量平均成本。', '',
             '| 平台 | 母清单 | 修复成功 | 直接双清（单列） | 待成功/待复核 | 正式 token | 正式 ORFS wrapper | 顶层 make |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for platform, t in totals.items():
        r, d = t['successful_repair_only'], t['already_clean_separate']
        lines.append(f"| {platform} | {t['population']} | {r['cases']} | {d['cases']} | {t['pending_or_unsuccessful']} | {r['provider_usage']['total_tokens']} | {r['orfs_wrapper_calls']} | {r['orfs_make_launches']} |")
    lines += ['', 'ORFS 主列是 run_orfs wrapper 次数；实际顶层 make（含 build/check 与 clean）另列，递归 make 不重复计。',
              'provider_usage 包括输入、输出、缓存等提供方字段；缓存 token 属于输入子集，不额外相加。', '',
              '- accepted_attempts.json：采用会话、原始输入/签核证据哈希、逐项成本。',
              '- nangate45_successful_cases.csv / sky130hd_successful_cases.csv：两平台逐项明细。',
              '- platform_success_totals.json：两平台正式汇总，直接双清另列。',
              '- attempts.csv：所有尝试及排除原因；pending_cases.json：未完成清单。', '']
    write(out / 'STATUS.md', '\n'.join(lines))
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.interval <= 60:
        parser.error('interval must be between 5 and 60 seconds')
    output = Path(read(args.config)['output'])
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.accounting.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            print(json.dumps(render(args.config), ensure_ascii=False), flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
