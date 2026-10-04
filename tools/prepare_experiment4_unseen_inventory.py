#!/usr/bin/env python3
"""Inventory potentially unexposed designs without executing any converter."""
import argparse
from collections import Counter
import hashlib
from pathlib import Path
import re

from experiment4_protocol import discover_eligible, load_source_index, read_json
from experiment4_semantic_audit import digest, save


SCHEMA_VERSION = 'experiment4-unseen-inventory-2.0'
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.50
SHINGLE_WIDTH = 7
VERILOG_KEYWORDS = {
    'always', 'always_comb', 'always_ff', 'always_latch', 'and', 'assign', 'automatic',
    'begin', 'buf', 'case', 'casex', 'casez', 'default', 'else', 'end', 'endcase',
    'endfunction', 'endgenerate', 'endmodule', 'endtask', 'for', 'force', 'forever',
    'function', 'generate', 'genvar', 'if', 'initial', 'inout', 'input', 'integer',
    'localparam', 'logic', 'module', 'nand', 'negedge', 'nor', 'not', 'or', 'output',
    'parameter', 'posedge', 'reg', 'release', 'repeat', 'signed', 'supply0', 'supply1',
    'task', 'time', 'tri', 'unsigned', 'wait', 'wand', 'while', 'wire', 'wor', 'xnor',
    'xor',
}
TOKEN_RE = re.compile(
    r"\\\\\S+|[A-Za-z_$][A-Za-z0-9_$]*|(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|"
    r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|===|!==|<<<|>>>|<<|>>|<=|>=|==|!=|&&|\|\||"
    r"\+\+|--|\*\*|->|=>|\+:|-:|::|[{}()\[\];,.?:~!%^&*+\-/|<>=@#]"
)


def strip_verilog_comments(text: str) -> str:
    """Remove comments while preserving quoted strings and token boundaries."""
    output, index, state = [], 0, 'code'
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ''
        if state == 'code':
            if char == '/' and next_char == '/':
                output.append(' ')
                index += 2
                state = 'line_comment'
                continue
            if char == '/' and next_char == '*':
                output.append(' ')
                index += 2
                state = 'block_comment'
                continue
            output.append(char)
            if char == '"':
                state = 'string'
        elif state == 'string':
            output.append(char)
            if char == '\\' and index + 1 < len(text):
                index += 1
                output.append(text[index])
            elif char == '"':
                state = 'code'
        elif state == 'line_comment':
            if char in '\r\n':
                output.append(char)
                state = 'code'
        elif char == '*' and next_char == '/':
            output.append(' ')
            index += 1
            state = 'code'
        index += 1
    return ''.join(output)


def normalized_tokens(text: str, *, anonymize_identifiers: bool) -> list[str]:
    tokens = TOKEN_RE.findall(strip_verilog_comments(text))
    if not anonymize_identifiers:
        return tokens
    return [
        token if token.lower() in VERILOG_KEYWORDS or not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*', token)
        else '<id>'
        for token in tokens
    ]


def task_source_profile(task: dict) -> dict:
    root = Path(task['source_root']).resolve()
    exact_files, structural_files, shingles = [], [], set()
    token_count = 0
    for filename in task['rtl_files']:
        path = (root / filename).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError('missing or out-of-root RTL source: ' + str(path))
        text = path.read_text(encoding='utf-8', errors='replace')
        exact = normalized_tokens(text, anonymize_identifiers=False)
        structural = normalized_tokens(text, anonymize_identifiers=True)
        exact_files.append(hashlib.sha256('\n'.join(exact).encode()).hexdigest())
        structural_files.append(hashlib.sha256('\n'.join(structural).encode()).hexdigest())
        token_count += len(structural)
        for index in range(max(0, len(structural) - SHINGLE_WIDTH + 1)):
            material = '\x1f'.join(structural[index:index + SHINGLE_WIDTH]).encode()
            shingles.add(int.from_bytes(hashlib.blake2b(material, digest_size=8).digest(), 'big'))
    if not exact_files:
        raise ValueError('empty RTL file list')
    return {
        'normalized_source_sha256': hashlib.sha256('\n'.join(sorted(exact_files)).encode()).hexdigest(),
        'structural_source_sha256': hashlib.sha256('\n'.join(sorted(structural_files)).encode()).hexdigest(),
        'structural_token_count': token_count,
        'structural_shingles': shingles,
    }


def structural_similarity(left: dict, right: dict) -> float:
    left_set, right_set = left['structural_shingles'], right['structural_shingles']
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def source_fingerprints(task):
    root = Path(task['source_root']).resolve()
    hashes = set()
    for filename in task['rtl_files']:
        path = (root / filename).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError('missing or out-of-root RTL source: ' + str(path))
        hashes.add(digest(path))
    if not hashes:
        raise ValueError('empty RTL file list')
    return hashes


