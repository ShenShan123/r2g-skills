#!/usr/bin/env python3
"""Freeze a deterministic Experiment 4 cohort from an audited unseen inventory."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from experiment4_protocol import (
    CANARY_BAND,
    REQUIRED_RESULTS,
    SCHEMA_VERSION,
    SIZE_BANDS,
    TARGETS,
    load_source_index,
    read_json,
    validate_cohort,
    write_json,
)
from prepare_experiment4_unseen_inventory import (
    SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION,
    structural_similarity,
    task_source_profile,
)
from experiment4_semantic_audit import digest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rank_key(seed: str, split: str, row: dict) -> str:
    material = f"{seed}|{split}|{row['size_band']}|{row['task_id']}"
    return hashlib.sha256(material.encode()).hexdigest()


def profile_summary(profile: dict) -> dict:
    return {key: profile[key] for key in (
        'normalized_source_sha256', 'structural_source_sha256', 'structural_token_count'
    )}


def select_rows(candidates: list[dict], profiles: dict[str, dict], *, seed: str,
                split: str, band: str, count: int, used_groups: set[str],
                selected: list[dict], threshold: float, minimum_tokens: int) -> list[dict]:
    ranked = sorted(
        (row for row in candidates if row['size_band'] == band),
        key=lambda row: (rank_key(seed, split, row), row['task_id']),
    )
    chosen = []
    for row in ranked:
        if row['source_group'] in used_groups:
            continue
        profile = profiles[row['task_id']]
        collisions = []
        for previous in selected + chosen:
            other = profiles[previous['task_id']]
            if (profile['structural_source_sha256'] == other['structural_source_sha256']):
                collisions.append((previous['task_id'], 1.0))
                continue
            if min(profile['structural_token_count'], other['structural_token_count']) < minimum_tokens:
                continue
            similarity = structural_similarity(profile, other)
            if similarity >= threshold:
                collisions.append((previous['task_id'], similarity))
        if collisions:
            continue
        frozen = dict(row)
        frozen.pop('exclusion_reasons', None)
        frozen.pop('near_duplicate_matches', None)
        frozen.update(profile_summary(profile))
        chosen.append(frozen)
        used_groups.add(row['source_group'])
        if len(chosen) == count:
            break
    if len(chosen) != count:
        raise ValueError(f'cannot select {count} independent {split}/{band} cases')
    return chosen


def build_pair_audit(rows: list[dict], profiles: dict[str, dict]) -> dict:
    pairs = []
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            similarity = structural_similarity(profiles[left['task_id']], profiles[right['task_id']])
            pairs.append({'left': left['task_id'], 'right': right['task_id'],
                          'similarity': round(similarity, 6)})
    pairs.sort(key=lambda row: (-row['similarity'], row['left'], row['right']))
    return {'pair_count': len(pairs), 'maximum_similarity': pairs[0]['similarity'] if pairs else 0.0,
            'highest_similarity_pairs': pairs[:20]}


def build(args: argparse.Namespace) -> dict:
    inventory = read_json(args.inventory)
    if inventory.get('schema_version') != INVENTORY_SCHEMA_VERSION:
        raise ValueError('inventory must be regenerated with the structural clone audit')
    policy = inventory.get('near_duplicate_policy') or {}
    threshold = float(policy.get('threshold'))
    minimum_tokens = int(policy.get('minimum_tokens'))
    candidates = inventory.get('candidates') or []
    if any(row.get('exclusion_reasons') for row in candidates):
        raise ValueError('inventory candidates contain excluded rows')

    source_index = load_source_index(args.plan)
    profiles = {}
    for row in candidates:
        task = source_index[row['task_id']]
        profile = task_source_profile(task)
        for key, expected in profile_summary(profile).items():
            if row.get(key) != expected:
                raise ValueError(f'inventory source profile changed for {row["task_id"]}: {key}')
        profiles[row['task_id']] = profile

    used_groups: set[str] = set()
    selected: list[dict] = []
    canary = select_rows(candidates, profiles, seed=args.seed, split='canary', band=CANARY_BAND,
                         count=1, used_groups=used_groups, selected=selected,
                         threshold=threshold, minimum_tokens=minimum_tokens)
    selected.extend(canary)
    splits = {'canary': canary}
    for split in ('development', 'hidden_test'):
        rows = []
        for band in SIZE_BANDS:
            rows.extend(select_rows(
                candidates, profiles, seed=args.seed, split=split, band=band,
                count=TARGETS[split][band], used_groups=used_groups,
                selected=selected + rows, threshold=threshold, minimum_tokens=minimum_tokens,
            ))
        rows.sort(key=lambda row: row['task_id'])
        splits[split] = rows
        selected.extend(rows)

    payload = {
        'schema_version': SCHEMA_VERSION,
        'created_at': utc_now(),
        'status': 'frozen_confirmatory',
        'selection_seed': args.seed,
        'selection_policy': {
            'uses_graph_outputs': False,
            'eligibility': 'strict_clean_complete_four_stage_inputs_and_unexposed_source_audit',
            'cell_range': [100, 10000],
            'source_group_disjoint_across_all_splits': True,
            'structural_near_duplicate_disjoint_across_all_splits': True,
            'near_duplicate_threshold': threshold,
            'minimum_near_duplicate_tokens': minimum_tokens,
            'size_bands': SIZE_BANDS,
            'targets': {'canary': {CANARY_BAND: 1}, **TARGETS},
        },
        'source': {
            'inventory': str(args.inventory.resolve()),
            'inventory_sha256': digest(args.inventory),
            'baseline_plan': str(args.plan.resolve()),
            'baseline_plan_sha256': digest(args.plan),
            'candidate_total': len(candidates),
            'candidate_by_size_band': dict(Counter(row['size_band'] for row in candidates)),
        },
        'independence_audit': build_pair_audit(selected, profiles),
        'splits': splits,
    }
    validate_cohort(payload, check_files=True)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--seed', default='experiment4-confirmatory-20260909')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    payload = build(args)
    write_json(args.output, payload)
    print({'status': payload['status'], 'counts': {key: len(value) for key, value in payload['splits'].items()},
           'maximum_structural_similarity': payload['independence_audit']['maximum_similarity']})


if __name__ == '__main__':
    main()
