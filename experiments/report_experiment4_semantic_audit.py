#!/usr/bin/env python3
"""Summarize an independent retrospective audit without inventing a total score."""
import argparse
from collections import Counter
import json
from pathlib import Path

from experiment4_semantic_audit import digest, save


def main(root):
    summary_path = root / 'summary.json'
    data = json.loads(summary_path.read_text())
    rows = [r for r in data['rows'] if 'method' in r]
    errors = [r for r in data['rows'] if 'method' not in r]
    methods = sorted({r['method'] for r in rows})
    groups = sorted({g for r in rows for g in r['groups']})
    verified_core = {
        method: dict(Counter(
            row.get('verified_core_status') or
            ('PASS' if all(group.get('status') == 'PASS' for group in row['groups'].values()) else 'FAIL')
            for row in rows if row['method'] == method
        ))
        for method in methods
    }
    counts = {m: {g: dict(Counter(r['groups'].get(g, {}).get('status', 'UNASSESSABLE')
                     for r in rows if r['method'] == m)) for g in groups} for m in methods}
    details, failed_checks = {}, []
    label_coverage = {method: {} for method in methods}
    for row in rows:
        path = root / 'cases' / row['method'] / (row['task'] + '.json')
        details[str(path)] = digest(path)
        case = json.loads(path.read_text())
        for check in case['checks']:
            if check['status'] in ('FAIL', 'UNASSESSABLE'):
                failed_checks.append(dict(method=row['method'], task=row['task'], **check))
            if check['stage'] == 'route' and check['group'] == 'label':
                bucket = label_coverage[row['method']].setdefault(check['check'], {
                    key: 0 for key in ('graph_entities', 'graph_finite', 'report_expected',
                                      'matched', 'missing', 'incorrect', 'unparsed_blocks')})
                evidence = check['evidence']
                for source, target in [('graph_entity_count', 'graph_entities'),
                                       ('graph_finite_labels', 'graph_finite'),
                                       ('report_expected_labels', 'report_expected'),
                                       ('matched', 'matched'), ('missing', 'missing'),
                                       ('incorrect', 'incorrect'), ('unparsed_blocks', 'unparsed_blocks')]:
                    bucket[target] += int(evidence.get(source, 0))
    result = {'role': data['study_role'], 'summary_sha256': digest(summary_path),
              'case_sha256': details, 'planned': data['planned_method_cases'], 'finished': len(rows),
              'oracle_errors': errors, 'group_counts': counts, 'failures_and_unassessable': failed_checks,
              'label_coverage_route_stage': label_coverage,
              'verified_core_case_counts': verified_core,
              'core_semantic_status': 'NOT_VERIFIED', 'unverified_dimensions': data['policy']['not_verified_dimensions']}
    save(root / 'aggregate.json', result)
    lines = ['# Experiment 4: Independent Semantic Audit', '',
             f'Role: {data["study_role"]}.', '',
             f'Completed method/design pairs: {len(rows)}/{data["planned_method_cases"]}. Oracle errors: {len(errors)}.', '',
             '## Scoped Check Results', '',
             'Cells are design counts: PASS / FAIL / UNASSESSABLE / NOT_APPLICABLE. A pass applies only to the implemented checks.', '',
             '| Method | ' + ' | '.join(groups) + ' |',
             '| --- | ' + ' | '.join('---' for _ in groups) + ' |']
    for method in methods:
        lines.append('| ' + method + ' | ' + ' | '.join('/'.join(str(counts[method][g].get(s, 0))
                      for s in ('PASS', 'FAIL', 'UNASSESSABLE', 'NOT_APPLICABLE')) for g in groups) + ' |')
    lines += ['', '## Verified Core Result', '',
              '| Method | Passed cases | Failed cases |', '| --- | ---: | ---: |']
    for method in methods:
        lines.append(f'| {method} | {verified_core[method].get("PASS", 0)} | '
                     f'{verified_core[method].get("FAIL", 0)} |')
    lines += ['', '## Label Coverage (Route Graph)', '',
              'Coverage is counted once per design from the route graph; repeated labels in earlier graph snapshots are not double-counted.', '',
              '| Method | Label | Finite graph values | Independently reported endpoints | Matched | Missing | Incorrect |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for method in methods:
        for check, values in sorted(label_coverage[method].items()):
            lines.append(f'| {method} | {check} | {values["graph_finite"]} | {values["report_expected"]} | '
                         f'{values["matched"]} | {values["missing"]} | {values["incorrect"]} |')
    lines += ['', '## Interpretation', '',
              '- Identity and core topology are checked against native Yosys parsing, not another converter.',
              '- Physical coordinates use OpenDB and the LEFs plus the permitted earlier-stage DEF.',
              '- Slack checks cover only canonical data endpoints present in the raw full-path reports. Missing report coverage is not success.',
              '- Routed wirelength and ground capacitance are independently checked only on route nets whose physical endpoints exactly match Yosys canonical endpoints.',
              '- `causal` checks selected forbidden early features and label-edge payloads. It is not a proof of complete noninterference.',
              '- Missing identity/schema needed by this adapter is UNASSESSABLE, not proof that every tensor is incorrect.',
              '- Keep schema/packaging compliance separate from semantic correctness and label coverage.',
              '- All methods remain NOT_VERIFIED for full semantic correctness. Do not publish a full-accuracy score from this partial audit.', '',
              '## Remaining Coverage', '']
    lines += ['- ' + d for d in data['policy']['not_verified_dimensions']]
    lines += ['', '## Evidence', '',
              '- [Aggregate and exact failures](aggregate.json)',
              '- [Source-bound summary](summary.json)',
              '- Per-case inputs, binaries, audit code and graph hashes are recorded under `cases/`.',
              '- Raw parser exports are retained under `oracles/`; original campaign artifacts were not changed.', '']
    (root / 'report.md').write_text('\n'.join(lines))
    print(json.dumps({'finished': len(rows), 'oracle_errors': len(errors), 'counts': counts}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    main(p.parse_args().root)
