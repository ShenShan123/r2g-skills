"""Prepare a separate group split and bounded pilots from completed graph exports."""
import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gnn-node'))
from stage_data import digest, save_json, validate_manifest
from experiment4_protocol import load_source_index
from experiment4_semantic_audit import read_routed_lengths
from prepare_experiment4_unseen_inventory import (
    normalized_tokens, structural_similarity, task_source_profile)


def read(path):
    return json.loads(Path(path).read_text())


def source_review(root, source_plan, output):
    if output.exists():
        raise FileExistsError(output)
    plan = read(root / 'plan.json')
    tasks = load_source_index(source_plan)
    partial = read(root / 'family_map_partial_v1.json')
    if partial['plan_sha256'] != digest(root / 'plan.json'):
        raise ValueError('Partial family review belongs to another export plan')
    parents = {r['task_id']: r['task_id'] for r in plan['rows']}

    def find(key):
        while parents[key] != key:
            key = parents[key]
        return key

    def merge(a, b):
        a, b = sorted((find(a), find(b)))
        parents[b] = a

    groups, files, profiles, evidence, joins = {}, defaultdict(list), {}, {}, []
    for row in plan['rows']:
        key = row['task_id']
        group = partial['design_to_family'][key]
        if group in groups:
            merge(key, groups[group])
        groups[group] = key
        task = tasks[key]
        profile = task_source_profile(task)
        for field in ('normalized_source_sha256', 'structural_source_sha256'):
            if profile[field] != row[field]:
                raise ValueError('Source profile changed: ' + key)
        profiles[key] = profile
        paths = []
        source_root = Path(task['source_root']).resolve()
        for name in task['rtl_files']:
            path = (source_root / name).resolve()
            if source_root not in path.parents:
                raise ValueError('Source outside closure')
            tokens = normalized_tokens(path.read_text(errors='replace'), anonymize_identifiers=False)
            paths.append({'path': str(path), 'sha256': digest(path), 'tokens': len(tokens)})
            # Tiny include files alone must not join otherwise unrelated designs.
            if len(tokens) >= 80:
                fingerprint = hashlib.sha256('\n'.join(tokens).encode()).hexdigest()
                files[fingerprint].append((key, str(path)))
        evidence[key] = paths
    for fingerprint, entries in files.items():
        first, first_path = entries[0]
        for key, path in entries[1:]:
            if find(first) != find(key):
                joins.append(dict(left=first, right=key, reason='shared_normalized_rtl_file',
                                  paths=[first_path, path], fingerprint=fingerprint))
                merge(first, key)
    pairs_checked = 0
    for left, right in itertools.combinations(sorted(profiles), 2):
        pairs_checked += 1
        if find(left) == find(right):
            continue
        a, b = profiles[left], profiles[right]
        if min(a['structural_token_count'], b['structural_token_count']) < 80:
            continue
        similarity = structural_similarity(a, b)
        if similarity >= .5:
            joins.append(dict(left=left, right=right, reason='structural_7_token_jaccard',
                              similarity=similarity))
            merge(left, right)
    mapping = {key: find(key) for key in parents}
    save_json(output, dict(family_reviewed=False, review_status='automated_evidence_ready',
        design_to_family=mapping, added_joins=joins, pairs_checked=pairs_checked,
        before_families=partial['after_families'], after_groups=len(set(mapping.values())),
        source_evidence=evidence, source_plan_sha256=digest(source_plan),
        export_plan_sha256=digest(root / 'plan.json'),
        partial_review_sha256=digest(root / 'family_map_partial_v1.json'),
        code_sha256={str(p): digest(p) for p in (Path(__file__),
            Path(__file__).with_name('prepare_experiment4_unseen_inventory.py'))},
        limits='Conservative source-similarity groups, not formal equivalence or proof of no shared ancestry.'))
    print(json.dumps(dict(groups=len(set(mapping.values())), added_joins=len(joins))), flush=True)


