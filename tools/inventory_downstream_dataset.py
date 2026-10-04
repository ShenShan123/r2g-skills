#!/usr/bin/env python3
"""Read-only, bounded inventory of baseline artifacts and existing graph labels.

This does not run ORFS, certify timing, select on prediction accuracy, or freeze
a train/test split. Large physical artifacts are stat'ed, not read or copied.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from experiment4_protocol import REQUIRED_RESULTS, normalized_repo, read_json, sha256_file, size_band
from prepare_experiment4_unseen_inventory import task_source_profile


COVERAGE = ('unclocked_register_pins', 'unconstrained_endpoints',
            'input_ports_missing_delay', 'output_ports_missing_delay')
STAGES = ('floorplan', 'placement', 'cts', 'route')
MEANINGS = {
    'cell_congestion': 'Routing congestion at a cell location; dimensionless',
    'ir_drop_mV': 'Supply voltage drop; reserved if no valid source is present',
    'routed_wirelength_um': 'Post-route wire length mapped to a canonical net',
    'ground_cap_pF': 'Post-route extracted net capacitance to ground',
    'setup_slack_ns': 'Endpoint worst setup slack; negative values are valid labels',
    'hold_slack_ns': 'Endpoint worst hold slack; negative values are valid labels',
    'coupling_cap_pF': 'Extracted coupling capacitance between two nets',
    'effective_resistance_ohm': 'Extracted effective driver-to-sink resistance',
}


def coverage_state(result):
    values = [result.get('constraint_coverage', {}).get(k) for k in COVERAGE]
    if not all(type(v) is int and v >= 0 for v in values):
        return 'unknown'
    return 'zero_missing_counts' if not any(values) else 'nonzero_missing_counts'


def physical_record(task, projects):
    path = projects / task['task_id'] / 'baseline/repair_family_probe_result.json'
    row = {'task_id': task['task_id'], 'repo': normalized_repo(task['repo_url']),
           'commit': task['commit'], 'top_module': task['top_module'],
           'source_root': task['source_root'], 'source_closure_sha256': task.get('source_closure_sha256'),
           'result_path': str(path), 'result_present': path.is_file(),
           'raw_export_candidate': False}
    if not path.is_file():
        row['exclusion_reasons'] = ['missing_baseline_result']
        return row
    try:
        result = read_json(path)
    except (OSError, ValueError) as exc:
        row['exclusion_reasons'] = ['unreadable_baseline_result: ' + str(exc)]
        return row
    row.update(result_sha256=sha256_file(path), mapped_cells=result.get('mapped_cells'),
               physical_clean_flag=result.get('strict_clean'),
               publication_clean_flag=result.get('publication_strict_clean'),
               coverage_state=coverage_state(result),
               coverage_counts=result.get('constraint_coverage', {}),
               timing_evaluation_incomplete=result.get('timing_evaluation_incomplete'),
               failure_signatures=result.get('normalized_failure_signature', []),
               completed_at=result.get('completed_at'), run_dir=result.get('run_dir'),
               metrics=result.get('metrics', {}))
    issues = []
    if result.get('task_id') != task['task_id']:
        issues.append('task_identity_mismatch')
    if not size_band(int(result.get('mapped_cells') or 0)):
        issues.append('mapped_cells_outside_100_10000')
    for flag in ('environment_failure', 'execution_interrupted', 'runtime_budget_failure',
                 'input_qualification_failure', 'scale_ineligible', 'capacity_infeasible',
                 'unclassified_execution_failure'):
        if result.get(flag):
            issues.append(flag)
    run = Path(result.get('run_dir') or '/nonexistent')
    # A final record may point at a deleted run or a different campaign.
    if not run.is_absolute() or not run.resolve().is_relative_to(path.parent.resolve()):
        issues.append('run_outside_baseline_project')
        artifacts = {}
    else:
        artifacts = {name: {'path': str(run / 'results' / name),
                           'bytes': (run / 'results' / name).stat().st_size}
                     for name in REQUIRED_RESULTS if (run / 'results' / name).is_file()}
    missing = [name for name in REQUIRED_RESULTS
               if name not in artifacts or artifacts[name]['bytes'] == 0]
    if missing:
        issues.append('missing_or_empty_physical_artifacts')
    row.update(artifacts=artifacts, missing_artifacts=missing,
               raw_export_candidate=not issues, exclusion_reasons=issues)
    # Timing/coverage issues do not invalidate wirelength/capacitance by themselves.
    # They remain explicit restrictions, not silently promoted to clean signoff.
    row['timing_label_review_required'] = (row['coverage_state'] != 'zero_missing_counts'
                                         or result.get('timing_evaluation_incomplete') is not False)
    return row


def verify_sources(task, archive_index, cache):
    root = Path(task['source_root']).resolve()
    expected = archive_index.get((str(root), task.get('source_closure_sha256')))
    if expected is None:
        return {'status': 'archive_manifest_not_found'}
    cache_key = (str(root), task['source_closure_sha256'])
    if cache_key not in cache:
        failures, checked = [], 0
        for item in expected:
            path = (root / item['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                failures.append(item['path'])
            elif sha256_file(path) != item['sha256']:
                failures.append(item['path'])
            checked += 1
        cache[cache_key] = {'status': 'PASS' if checked and not failures else 'FAIL',
                            'checked_files': checked, 'failed_files': failures}
    return cache[cache_key]


def group_sources(rows):
    parents = {r['task_id']: r['task_id'] for r in rows}
    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    by_key = {}
    for row in rows:
        for field in ('repo', 'normalized_source_sha256', 'structural_source_sha256'):
            value = row.get(field)
            if not value:
                continue
            previous = by_key.setdefault((field, value), row['task_id'])
            a, b = sorted((root(previous), root(row['task_id'])))
            parents[b] = a
    groups = defaultdict(list)
    for row in rows:
        row['provisional_family_id'] = root(row['task_id'])
        groups[row['provisional_family_id']].append(row['task_id'])
    return dict(sorted(groups.items()))


def aggregate_labels(graphs):
    columns = {}
    for graph in graphs:
        if graph['status'] != 'recorded_pass':
            continue
        seen = set()
        for item in graph['label_columns']:
            key = (item['entity_type'], item['column'], item['unit'])
            if key in seen:
                raise ValueError('Duplicate label column in one design')
            seen.add(key)
            entry = columns.setdefault(key, dict(entity_type=key[0], column=key[1], unit=key[2],
                description=MEANINGS.get(key[1], 'See extraction schema'), designs_present=0,
                designs_with_valid_labels=0, total_count=0, valid_count=0))
            entry['designs_present'] += 1
            entry['designs_with_valid_labels'] += item['mask_valid_count'] > 0
            entry['total_count'] += item['total_count']
            entry['valid_count'] += item['mask_valid_count']
    for entry in columns.values():
        entry['coverage_fraction'] = entry['valid_count'] / entry['total_count'] if entry['total_count'] else None
    return list(columns.values())


def inventory_graphs(graph_root):
    rows = []
    for folder in sorted(graph_root.iterdir()):
        if not folder.is_dir() or not folder.name.startswith('exp1_'):
            continue
        base = folder / 'generated'
        stats_path = base / 'statistics/four_stage_data_statistics.json'
        validation_path = base / 'four_stage.validation.json'
        row = {'task_id': folder.name, 'graph_root': str(base), 'status': 'missing_records'}
        if stats_path.is_file() and validation_path.is_file():
            stats, validation = read_json(stats_path), read_json(validation_path)
            missing = [s for s in STAGES if not (base / 'stages' / s / 'heterograph.pt').is_file()]
            available = not missing
            row.update(status='recorded_pass' if available and stats['status'] == validation['status'] == 'PASS'
                       else 'requires_review', statistics_sha256=sha256_file(stats_path),
                       validation_sha256=sha256_file(validation_path),
                       missing_stages=missing, node_counts=stats['stages']['route']['node_counts'],
                       feature_schema={k: v for k, v in stats['stages']['route']['tensor_hashes'].items()
                                       if k.endswith('/x_schema')},
                       label_columns=[{k: c[k] for k in ('entity_type', 'column', 'unit',
                                                        'total_count', 'mask_valid_count')}
                                      for c in stats['column_statistics'] if c['stage'] == 'route'
                                      and c['tensor'] in ('y', 'edge_y')])
        rows.append(row)
    return rows


def build(args):
    plan, registry = read_json(args.plan), read_json(args.registry)
    tasks = plan['tasks']
    if len({t['task_id'] for t in tasks}) != len(tasks):
        raise ValueError('Duplicate planned task ID')
    archive_index = {}
    for record in registry['records']:
        key = (str(Path(record['archive_source_root']).resolve()), record['source_evidence']['closure_sha256'])
        files = record['source_evidence']['files']
        if key in archive_index and archive_index[key] != files:
            raise ValueError('Conflicting archive manifests')
        archive_index[key] = files
    records, cache = [], {}
    for i, task in enumerate(tasks):
        row = physical_record(task, args.projects)
        row['source_verification'] = verify_sources(task, archive_index, cache)
        if row['source_verification']['status'] == 'PASS':
            try:
                profile = task_source_profile(task)
                row.update({k: v for k, v in profile.items() if k != 'structural_shingles'})
            except (OSError, ValueError) as exc:
                row['source_profile_error'] = str(exc)
        row['export_candidate'] = (row['raw_export_candidate'] and
                                    row['source_verification']['status'] == 'PASS' and
                                    bool(row.get('normalized_source_sha256')))
        records.append(row)
        if (i + 1) % 50 == 0:
            print(f'Inventoried {i + 1}/{len(tasks)} planned designs', flush=True)
    groups = group_sources(records)
    graphs = inventory_graphs(args.graph_root)
    labels = aggregate_labels(graphs)
    index = {r['task_id']: r for r in records}
    for graph in graphs:
        graph['baseline_inventory'] = {k: index.get(graph['task_id'], {}).get(k) for k in (
            'export_candidate', 'physical_clean_flag', 'coverage_state', 'timing_label_review_required',
            'provisional_family_id')}
    candidates = [r for r in records if r['export_candidate']]
    summary = {
        'qualified_unique_designs': registry['unique_design_count'],
        'planned_clocked_tasks': len(tasks), 'clock_review_entries': len(plan['unresolved']),
        'baseline_records_present': sum(r['result_present'] for r in records),
        'export_candidates': len(candidates),
        'export_candidates_by_physical_clean_flag': dict(Counter(str(r.get('physical_clean_flag')) for r in candidates)),
        'export_candidates_by_coverage': dict(Counter(r.get('coverage_state') for r in candidates)),
        'export_candidates_by_size': dict(Counter(size_band(r['mapped_cells']) for r in candidates)),
        'candidate_provisional_families': len({r['provisional_family_id'] for r in candidates}),
        'source_verification': dict(Counter(r['source_verification']['status'] for r in records)),
        'exclusion_reasons': dict(Counter(reason for r in records for reason in r.get('exclusion_reasons', []))),
        'existing_graph_status': dict(Counter(r['status'] for r in graphs)),
        'existing_graph_nodes': dict(sum((Counter(r['node_counts']) for r in graphs
                                         if r['status'] == 'recorded_pass'), Counter())),
        'existing_graphs_with_zero_missing_constraint_counts': sum(
            r['status'] == 'recorded_pass' and r['baseline_inventory']['coverage_state'] == 'zero_missing_counts'
            for r in graphs),
        'valid_label_field_count': sum(c['valid_count'] > 0 for c in labels),
        'reserved_empty_label_field_count': sum(c['valid_count'] == 0 for c in labels),
    }
    other_projects = sorted(p.name for p in args.projects.iterdir()
                            if p.is_dir() and p.name not in index)
    return dict(schema='r2g_downstream_artifact_inventory_v1', created_at=datetime.now(timezone.utc).isoformat(),
        summary=summary, family_reviewed=False, records=records, provisional_families=groups,
        graph_records=graphs, label_dictionary=labels, unplanned_projects=other_projects,
        unresolved=plan['unresolved'], provenance={str(p): sha256_file(p)
            for p in (args.plan, args.registry, Path(__file__),
                      Path(__file__).with_name('prepare_experiment4_unseen_inventory.py'))},
        scope={'baseline_projects': str(args.projects), 'graph_root': str(args.graph_root),
               'family_rule': 'same repository OR exact normalized RTL OR identifier-anonymized exact RTL',
               'new_graph_export_executed': False, 'new_graph_audit_executed': False,
               'physical_artifacts_content_verified': False,
               'graph_statistics_counting': 'route snapshot only; shared labels not multiplied by four',
               'mapped_cell_scope': '100-10000 in the physical baseline record, not the earlier acquisition precheck',
               'limitations': ['Source heuristics are not formal equivalence; family review remains required.',
                   'Only named canonical baseline projects are counted; historical retries are not merged.',
                   'Export candidates are not yet graphs or strict-clean designs.',
                   'Available timing labels with incomplete constraints are not certified timing targets.',
                   'Only the explicitly supplied graph root is aggregated; historical versions are not mixed.']})


def markdown(data):
    summary = data['summary']
    lines = ['# Dataset Artifact Inventory', '',
        'Read-only preparation snapshot, not formal GNN results or a new signoff audit.', '',
        '| Item | Count |', '| --- | ---: |']
    for key, value in summary.items():
        if type(value) is int:
            lines.append(f'| {key} | {value} |')
    lines += ['', '## Existing Graph Label Coverage', '',
        'Counted once per design, not once per prediction stage. Edge counts are stored directed rows.', '',
        '| Entity | Label | Unit | Designs with valid labels | Valid / total |',
        '| --- | --- | --- | ---: | ---: |']
    for c in data['label_dictionary']:
        lines.append(f"| {c['entity_type'].replace('|', '/')} | {c['column']} | {c['unit']} | "
                     f"{c['designs_with_valid_labels']} | {c['valid_count']} / {c['total_count']} |")
    lines += ['', '## Interpretation', '',
        '- Physical clean flags and constraint-count completeness are reported separately.',
        '- Wirelength/capacitance candidates with timing failures are a separate research tier, not clean signoff.',
        '- Missing labels are not zero labels. Reserved empty fields are not dataset contributions.',
        '- Family IDs are provisional; no training split was created or approved.',
        '- The 24-design pilot manifest is unchanged; no formal training was launched.', '',
        'Full per-design paths, hashes, exclusions and family groups are in inventory.json.', '']
    lines += ['## Label Definitions', '']
    for c in data['label_dictionary']:
        lines.append(f"- `{c['entity_type']}/{c['column']}`: {c['description']} ({c['unit']}).")
    lines += ['', '## Feature Schema', '',
              'Fields provided by the dataset, not all fields permitted as GNN input. '
              'Use the training whitelist; graph IDs and post-route auxiliary relations are excluded.', '']
    schemas = defaultdict(set)
    for graph in data['graph_records']:
        if graph['status'] == 'recorded_pass':
            for kind, fields in graph['feature_schema'].items():
                schemas[kind].update(fields)
    for kind, fields in sorted(schemas.items()):
        lines += [f'### {kind} ({len(fields)} fields)', '', ', '.join(f'`{f}`' for f in sorted(fields)), '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('plan', 'registry', 'projects', 'graph-root', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new output directory; do not replace existing inventory')
    data = build(args)
    args.output.mkdir(parents=True)
    (args.output / 'inventory.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    (args.output / 'README.md').write_text(markdown(data))
    print(json.dumps(data['summary'], indent=2))


if __name__ == '__main__':
    main()
