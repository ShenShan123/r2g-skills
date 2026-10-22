#!/usr/bin/env python3
"""Read-only certified-corpus audit; proposes lineage reservations, never repairs."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import time

SEED = 'tehm-learning-sl1-certified-0002-v1'
KNOWN_OWNERS = {'secworks', 'ultraembedded', 'alexforencich', 'yosyshq', 'freecores', 'opencores', 'lowrisc'}
FRONTENDS = {'verilog-native', 'verilog-slang'}
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def inside(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('Input path escapes the handoff: ' + relative)
    return p


def rank(value):
    return hashlib.sha256((SEED + '|' + value).encode()).hexdigest()


def grouped(items, known_hashes):
    """Conservative connected components, not proof of semantic independence."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen = {}
    for i, item in enumerate(items):
        keys = [('repository', item['repository'].lower()), ('owner', item['owner'].lower())]
        if item.get('fingerprint'):
            keys.append(('fingerprint', item['fingerprint']))
        keys += [('rtl_sha256', h) for h in item['source_hashes']]
        for key in keys:
            if key[1]:
                if key in seen:
                    union(i, seen[key])
                else:
                    seen[key] = i
    buckets = collections.defaultdict(list)
    for i, item in enumerate(items):
        buckets[find(i)].append(item)
    result = []
    for members in buckets.values():
        members = sorted(members, key=lambda x: x['design_id'])
        gid = 'lineage_' + hashlib.sha256('|'.join(x['design_id'] for x in members).encode()).hexdigest()[:20]
        reasons = set()
        if any(x['owner'].lower() in KNOWN_OWNERS for x in members):
            reasons.add('known_SOURCE_DEV_or_exposed_TEST_owner')
        if any(set(x['source_hashes']) & known_hashes for x in members):
            reasons.add('exact_RTL_overlap_with_SOURCE_DEV_or_exposed_TEST')
        supported = [x for x in members if x['frontend'] in FRONTENDS]
        if not supported:
            reasons.add('frontend_adapter_not_yet_qualified')
        split = 'HELD_FOR_REVIEW' if reasons else ('PROBE_CANDIDATE' if int(rank(gid), 16) % 5 == 0 else 'LEARN_CANDIDATE')
        rep = min(supported, key=lambda x: rank(x['design_id']))['design_id'] if supported else None
        result.append({'group_id': gid, 'split_reservation': split,
                       'member_ids': [x['design_id'] for x in members], 'representative_id': rep,
                       'reasons': sorted(reasons), 'repair_qualified': False})
    return sorted(result, key=lambda x: rank(x['group_id']))