def assign_groups(records, forced_train, seed=20260917):
    groups = sorted({r['family_id'] for r in records})
    forced = {r['family_id'] for r in records if r['design_id'] in forced_train}
    available = [g for g in groups if g not in forced]
    random.Random(seed).shuffle(available)
    n = max(1, int(len(groups) * .2))
    if len(available) < 2*n+1:
        raise ValueError('Insufficient unexposed groups for three splits')
    assignment = {g: 'test' if i < n else 'validation' if i < 2*n else 'train'
                  for i, g in enumerate(available)}
    assignment.update({g: 'train' for g in forced})
    return [dict(r, split=assignment[r['family_id']]) for r in records]


def valid_common(counts):
    return all(counts[s][t]['valid'] > 0 for s in ('cts', 'route')
               for t in ('wirelength', 'congestion'))


def wire_audit(record):
    folder = Path(record['graphs']['route']['path']).parents[2]
    cfg_path = folder.parent / 'method_config.json'
    cfg = read(cfg_path)
    csv_path = folder / 'labels/net_wirelength_Cg.csv'
    raw_path = Path(cfg.get('label_def') or cfg['route_def'])
    physical = read_routed_lengths(raw_path)
    checked, valid, pending, errors, maximum = 0, 0, [], [], 0.
    with csv_path.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len({r['net_name'] for r in rows}) != len(rows):
        raise ValueError('Duplicate net names')
    for row in rows:
        if not int(row['wirelength_valid']):
            continue
        valid += 1
        if row['wirelength_label_unit'] != 'um' or row['wirelength_label_transform'] != 'none':
            raise ValueError('Unexpected wirelength units')
        value = float(row['wirelength_um'])
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid supervised wirelength')
        name = row['net_name'].replace('\\', '')
        if (int(row['route_segment_net_count']) != 1 or
                int(row['route_direct_net_count']) != 1 or name not in physical):
            pending.append(row['net_name'])
            continue
        error = abs(value-physical[name])
        maximum = max(maximum, error)
        checked += 1
        if not math.isclose(value, physical[name], rel_tol=1e-5, abs_tol=.0011):
            errors.append(dict(net=name, csv_um=value, def_um=physical[name]))
    return dict(design_id=record['design_id'], status='FAIL' if errors else 'PASS_DIRECT_SUBSET',
                total=len(rows), valid=valid, checked_direct=checked,
                lineage_not_independently_verified=pending, errors=errors,
                max_abs_error_um=maximum,
                inputs={str(p): digest(p) for p in (cfg_path, csv_path, raw_path)})


