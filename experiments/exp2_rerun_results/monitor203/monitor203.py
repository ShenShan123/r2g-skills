#!/usr/bin/env python3
"""Hourly host metrics and allowlisted completed-evaluation cleanup; no process control."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

BASE = Path('/data/yangao/exp2_rerun')
STATE = BASE / 'monitoring'
FLOW = Path('/data/yangao/nvme_home/r2g_toolchain/OpenROAD-flow-scripts/flow')
CAMPAIGNS = {
    'exp2-v5b-20261114': ('native-dsh', 'native-codex'),
    'exp2-v5fix2-20261115': ('r2g-expander-deepseek', 'r2g-expander-gpt'),
}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def journal(event):
    with (STATE / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'at': now(), **event}) + '\n')
        stream.flush()


def original_sources(campaign, method):
    root = campaign / 'method_runs' / (method + '.batch1.formal/repositories')
    sources = {}
    if root.exists():
        for source in root.iterdir():
            if (source / '.git').exists():
                def git(*args):
                    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
                sources[(normalize(git('config', '--get', 'remote.origin.url')), git('rev-parse', 'HEAD').lower())] = source
    return sources


def normalize(url):
    return url.lower().rstrip('/').removesuffix('.git')


def cleanup_batch(campaign, method, batch):
    if batch['status'] != 'complete':
        return {'status': batch['status'], 'deleted_directories': 0}
    evaluation = campaign / 'evaluation' / (method + '.batch1')
    marker = STATE / ('cleaned.' + campaign.parent.name + '.' + method + '.json')
    if marker.exists() and read(marker)['evaluation_sha256'] == batch['evaluation_sha256']:
        return {'status': 'already_cleaned', 'deleted_directories': 0}
    report_path = Path(batch['evaluation_path'])
    submission_path = Path(batch['submission_path'])
    assert sha(report_path) == batch['evaluation_sha256'], 'evaluation hash changed'
    assert sha(submission_path) == batch['submission_sha256'], 'submission hash changed'
    results = {row['candidate_id']: row for row in read(report_path)['results']}
    candidates = read(submission_path)['candidates']
    targets = set()
    retained = []
    caches = {}
    sources = None
    for candidate in candidates:
        result = results[candidate['candidate_id']]
        output = evaluation / candidate['candidate_id']
        assert output.parent == evaluation and not output.is_symlink()
        assert read(output / 'result.json') == result, 'per-design result differs'
        synthesis = result.get('synthesis') or {}
        netlist_name = synthesis.get('mapped_netlist')
        if netlist_name:
            netlist = Path(netlist_name)
            assert netlist.parent == output
            assert sha(netlist) == synthesis['mapped_netlist_sha256'], 'retained netlist hash mismatch'
            retained.append((netlist, synthesis['mapped_netlist_sha256']))
            source_netlist = Path(synthesis['source_mapped_netlist'])
            relative = source_netlist.parent.relative_to(FLOW / 'results')
            assert len(relative.parts) == 3 and relative.parts[-1] == synthesis['flow_variant']
            assert relative.parts[-1].startswith('exp1_')
            if source_netlist.exists():
                assert sha(source_netlist) == synthesis['mapped_netlist_sha256']
            for section in ('results', 'objects'):
                path = FLOW / section / relative
                if path.is_dir():
                    targets.add(path)
        if method.startswith('native-'):
            key = hashlib.sha256((normalize(candidate['repo_url']) + '|' + candidate['commit'].lower()).encode()).hexdigest()[:20]
            cache = evaluation / '_source_cache' / key
            if cache.is_dir():
                if sources is None:
                    sources = original_sources(campaign, method)
                source = sources.get((normalize(candidate['repo_url']), candidate['commit'].lower()))
                if source is None:
                    continue  # Retain the cache if it is the only known pinned source copy.
                for entry in result['source_evidence']['files']:
                    assert sha(source / entry['path']) == entry['sha256'], 'retained source hash mismatch'
                license_path = candidate['license_evidence']['repository_path']
                assert sha(source / license_path) == sha(cache / license_path)
                caches[cache] = source
                targets.add(cache)
    # A complete batch must have no evaluator still using any planned target.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        assert not any(str(path) in command for path in targets), 'cleanup target is in use'
    before = os.statvfs(BASE)
    for path in sorted(targets):
        assert not path.is_symlink()
        journal({'action': 'delete_duplicate', 'path': str(path),
                 'retained_source': str(caches[path]) if path in caches else None,
                 'method': method, 'campaign': campaign.parent.name})
        shutil.rmtree(path)
    assert sha(report_path) == batch['evaluation_sha256']
    assert sha(submission_path) == batch['submission_sha256']
    for path, digest in retained:
        assert sha(path) == digest
    after = os.statvfs(BASE)
    record = {'at': now(), 'evaluation_sha256': batch['evaluation_sha256'],
              'deleted_directories': len(targets),
              'freed_gib': (after.f_bavail - before.f_bavail) * after.f_frsize / 2**30,
              'retained_source_locations': {str(k): str(v) for k, v in caches.items()}}
    marker.write_text(json.dumps(record, indent=2) + '\n')
    return dict(status='cleaned', **record)


def metrics():
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    pressure = {name: Path('/proc/pressure', name).read_text().strip() for name in ('cpu', 'memory', 'io')}
    def ticks():
        return list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    first = ticks()
    time.sleep(1)
    last = ticks()
    delta = [b-a for a, b in zip(first, last)]
    disks = {}
    for mount in ('/data', '/', '/home/yangao'):
        stat = os.statvfs(mount)
        disks[mount] = {'available_gib': round(stat.f_bavail * stat.f_frsize / 2**30, 2),
                        'used_percent': round(100 * (1 - stat.f_bfree / stat.f_blocks), 1)}
    return {'at': now(), 'cpu_busy_percent': round(100 * (1 - (delta[3]+delta[4])/sum(delta)), 1),
            'load_1_5_15': os.getloadavg(), 'cpu_count': os.cpu_count(),
            'memory_available_gib': round(memory['MemAvailable']/2**30, 2),
            'swap_used_gib': round((memory['SwapTotal']-memory['SwapFree'])/2**30, 2),
            'pressure': pressure, 'disks': disks}


def alerts(snapshot):
    messages = []
    free = snapshot['disks']['/data']['available_gib']
    if free < 100:
        messages.append(('CRITICAL' if free < 50 else 'WARNING') + ': /data available below ' + ('50' if free < 50 else '100') + ' GiB')
    mem = snapshot['memory_available_gib']
    if mem < 32:
        messages.append(('CRITICAL' if mem < 16 else 'WARNING') + ': available memory below ' + ('16' if mem < 16 else '32') + ' GiB')
    for resource, limit in [('cpu', 20), ('memory', 5), ('io', 10)]:
        line = snapshot['pressure'][resource].splitlines()[0]
        avg = float(dict(x.split('=') for x in line.split()[1:])['avg60'])
        if avg > limit:
            messages.append('WARNING: elevated ' + resource + ' pressure avg60=' + str(avg))
    return messages


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    before = metrics()
    outcomes = {}
    for name, methods in CAMPAIGNS.items():
        campaign = BASE / 'runs' / name / 'campaign'
        manifest = read(campaign / 'execution_manifest.json')
        for method in methods:
            batch = next(b for b in manifest['batches'] if b['method_id'] == method and b['batch_id'] == 1)
            try:
                outcomes[name + '/' + method] = cleanup_batch(campaign, method, batch)
            except Exception as error:
                outcomes[name + '/' + method] = {'status': 'cleanup_error', 'error': str(error)}
    after = metrics()
    after['alerts'] = alerts(after)
    after['alerts'].extend('WARNING: cleanup blocked for ' + name + ': ' + row['error']
                           for name, row in outcomes.items() if row['status'] == 'cleanup_error')
    after['cleanup'] = outcomes
    after['available_data_gib_before'] = before['disks']['/data']['available_gib']
    temporary = STATE / 'latest.json.tmp'
    temporary.write_text(json.dumps(after, indent=2) + '\n')
    temporary.replace(STATE / 'latest.json')
    journal({'action': 'hourly_check', **after})
    print(json.dumps(after, indent=2))
    if after['alerts']:
        (STATE / 'ALERTS.txt').write_text(after['at'] + '\n' + '\n'.join(after['alerts']) + '\n')
    else:
        (STATE / 'ALERTS.txt').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
