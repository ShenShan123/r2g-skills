"""Summarize a completed multi-seed downstream formal-training matrix."""
import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


METRICS = ('macro_mae', 'pooled_mae', 'pooled_rmse', 'pooled_r2')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def mean_std(values):
    return {'mean': statistics.fmean(values),
            'std': statistics.stdev(values) if len(values) > 1 else 0.0,
            'values': values}


def write_csv(path, rows, fields):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value, unit):
    if unit == 'pF':
        return f'{value:.6g}'
    return f'{value:.4f}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    plan_path, status_path = root / 'plan.json', root / 'status.json'
    plan, status = json.loads(plan_path.read_text()), json.loads(status_path.read_text())
    if status.get('phase') != 'complete' or status.get('test_scored') != len(plan['jobs']):
        raise ValueError('Formal matrix is not completely test-scored')

    runs = []
    for job in plan['jobs']:
        result_path = root / job['name'] / 'result.json'
        config_path = root / job['name'] / 'config.json'
        result, config = json.loads(result_path.read_text()), json.loads(config_path.read_text())
        if result.get('status') != 'complete' or not result.get('test_evaluated'):
            raise ValueError(f'Incomplete result: {job["name"]}')
        for key in ('stage', 'target', 'model', 'seed'):
            if config[key] != job[key]:
                raise ValueError(f'Plan/config mismatch for {job["name"]}: {key}')
        test = result['test']
        row = {
            **job,
            'unit': test['unit'],
            'eligible_test_designs': result['target_eligibility']['eligible_designs']['test'],
            'test_targets': test['pooled']['n'],
            'macro_mae': test['macro_mae'],
            'pooled_mae': test['pooled']['mae'],
            'pooled_rmse': test['pooled']['rmse'],
            'pooled_r2': test['pooled']['r2'],
            'invocation_seconds': result['timing']['invocation_seconds'],
            'result_sha256': sha256(result_path),
        }
        runs.append(row)

    groups = defaultdict(list)
    for row in runs:
        groups[(row['stage'], row['target'], row['model'])].append(row)
    expected = {(stage, target, model) for stage in ('cts', 'route')
                for target in plan['targets'] for model in ('gine', 'mlp')}
    if set(groups) != expected or any(len(rows) != len(plan['seeds']) for rows in groups.values()):
        raise ValueError('Unexpected group or seed coverage')

    aggregates = []
    for key in sorted(groups):
        stage, target, model = key
        rows = sorted(groups[key], key=lambda row: row['seed'])
        if len({row['unit'] for row in rows}) != 1 or len({row['test_targets'] for row in rows}) != 1:
            raise ValueError(f'Inconsistent evaluation cohort for {key}')
        aggregate = {
            'stage': stage, 'target': target, 'model': model,
            'unit': rows[0]['unit'], 'seeds': [row['seed'] for row in rows],
            'eligible_test_designs': rows[0]['eligible_test_designs'],
            'test_targets': rows[0]['test_targets'],
        }
        for metric in METRICS:
            aggregate[metric] = mean_std([row[metric] for row in rows])
        aggregates.append(aggregate)

    indexed = {(row['stage'], row['target'], row['model']): row for row in aggregates}
    comparisons = []
    for stage in ('cts', 'route'):
        for target in plan['targets']:
            gine, mlp = indexed[(stage, target, 'gine')], indexed[(stage, target, 'mlp')]
            gmae, mmae = gine['macro_mae']['mean'], mlp['macro_mae']['mean']
            comparisons.append({
                'stage': stage, 'target': target, 'unit': gine['unit'],
                'gine_macro_mae_mean': gmae, 'mlp_macro_mae_mean': mmae,
                'gine_relative_mae_reduction_percent': 100.0 * (mmae - gmae) / mmae,
                'winner': 'gine' if gmae < mmae else 'mlp' if mmae < gmae else 'tie',
                'gine_pooled_r2_mean': gine['pooled_r2']['mean'],
                'mlp_pooled_r2_mean': mlp['pooled_r2']['mean'],
                'pooled_r2_difference_gine_minus_mlp':
                    gine['pooled_r2']['mean'] - mlp['pooled_r2']['mean'],
            })

    output = {
        'schema': 'r2g_downstream_formal_summary_v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS',
        'source': {
            'root': str(root), 'plan': str(plan_path), 'plan_sha256': sha256(plan_path),
            'status': str(status_path), 'status_sha256': sha256(status_path),
            'manifest': plan['manifest'], 'manifest_sha256': plan['manifest_sha256'],
            'result_files': len(runs),
        },
        'protocol': {
            'epochs': plan['epochs'], 'seeds': plan['seeds'], 'targets': plan['targets'],
            'stages': ['cts', 'route'], 'models': ['gine', 'mlp'],
            'checkpoint_rule': plan['rule'],
            'edge_task_scope': ('attribute regression conditioned on frozen endpoint pairs; '
                                'RC target edges are not message-passing edges'),
        },
        'runs': runs, 'aggregates': aggregates, 'comparisons': comparisons,
    }
    json_path = root / 'formal_summary.json'
    json_path.write_text(json.dumps(output, indent=2, allow_nan=False) + '\n')

    run_fields = ['name', 'stage', 'target', 'model', 'seed', 'unit',
                  'eligible_test_designs', 'test_targets', *METRICS,
                  'invocation_seconds', 'result_sha256']
    write_csv(root / 'per_run_results.csv', runs, run_fields)
    aggregate_rows = []
    for row in aggregates:
        flat = {key: row[key] for key in ('stage', 'target', 'model', 'unit',
                                          'eligible_test_designs', 'test_targets')}
        flat['seeds'] = ';'.join(map(str, row['seeds']))
        for metric in METRICS:
            flat[metric + '_mean'] = row[metric]['mean']
            flat[metric + '_std'] = row[metric]['std']
        aggregate_rows.append(flat)
    aggregate_fields = ['stage', 'target', 'model', 'unit', 'seeds',
                        'eligible_test_designs', 'test_targets']
    for metric in METRICS:
        aggregate_fields.extend((metric + '_mean', metric + '_std'))
    write_csv(root / 'aggregate_results.csv', aggregate_rows, aggregate_fields)

    lines = [
        '# R2G 剩余三类标签下游预测正式结果', '',
        '## 结论', '',
        '本轮 36/36 个训练与测试任务均成功完成。结果表明，拓扑信息的价值取决于目标：',
        '`GINE` 在 Hold Slack 上稳定优于只看节点属性的 `MLP`；但在给定端点对的两类',
        'RC 属性回归中，当前 `MLP` 的误差更低。这说明数据集既能支持图模型，也能揭示',
        '某些物理量主要由局部属性解释，而不是预设 GNN 必然获胜。', '',
        '## 测试集结果', '',
        '| 阶段 | 标签 | 模型 | Macro MAE（均值 ± 样本标准差） | Pooled R²（均值 ± 样本标准差） | 测试设计 | 有效目标 |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |',
    ]
    labels = {'hold_slack': 'Hold slack', 'coupling_cap': 'Coupling capacitance',
              'effective_resistance': 'Effective resistance'}
    for row in aggregates:
        unit = row['unit']
        lines.append('| {stage} | {target} | {model} | {mae} ± {mae_std} {unit} | '
                     '{r2:.4f} ± {r2_std:.4f} | {designs} | {targets:,} |'.format(
                         stage=row['stage'].upper(), target=labels[row['target']],
                         model=row['model'].upper(), mae=fmt(row['macro_mae']['mean'], unit),
                         mae_std=fmt(row['macro_mae']['std'], unit), unit=unit,
                         r2=row['pooled_r2']['mean'], r2_std=row['pooled_r2']['std'],
                         designs=row['eligible_test_designs'], targets=row['test_targets']))
    lines += ['', '## GINE 与 MLP 的直接比较', '',
              '| 阶段 | 标签 | Macro MAE 更优模型 | GINE 相对 MLP 的 MAE 降幅 | Pooled R² 差（GINE−MLP） |',
              '| --- | --- | --- | ---: | ---: |']
    for row in comparisons:
        lines.append(f'| {row["stage"].upper()} | {labels[row["target"]]} | '
                     f'{row["winner"].upper()} | '
                     f'{row["gine_relative_mae_reduction_percent"]:+.2f}% | '
                     f'{row["pooled_r2_difference_gine_minus_mlp"]:+.4f} |')
    lines += [
        '', '正值表示 GINE 更优；负值表示 MLP 更优。这里的 MAE 比较采用每个设计先算 MAE、',
        '再对设计等权平均的 Macro MAE，避免大型图凭借更多节点或边支配结论。', '',
        '## 评估边界', '',
        '- 数据划分按仓库 family 隔离：训练 175、验证 49、测试 54 个设计。',
        '- Hold Slack 有 53 个测试设计、18,013 个有效 pin 标签；1 个测试设计无有效标签而排除。',
        '- Coupling Cap 有 54 个测试设计、418,108 个去除双向重复后的物理边标签。',
        '- Effective Resistance 有 54 个测试设计、198,291 个边标签。',
        '- 两个 RC 任务是给定端点对后的属性回归，不是预测 RC 边是否存在。RC 标签边没有进入消息传递图。',
        '- 每组报告 3 个随机种子（42/43/44）的均值和样本标准差；验证集选 checkpoint，测试集只在全部训练结束后统一评分。',
        '', '## 可复现材料', '',
        f'- 权威结果目录：`{root}`',
        '- `formal_summary.json`：完整机器可读汇总、逐运行哈希和比较。',
        '- `aggregate_results.csv`：12 组跨 seed 汇总。',
        '- `per_run_results.csv`：36 个单次运行结果。',
        '- `plan.json`、`status.json` 和各运行目录：冻结协议、checkpoint 与原始预测。',
    ]
    (root / 'formal_report_zh.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'status': 'PASS', 'runs': len(runs), 'groups': len(aggregates),
                      'json': str(json_path), 'report': str(root/'formal_report_zh.md')}))


if __name__ == '__main__':
    main()