def prepare(root, review_path, output, previous_manifests):
    if (output / 'manifest.json').exists():
        raise FileExistsError('Do not replace an existing split')
    review = read(review_path)
    if review['export_plan_sha256'] != digest(root / 'plan.json'):
        raise ValueError('Source review does not match export')
    prior, prior_hashes = set(), {}
    for path in previous_manifests:
        prior_hashes[str(path)] = digest(path)
        # Historical test graphs were not model-evaluated; only development exposure counts.
        prior.update(r['design_id'] for r in read(path)['records'] if r['split'] != 'test')
    records, excluded, congestion, coverage, wires = [], [], [], [], []
    checkpoints = sorted((root / 'checkpoints').glob('*.json'))
    if len(checkpoints) != 280 or read(root / 'status.json')['status'] != 'queue_complete':
        raise ValueError('Expected completed 280-design export')
    for path in checkpoints:
        state = read(path)
        if state['status'] != 'PASS' or state['audit']['status'] != 'PASS':
            raise ValueError('Unexpected export/audit failure: ' + path.name)
        record = copy.deepcopy(state['record'])
        for info in record['graphs'].values():
            if digest(info['path']) != info['sha256'] or digest(info['validation_path']) != info['validation_sha256']:
                raise ValueError('Graph/validation artifact changed')
        for p, expected in state['audit']['inputs'].items():
            if digest(p) != expected:
                raise ValueError('Audited input changed: ' + p)
        wire_path = output / 'wire_checks' / path.name
        if wire_path.exists():
            wire = read(wire_path)
            if any(digest(p) != h for p, h in wire['inputs'].items()):
                raise ValueError('Wire audit input changed')
        else:
            wire = wire_audit(record)
            save_json(wire_path, wire)
        wires.append(wire)
        if wire['errors']:
            raise ValueError('Wire numeric mismatch: ' + record['design_id'])
        coverage.append(dict(design_id=record['design_id'], counts=state['label_counts'],
                             review_flag=state['readiness']))
        if not valid_common(state['label_counts']):
            excluded.append(dict(design_id=record['design_id'], reason='no_valid_congestion_supervision',
                                 label_counts=state['label_counts'], artifacts_retained=True))
        else:
            record['family_id'] = review['design_to_family'][record['design_id']]
            records.append(record)
            congestion.append(state['audit'])
        print(json.dumps(dict(checked=len(wires), design=record['design_id'],
                              wire_checked=wire['checked_direct'],
                              wire_pending=len(wire['lineage_not_independently_verified']))), flush=True)
    records = assign_groups(records, prior)
    save_json(output / 'congestion_audit.json', dict(status='PASS', records=congestion,
              provenance='Reused independent raw-DEF/LEF checks from export; current input hashes reverified.'))
    manifest = dict(schema='r2g_downstream_training_preparation_v1', family_reviewed=False,
        pilot_subset=False, split_seed=20260917, records=records, excluded=excluded,
        group_review={'path': str(review_path), 'sha256': digest(review_path)},
        previous_development_manifests=prior_hashes, forced_training_designs=sorted(prior),
        eligibility_rule='Positive valid target counts for both targets and both stages; missing targets stay masked.',
        congestion_audit={'path': str(output / 'congestion_audit.json'),
                          'sha256': digest(output / 'congestion_audit.json')},
        formal_training_ready=False,
        pending=['Review automated source joins', 'Independent numeric checks for renamed/split wirelength nets'])
    validate_manifest(manifest, pilot=True)
    save_json(output / 'manifest.json', manifest)
    save_json(output / 'coverage.json', dict(records=coverage, excluded=excluded))
    save_json(output / 'wire_audit.json', dict(records=wires, status='DIRECT_SUBSET_CHECKED',
        checked_direct=sum(r['checked_direct'] for r in wires), valid=sum(r['valid'] for r in wires),
        pending_lineage=sum(len(r['lineage_not_independently_verified']) for r in wires)))
    summary = dict(exported=len(checkpoints), common_designs=len(records), excluded=len(excluded),
        split_counts=dict(Counter(r['split'] for r in records)),
        groups=len({r['family_id'] for r in records}),
        split_groups={s: len({r['family_id'] for r in records if r['split']==s})
                      for s in ('train', 'validation', 'test')},
        formal_training_ready=False, test_evaluated=False)
    save_json(output / 'summary.json', summary)
    # Stratified software pilot inherits the full split, never selecting by accuracy.
    chosen, used = [], set()
    for split, per_band in (('train', 2), ('validation', 1), ('test', 1)):
        for low, high in ((100, 499), (500, 1999), (2000, 10000)):
            candidates = sorted((r for r in records if r['split']==split and low <= r['mapped_cells'] <= high),
                                key=lambda r: (r['mapped_cells'], r['design_id']))
            count = 0
            for row in candidates:
                if row['family_id'] in used:
                    continue
                chosen.append(row); used.add(row['family_id']); count += 1
                if count == per_band:
                    break
            if count != per_band:
                raise ValueError('Insufficient pilot groups in size band')
    pilot = dict(manifest, pilot_subset=True, records=chosen)
    validate_manifest(pilot, pilot=True)
    save_json(output / 'pilot_manifest.json', pilot)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('sources')
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--source-plan', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--previous-manifest', type=Path, action='append', default=[])
    args = parser.parse_args()
    if args.mode == 'sources':
        source_review(args.root, args.source_plan, args.output)
    else:
        prepare(args.root, args.review, args.output, args.previous_manifest)


if __name__ == '__main__':
    main()
