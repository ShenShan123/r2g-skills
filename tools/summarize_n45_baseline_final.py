"""Create an auditable inventory for the completed Nangate45 baseline."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def derived_category(record):
    signatures = tuple((record.get('physical_result') or {}).get(
        'normalized_failure_signature') or [])
    if record.get('trial_timeout'):
        return 'timeout_not_repair_evidence'
    if 'PDN-0185' in signatures:
        return 'floorplan_too_small_for_default_pdn'
    return record['status']


def build(root):
    plan = read(root / 'plan.json')
    tasks = {task['task_id']: task for task in plan['tasks']}
    rows = []
    missing = []
    signature_counts = Counter()
    combination_counts = Counter()
    reproduction = []
    for task_id, task in sorted(tasks.items()):
        complete = root / 'jobs' / task_id / 'complete.json'
        if task.get('eligibility') != 'queued':
            rows.append({
                'task_id': task_id,
                'repo_url': task['key'][0],
                'top_module': task['key'][2],
                'mapped_cells': task.get('exp1_mapped_cells'),
                'raw_status': task['eligibility'],
                'category': task['eligibility'],
                'failure_signatures': '',
                'elapsed_seconds': '',
                'evidence_path': '',
            })
            continue
        if not complete.is_file():
            missing.append(task_id)
            continue
        record = read(complete)
        physical = record.get('physical_result') or {}
        signatures = tuple(physical.get('normalized_failure_signature') or [])
        category = derived_category(record)
        row = {
            'task_id': task_id,
            'repo_url': task['key'][0],
            'top_module': task['key'][2],
            'mapped_cells': physical.get('mapped_cells', task.get('exp1_mapped_cells')),
            'raw_status': record['status'],
            'category': category,
            'failure_signatures': ';'.join(signatures),
            'elapsed_seconds': record.get('elapsed_seconds'),
            'evidence_path': str(complete),
        }
        rows.append(row)
        if record['status'] == 'physical_failure_pending_reproduction':
            combination_counts[signatures] += 1
            signature_counts.update(signatures)
            reproduction.append({
                'task_id': task_id,
                'repo_url': task['key'][0],
                'source_commit': task['key'][1],
                'top_module': task['key'][2],
                'source_closure_sha256': task['source_closure_sha256'],
                'mapped_cells': row['mapped_cells'],
                'first_run_elapsed_seconds': row['elapsed_seconds'],
                'first_run_signatures': list(signatures),
                'admission_status': 'requires_independent_same_protocol_reproduction',
            })

    counts = Counter(row['category'] for row in rows)
    inventory = {
        'schema': 'r2g_nangate45_baseline_final_inventory_v1',
        'platform': 'nangate45',
        'population': len(tasks),
        'physical_queue': plan['runnable'],
        'clock_review_pending': plan['clock_review'],
        'complete_records': sum(bool(row['evidence_path']) for row in rows),
        'missing_queued_records': missing,
        'categories': dict(sorted(counts.items())),
        'physical_failure_candidates': len(reproduction),
        'repair_challenges_admitted': 0,
        'interpretation': ('Single-run physical failures are candidates only. They become repair '
                           'challenges only after an independent same-protocol reproduction.'),
        'frozen_evidence': {
            name: sha256(root / name) for name in ('plan.json', 'protocol.md', 'code_snapshot.json')
        },
    }
    signature_rows = [
        {'failure_signature': key, 'design_count': value}
        for key, value in signature_counts.most_common()
    ]
    combination_rows = [
        {'failure_signatures': list(key), 'design_count': value}
        for key, value in combination_counts.most_common()
    ]
    return inventory, rows, signature_rows, combination_rows, reproduction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    root = args.campaign.resolve()
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    inventory, rows, signatures, combinations, reproduction = build(root)
    write_json(reports / 'final_baseline_inventory.json', inventory)
    write_json(reports / 'failure_signature_counts.json', {
        'individual': signatures, 'combinations': combinations,
    })
    write_json(reports / 'reproduction_candidates.json', {
        'count': len(reproduction),
        'selection_policy': 'all single-run physical failures; no repair admission yet',
        'records': reproduction,
    })
    with (reports / 'design_outcomes.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        '# Nangate45 Baseline 终态清单', '',
        f"- 总体：{inventory['population']} 个独立设计。",
        f"- 进入物理队列：{inventory['physical_queue']}；时钟待审查：{inventory['clock_review_pending']}。",
        f"- 已形成物理记录：{inventory['complete_records']}；缺失记录：{len(inventory['missing_queued_records'])}。",
        '', '## 分类', '', '| 类别 | 数量 |', '|---|---:|',
    ]
    lines += [f'| `{key}` | {value} |' for key, value in inventory['categories'].items()]
    lines += ['', '## 解释边界', '',
              f"当前有 {inventory['physical_failure_candidates']} 个单次物理失败候选，尚未直接计入正式 Repair Challenge。",
              '只有在相同冻结协议下独立复现相同失败族后，才进入后续实验三挑战池。',
              'Timeout、约束/时序不完整、输入准备问题和时钟待审查不进入修复成功率分母。',
              '', '机器可读证据见 `final_baseline_inventory.json`、`design_outcomes.csv`、',
              '`failure_signature_counts.json` 与 `reproduction_candidates.json`。', '']
    (reports / 'final_baseline_report_zh.md').write_text('\n'.join(lines))
    print(json.dumps(inventory, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