def audit(source, output):
    source = source.resolve()
    if output.exists():
        raise RuntimeError('Output exists; never overwrite an earlier audit')
    start, cpu = time.time(), time.process_time()
    output.mkdir(parents=True)
    paths = [source / 'README.md', *sorted((source / 'metadata').glob('*'))]
    paths = [p for p in paths if p.is_file()]
    bindings = {str(p): digest(p) for p in paths}
    past = EVAL / 't2a-formal-f2-20261013/inventory.json'
    old = read(past)
    bindings[str(past)] = digest(past)
    known_hashes = {sha.removeprefix('sha256:') for p, sha in old.get('input_hashes', {}).items()
                    if Path(p).suffix.lower() in {'.v', '.sv', '.vhd', '.vhdl'} and '/rtl/' in p}
    for manifest in (EVAL / 't2a-formal-f2-20261013/tasks').glob('*/manifest.json'):
        d = read(manifest)
        bindings[str(manifest)] = digest(manifest)
        known_hashes.update(sha.removeprefix('sha256:') for p, sha in d['immutable']['files'].items()
                            if Path(p).suffix.lower() in {'.v', '.sv', '.vhd', '.vhdl'})
    bindings[str(Path(__file__).resolve())] = digest(Path(__file__).resolve())
    inventory = rows(source / 'metadata/INVENTORY.jsonl')
    if len(inventory) != len({x['design_id'] for x in inventory}):
        raise ValueError('Duplicate design IDs')
    save(output / 'registration.json', {
        'schema': 'tehm-learning-certified-corpus-preflight-v1', 'started': start,
        'source': str(source), 'input_hashes': bindings, 'platform': 'sky130hd',
        'split_seed': SEED, 'grouping': ['repository', 'owner', 'shared_RTL_sha256', 'structural_fingerprint'],
        'no_eda': True, 'no_llm': True, 'repair_launch_allowed': False,
        'paper_data': False, 'no_retry': True,
    })
    save(output / 'progress.json', {'stage': 'PAYLOAD_INTEGRITY', 'time': time.time()})
    bad, count, byte_count = [], 0, 0
    for item in rows(source / 'metadata/FILES.jsonl'):
        p = inside(source, item['path'])
        if not p.is_file() or digest(p) != item['sha256']:
            bad.append({'path': item['path'], 'reason': 'missing_or_sha256_mismatch'})
        else:
            count += 1
            byte_count += p.stat().st_size
    save(output / 'payload-integrity.json', {'verified': count, 'bytes_hashed': byte_count, 'failures': bad})
    if bad:
        save(output / 'progress.json', {'stage': 'STOPPED_NO_RETRY', 'reason': 'payload integrity failure'})
        raise RuntimeError('Payload integrity failure; no task selection')
    save(output / 'progress.json', {'stage': 'LINEAGE_AND_FRONTEND_AUDIT', 'time': time.time()})
    designs = []
    for item in inventory:
        folder = inside(source, item['directory'])
        path = folder / 'DESIGN.json'
        data = read(path)
        if (data['design_id'] != item['design_id'] or data.get('engineering', {}).get('status') != 'CERTIFIED_GENERIC_SYNTHESIS'
                or data.get('repository', {}).get('url') != item['repository_url']):
            raise RuntimeError('Inventory/design identity disagreement: ' + item['design_id'])
        hashes = []
        for entry in data['source_files']:
            p = inside(source, str((folder / entry['file']).relative_to(source)))
            if digest(p) != entry['sha256']:
                raise RuntimeError('Design-specific RTL digest mismatch: ' + str(p))
            hashes.append(entry['sha256'])
        designs.append({'design_id': data['design_id'], 'handoff_id': data['handoff_id'],
                        'directory': str(folder), 'top': data['top_module'],
                        'owner': data['repository']['owner'], 'repository': data['repository']['url'],
                        'commit': data['repository']['commit'], 'source_hashes': hashes,
                        'fingerprint': item.get('structural_fingerprint'), 'frontend': item['frontend_route'],
                        'design_manifest_sha256': digest(path), 'repair_qualified': False})
    groups = grouped(designs, known_hashes)
    group_of = {ident: g for g in groups for ident in g['member_ids']}
    with (output / 'candidate_inventory.jsonl').open('x') as out:
        for item in designs:
            g = group_of[item['design_id']]
            out.write(json.dumps(dict(item, group_id=g['group_id'], split_reservation=g['split_reservation']), sort_keys=True) + '\n')
    save(output / 'lineage-reservations.json', {'groups': groups, 'not_a_qualified_task_split': True})
    by_id = {x['design_id']: x for x in designs}
    order = {split: [by_id[g['representative_id']] for g in groups
                     if g['split_reservation'] == split and g['representative_id']]
             for split in ('LEARN_CANDIDATE', 'PROBE_CANDIDATE')}
    save(output / 'qualification-candidate-order.json', {'order': order, 'no_fill_based_on_repair_results': True,
                                                       'repair_launch_allowed': False})
    if any(digest(Path(p)) != h for p, h in bindings.items()):
        raise RuntimeError('Source/control inputs changed during audit')
    result = {'status': 'AUDITED_CANDIDATES_NOT_REPAIR_QUALIFIED', 'designs': len(designs),
              'repositories': len({x['repository'].lower() for x in designs}),
              'conservative_groups': len(groups),
              'groups_by_reservation': dict(collections.Counter(x['split_reservation'] for x in groups)),
              'frontends': dict(collections.Counter(x['frontend'] for x in designs)),
              'payload_files_verified': count, 'bytes_hashed': byte_count,
              'actual_llm_requests': 0, 'actual_eda_flows': 0,
              'wall_s': time.time() - start, 'cpu_s': time.process_time() - cpu,
              'source_bound_metadata_unchanged': True, 'repair_launch_allowed': False,
              'limitations': ['Generic synthesis certification is not sky130hd physical/signoff qualification.',
                             'Connected components are conservative, not proof of semantic uniqueness.',
                             'All candidates need clock/IO/constraint/macro and failure qualification.',
                             'Fixed LEARN/PROBE repair manifests and model authorization remain pending.']}
    save(output / 'result.json', result)
    save(output / 'progress.json', {'stage': 'COMPLETE_PREPARATION_ONLY', 'time': time.time()})
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit(args.source, args.output.resolve())