def exclusion_reasons(row, task, fingerprints, exposed, profile=None, near_duplicate_matches=None):
    reasons = []
    if row['task_id'] in exposed['tasks']:
        reasons.append('previous_task')
    if row['source_group'] in exposed['groups']:
        reasons.append('previous_repository')
    if task.get('source_closure_sha256') in exposed['closures']:
        reasons.append('previous_source_closure')
    if fingerprints & exposed['rtl_hashes']:
        reasons.append('exact_rtl_file_overlap')
    if profile and profile['normalized_source_sha256'] in exposed.get('normalized_sources', set()):
        reasons.append('normalized_source_overlap')
    if profile and profile['structural_source_sha256'] in exposed.get('structural_sources', set()):
        reasons.append('structural_source_overlap')
    if near_duplicate_matches:
        reasons.append('near_duplicate_rtl')
    return reasons


def main(args):
    index = load_source_index(args.plan)
    exposed = {k: set() for k in ('tasks', 'groups', 'closures', 'rtl_hashes',
                                   'normalized_sources', 'structural_sources')}
    exposed_profiles = []
    cohort_hashes = {}
    for path in args.previous_cohort:
        data = read_json(path)
        cohort_hashes[str(path.resolve())] = digest(path)
        for rows in data['splits'].values():
            for row in rows:
                task = index[row['task_id']]
                exposed['tasks'].add(row['task_id'])
                exposed['groups'].add(row['source_group'])
                if task.get('source_closure_sha256'):
                    exposed['closures'].add(task['source_closure_sha256'])
                exposed['rtl_hashes'].update(source_fingerprints(task))
                profile = task_source_profile(task)
                exposed['normalized_sources'].add(profile['normalized_source_sha256'])
                exposed['structural_sources'].add(profile['structural_source_sha256'])
                exposed_profiles.append((row['task_id'], row['source_group'], profile))
    eligible = discover_eligible(args.projects, args.plan)
    accepted, excluded = [], []
    for row in eligible:
        task = index[row['task_id']]
        try:
            hashes = source_fingerprints(task)
            profile = task_source_profile(task)
            matches = []
            if (profile['structural_token_count'] >= args.minimum_near_duplicate_tokens
                    and profile['structural_source_sha256'] not in exposed['structural_sources']):
                for old_task, old_group, old_profile in exposed_profiles:
                    if old_profile['structural_token_count'] < args.minimum_near_duplicate_tokens:
                        continue
                    similarity = structural_similarity(profile, old_profile)
                    if similarity >= args.near_duplicate_threshold:
                        matches.append({'task_id': old_task, 'source_group': old_group,
                                        'similarity': round(similarity, 6)})
            matches.sort(key=lambda match: (-match['similarity'], match['task_id']))
            reasons = exclusion_reasons(row, task, hashes, exposed, profile, matches)
        except (KeyError, ValueError, OSError) as exc:
            hashes, profile, matches, reasons = set(), {}, [], ['source_not_verifiable: ' + str(exc)]
        record = dict(row, rtl_file_sha256=sorted(hashes),
                      source_closure_sha256=task.get('source_closure_sha256'),
                      normalized_source_sha256=profile.get('normalized_source_sha256'),
                      structural_source_sha256=profile.get('structural_source_sha256'),
                      structural_token_count=profile.get('structural_token_count'),
                      near_duplicate_matches=matches[:10], exclusion_reasons=reasons)
        (excluded if reasons else accepted).append(record)
    summary = {'eligible': len(eligible), 'potentially_unexposed': len(accepted),
               'distinct_repositories': len({r['source_group'] for r in accepted}),
               'by_size': dict(Counter(r['size_band'] for r in accepted)),
               'repository_counts_by_size': {band: len({r['source_group'] for r in accepted if r['size_band'] == band})
                    for band in sorted({r['size_band'] for r in eligible})},
               'excluded': len(excluded), 'exclusion_reasons': dict(Counter(reason for r in excluded for reason in r['exclusion_reasons']))}
    save(args.output, {'schema_version': SCHEMA_VERSION,
         'role': 'inventory only; not a frozen held-out split', 'summary': summary,
         'previous_cohort_sha256': cohort_hashes, 'baseline_plan_sha256': digest(args.plan),
         'script_sha256': digest(__file__), 'uses_converter_outputs': False,
         'near_duplicate_policy': {'method': 'Jaccard over 7-token structural shingles',
                                   'identifier_normalization': True,
                                   'threshold': args.near_duplicate_threshold,
                                   'minimum_tokens': args.minimum_near_duplicate_tokens},
         'limitations': ['structural similarity is a conservative heuristic, not formal equivalence',
                        'prior exposure outside declared cohorts is not automatically known',
                        'repository grouping is conservative, not proof of functional family independence',
                        'cross-split and within-test source independence must be checked when selecting the final cohort'],
         'candidates': accepted, 'excluded': excluded})
    print(summary)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--projects', type=Path, required=True)
    p.add_argument('--previous-cohort', type=Path, action='append', required=True)
    p.add_argument('--near-duplicate-threshold', type=float, default=DEFAULT_NEAR_DUPLICATE_THRESHOLD)
    p.add_argument('--minimum-near-duplicate-tokens', type=int, default=40)
    p.add_argument('--output', type=Path, required=True)
    main(p.parse_args())
