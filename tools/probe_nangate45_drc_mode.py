#!/usr/bin/env python3
"""Isolated full-rule flat DRC comparison against completed deep references.

Never edits baseline inputs, decks, checkpoints or scores. Exact top-cell RDB
marker equality is deliberately conservative; hierarchy/representation changes
require review rather than being silently equated by count or bounding box.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET


CASES = ['exp1_30b6eb260039_impl_top', 'exp1_58cd580c3929_trivium',
         'exp1_0c71059b0008_picorv32_pcpi_fast_mul']


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1048576), b''):
            h.update(data)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def flat_deck(data):
    root = ET.fromstring(data)
    node = root.find('text')
    if node is None:
        raise ValueError('No Ruby text in deck')
    lines = node.text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if line.strip() == 'deep']
    if len(indices) != 1:
        raise ValueError('Expected exactly one deep statement')
    i = indices[0]
    lines[i] = lines[i].replace('deep', 'flat', 1)
    node.text = ''.join(lines)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def contact_local_deck(data, scoped_flat=False):
    root = ET.fromstring(data)
    node = root.find('text')
    old = 'cont.not(active.or(poly.or(metal1)))'
    new = 'cont.dup.flatten.not(active.dup.flatten.or(poly.dup.flatten.or(metal1.dup.flatten)))'
    if node is None or node.text.count(old) != 1:
        raise ValueError('Expected exactly one CONTACT.3 geometry expression')
    node.text = node.text.replace(old, new)
    if scoped_flat:
        lines = node.text.splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if new in line)
        lines[index] = 'flat\n' + lines[index] + 'deep\n'
        node.text = ''.join(lines)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def contact_difference_deck(data):
    root = ET.fromstring(data)
    node = root.find('text')
    old = 'cont.not(active.or(poly.or(metal1)))'
    if node is None or node.text.count(old) != 1:
        raise ValueError('Expected exactly one CONTACT.3 geometry expression')
    node.text = node.text.replace(old, 'cont.not(active).not(poly).not(metal1)')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def report(path):
    root = ET.parse(path).getroot()
    if root.tag != 'report-database':
        raise ValueError('Not a report database')
    top = root.findtext('top-cell')
    cats = root.findall('./categories/category')
    if not top or not cats or root.find('items') is None:
        raise ValueError('Incomplete report structure')
    if any(c.findall('./categories/category') for c in cats):
        raise ValueError('Nested categories require separate comparison')
    categories = sorted((c.findtext('name'), c.findtext('description', '')) for c in cats)
    category_names = {c[0] for c in categories}
    if len(category_names) != len(categories):
        raise ValueError('Duplicate category names')
    for cell in root.findall('./cells/cell'):
        if cell.findtext('name') != top or cell.findtext('variant') or list(cell.find('references') or []):
            raise ValueError('Hierarchical report needs coordinate transformation')
    markers = Counter()
    counts = Counter()
    for item in root.findall('./items/item'):
        category, cell = item.findtext('category'), item.findtext('cell')
        if category not in category_names or cell != top:
            raise ValueError('Unknown category or non-top-cell marker')
        values = tuple(sorted(v.text or '' for v in item.findall('./values/value')))
        if not values:
            raise ValueError('Marker has no evidence values')
        multiplicity = int(item.findtext('multiplicity', '1'))
        if multiplicity < 1:
            raise ValueError('Invalid marker multiplicity')
        tags = ET.tostring(item.find('tags'), encoding='unicode').strip() if item.find('tags') is not None else ''
        markers[(category, cell, values, tags)] += multiplicity
        counts[category] += multiplicity
    return {'top': top, 'categories': categories, 'markers': markers,
            'counts': dict(counts), 'items': sum(markers.values())}


def compare(a, b):
    left, right = report(a), report(b)
    missing, extra = left['markers'] - right['markers'], right['markers'] - left['markers']
    same_schema = left['top'] == right['top'] and left['categories'] == right['categories']
    return {'equivalent_on_this_case': same_schema and not missing and not extra,
            'same_category_definitions_and_top': same_schema,
            'reference_markers': left['items'], 'trial_markers': right['items'],
            'reference_categories': left['counts'], 'trial_categories': right['counts'],
            'missing_markers': sum(missing.values()), 'extra_markers': sum(extra.values()),
            'missing_examples': [repr(k) for k in list(missing)[:3]],
            'extra_examples': [repr(k) for k in list(extra)[:3]],
            'comparison': 'Exact top-cell marker values, coordinates, tags and multiplicity; not count-only.'}


def prepare(args):
    root = args.root.resolve()
    if (root / 'plan.json').exists():
        raise FileExistsError('Existing plan must not be overwritten')
    records = []
    deck = None
    version = subprocess.check_output([args.klayout, '-v'], text=True).strip()
    tasks = args.case or CASES
    if len(set(tasks)) != len(tasks):
        raise ValueError('Duplicate case IDs')
    for task in tasks:
        job = args.baseline / 'jobs' / task
        done = job / 'complete.json'
        outcome = json.loads(done.read_text())
        if outcome.get('trial_timeout') or outcome.get('returncode') != 0:
            raise ValueError('Incomplete baseline: ' + task)
        candidates = list(job.glob('project/backend/RUN_*/drc/drc_result.json'))
        if len(candidates) != 1:
            raise ValueError('Ambiguous baseline run: ' + task)
        meta_path = candidates[0]
        meta = json.loads(meta_path.read_text())
        source_deck, gds = Path(meta['deck_path']), Path(meta['gds_path'])
        rdb = meta_path.parent / '6_drc.lyrdb'
        if meta['exit_code'] != 0 or meta['drc_mode'] != 'full' or meta['status'] not in ('clean', 'violations'):
            raise ValueError('Reference is not a completed full-rule check')
        if version != meta['klayout_version'] or sha(source_deck) != meta['deck_sha256'] or sha(gds) != meta['gds_sha256']:
            raise ValueError('Tool version/deck/GDS differs from reference')
        if deck is not None and source_deck != deck:
            raise ValueError('Mixed reference decks')
        deck = source_deck
        parsed = report(rdb)
        records.append({'task_id': task, 'gds': str(gds), 'reference_report': str(rdb),
            'reference_wall_seconds': meta['wall_s'], 'reference_marker_count': parsed['items'],
            'reference_wrapper_count': meta.get('violations'),
            'input_hashes': {str(p): sha(p) for p in (gds, source_deck, rdb, meta_path, done)}})
    root.mkdir(parents=True, exist_ok=True)
    variant = args.mode
    flat = root / ('FreePDK45.' + variant + '.lydrc')
    data = deck.read_bytes()
    trial = flat_deck(data) if variant == 'flat' else contact_local_deck(data, variant == 'contact_scoped')
    if variant == 'contact_difference':
        trial = contact_difference_deck(data)
    flat.write_bytes(trial)
    save(root / 'plan.json', {'created_at': now(), 'cases': records,
        'klayout': args.klayout, 'klayout_version': version, 'tool_sha256': sha(args.klayout),
        'source_deck': str(deck), 'trial_deck': str(flat), 'trial_deck_sha256': sha(flat),
        'script_sha256': sha(__file__), 'execution_variant': variant,
        'cpu_set': args.cpu_set, 'timeout_seconds': 900,
        'minimum_free_gib': 20, 'baseline_modified': False, 'auto_rollout': False,
        'timing_note': 'Reference times are historical, not a controlled simultaneous hardware benchmark.',
        'limits': 'Selected-case comparison only. Matching clean reports alone does not prove detection sensitivity.'})
    print('Prepared %d full-rule canaries; execution variant: %s.' % (len(tasks), variant), flush=True)


def invoke(command, log, timeout):
    started = time.monotonic()
    with log.open('w') as stream:
        proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            return {'returncode': 124, 'timed_out': True, 'wall_seconds': time.monotonic() - started}
        return {'returncode': code, 'timed_out': False, 'wall_seconds': time.monotonic() - started}


def run(args):
    root = args.root.resolve()
    plan = json.loads((root / 'plan.json').read_text())
    cpus = {int(x) for x in plan['cpu_set'].split(',')}
    if not cpus <= os.sched_getaffinity(0):
        raise ValueError('CPU affinity unavailable')
    os.sched_setaffinity(0, cpus)
    for name, checksum in ((plan['klayout'], plan['tool_sha256']),
                          (plan['trial_deck'], plan['trial_deck_sha256']), (__file__, plan['script_sha256'])):
        if sha(name) != checksum:
            raise ValueError('Probe implementation changed: ' + name)
    with (root / 'probe.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        results = []
        for case in plan['cases']:
            work = root / case['task_id']
            result_path = work / 'result.json'
            if result_path.exists():
                results.append(json.loads(result_path.read_text()))
                continue
            if work.exists():
                raise RuntimeError('Partial canary exists; inspect before rerun')
            if shutil.disk_usage(root).free < plan['minimum_free_gib'] * 1024**3:
                save(root / 'status.json', {'status': 'paused_low_disk', 'results': results})
                return
            for path, checksum in case['input_hashes'].items():
                if sha(path) != checksum:
                    raise ValueError('Reference changed: ' + path)
            work.mkdir()
            save(root / 'status.json', {'status': 'running', 'active': case['task_id'],
                                       'started_at': now(), 'results': results})
            variant = plan.get('execution_variant', 'flat')
            output = work / (variant + '.lyrdb')
            cmd = [plan['klayout'], '-zz', '-rd', 'in_gds=' + case['gds'],
                   '-rd', 'report_file=' + str(output), '-r', plan['trial_deck']]
            print(case['task_id'] + ': started', flush=True)
            result = invoke(cmd, work / (variant + '.log'), plan['timeout_seconds'])
            result.update(task_id=case['task_id'], command=cmd, completed_at=now(),
                          reference_wall_seconds=case['reference_wall_seconds'])
            if result['returncode'] == 0 and output.is_file():
                try:
                    result['comparison'] = compare(case['reference_report'], output)
                    result['status'] = 'MATCH' if result['comparison']['equivalent_on_this_case'] else 'MISMATCH_REVIEW'
                except Exception as exc:
                    result.update(status='COMPARISON_REVIEW', error=str(exc))
                result['trial_report_sha256'] = sha(output)
            else:
                result['status'] = 'TIMEOUT' if result['timed_out'] else 'EXECUTION_FAILURE'
            result['reference_inputs_unchanged'] = all(sha(p) == h for p, h in case['input_hashes'].items())
            if not result['reference_inputs_unchanged']:
                result['status'] = 'REFERENCE_CHANGED'
            save(result_path, result)
            results.append(result)
            print(case['task_id'] + ': ' + result['status'] + ' ' + str(round(result['wall_seconds'], 2)) + 's', flush=True)
        save(root / 'status.json', {'status': 'complete', 'completed_at': now(), 'results': results,
            'all_canaries_match': all(r['status'] == 'MATCH' for r in results),
            'auto_rollout': False, 'baseline_modified': False})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['prepare', 'run'])
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--baseline', type=Path)
    p.add_argument('--klayout', default='/usr/bin/klayout')
    p.add_argument('--cpu-set', default='180,183,186,187')
    p.add_argument('--case', action='append', help='Completed reference task; repeat for multiple cases')
    p.add_argument('--mode', choices=['flat', 'contact_local', 'contact_scoped', 'contact_difference'], default='flat')
    args = p.parse_args()
    (prepare if args.command == 'prepare' else run)(args)
