"""Read-only Experiment 2 handoff audit; run on 203 and capture stdout locally.

No model calls, downloads, synthesis, SQLite updates, or experiment mutations.
Each database uses a read transaction; the whole live-run snapshot is not atomic.
"""
import collections
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

BASE = Path('/data/yangao/exp2_rerun/runs')
QUOTA = {'small': 70, 'medium': 90, 'large': 40}


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def lines(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass  # A concurrent writer may not have completed the final line.
    return rows


def norm(url):
    return str(url).rstrip('/').removesuffix('.git').lower()


def bin_for(cells):
    if cells is None or cells < 100 or cells >= 100000:
        return 'out_of_range'
    return 'small' if cells < 1000 else 'medium' if cells < 10000 else 'large'


def db_rows(path, sql):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        return [dict(row) for row in connection.execute(sql)]
    finally:
        connection.close()


def bindings(campaign):
    manifest = read(campaign / 'execution_manifest.json')
    records = [manifest[k] for k in ('protocol', 'task_spec', 'submission_schema', 'model_route_preflight')]
    records += manifest.get('implementation_bindings', [])
    result = []
    for record in records:
        path = Path(record['path'])
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        result.append({'role': record.get('role', path.name), 'path': str(path),
                       'matches': actual == record['sha256'], 'sha256': actual})
    return result


def native(arm):
    campaign = BASE / 'exp2-v5b-20261114/campaign'
    root = campaign / 'method_runs' / (arm + '.batch1.formal')
    state = read(root / 'runner_state.json')
    submission = read(root / 'submission.json')
    wall_seconds = (datetime.datetime.fromisoformat(submission['ended_at'])
                    - datetime.datetime.fromisoformat(submission['started_at'])).total_seconds()
    manifest = read(campaign / 'execution_manifest.json')
    batch = next(b for b in manifest['batches'] if b['method_id'] == arm and b['batch_id'] == 1)
    locked_path = Path(batch['submission_path']) if batch.get('submission_path') else None
    locked_hash = hashlib.sha256(locked_path.read_bytes()).hexdigest() if locked_path and locked_path.is_file() else None
    history = {h['candidate_id']: h for h in state['validation_history'] if h.get('precheck_qualified')}
    submitted = []
    for candidate in submission['candidates']:
        h = history[candidate['candidate_id']]
        submitted.append({'candidate_id': candidate['candidate_id'], 'repo_url': norm(candidate['repo_url']),
                          'commit': candidate['commit'], 'top_module': candidate['top_module'],
                          'mapped_cells': h['mapped_cells'], 'size_bin': bin_for(h['mapped_cells'])})
    return {'root': str(root), 'stop_reason': submission['stop_reason'], 'submitted_count': len(submitted),
            'started_at': submission['started_at'], 'ended_at': submission['ended_at'],
            'timestamp_wall_seconds': wall_seconds,
            'runtime_gap_seconds': wall_seconds - submission['resource_usage']['method_runtime_seconds'],
            'size_counts': dict(collections.Counter(c['size_bin'] for c in submitted)),
            'submitted': submitted, 'resource_usage': submission['resource_usage'],
            'native_run': read(root / 'native_run.json'),
            'manifest_batch': batch, 'locked_submission_digest_matches': locked_hash == batch.get('submission_sha256'),
            'locked_submission_equals_runner_submission': read(locked_path) == submission if locked_path else False,
            'method_result': read(root / 'method_result.json'),
            'queries': state['queries'], 'recovery_count': state.get('recovery_count')}


def r2g(arm, controls):
    campaign = BASE / 'exp2-v5s-20261114/campaign'
    root = campaign / 'method_runs' / (arm + '.batch1.formal')
    corpus = campaign / 'method_runs' / (arm + '.formal_state/expander_corpus')
    pre = lines(root / 'precheck_log.jsonl')
    qualified = [p for p in pre if p.get('publishable_qualified')]
    resume = read(root / 'resume_state.json')
    frontier = db_rows(corpus / 'state/frontier.sqlite', 'SELECT * FROM repositories')
    repos = {norm(r['canonical_url']): r for r in frontier}
    queries = db_rows(corpus / 'state/frontier.sqlite', 'SELECT * FROM queries')
    events = db_rows(corpus / 'state/frontier.sqlite', 'SELECT query_id,repository_key FROM discovery_events')
    by_key = {r['repository_key']: norm(r['canonical_url']) for r in frontier}
    query_urls = collections.defaultdict(set)
    for e in events:
        query_urls[e['query_id']].add(by_key.get(e['repository_key'], ''))
    qualified_by_repo = collections.Counter(norm(p['repo_url']) for p in qualified)
    large_by_repo = collections.Counter(norm(p['repo_url']) for p in qualified if bin_for(p.get('mapped_cells')) == 'large')
    for q in queries:
        urls = query_urls[q['query_id']]
        q['qualified_designs_associated'] = sum(qualified_by_repo[u] for u in urls)
        q['large_designs_associated'] = sum(large_by_repo[u] for u in urls)
    designs = db_rows(corpus / 'state/corpus.sqlite', 'SELECT * FROM designs')
    corpus_counts = collections.Counter()
    license_reasons = collections.Counter()
    samples = []
    generic_by_id = {}
    generic_by_key = {}
    ratios = []
    checked_by_id = {p['candidate_id'].removeprefix('exp_'): p for p in pre}
    for row in designs:
        p = json.loads(row.pop('payload_json'))
        stats = read(corpus / 'synthesis/generic' / row['design_id'] / 'stats.json', {})
        generic = stats.get('design', {}).get('num_cells')
        generic_by_id[row['design_id']] = generic
        gbin = 'unknown' if generic is None else '<1k' if generic < 1000 else '1k-10k' if generic < 10000 else '10k-200k' if generic < 200000 else '>=200k'
        corpus_counts[(gbin, str(row['synthesis_valid']), row['release_policy'])] += 1
        prov = p.get('provenance') or {}
        url = norm(prov.get('repository_url'))
        top = (p.get('build') or {}).get('top_module')
        generic_by_key[(url, top)] = generic
        check = checked_by_id.get(row['design_id'])
        if check and check.get('mapped_cells') is not None and generic:
            ratios.append({'generic': generic, 'mapped': check['mapped_cells'],
                           'ratio': round(check['mapped_cells'] / generic, 3),
                           'generic_estimated_bin': bin_for(generic * 7), 'mapped_bin': bin_for(check['mapped_cells'])})
        if row['release_policy'] == 'QUARANTINE' and generic is not None and 1000 <= generic < 200000:
            release = p.get('release') or {}
            evidence = release.get('license_evidence') or {}
            for reason in evidence.get('resolution_states') or ['UNSPECIFIED']:
                license_reasons[reason] += 1
            if len(samples) < 12:
                source_root = Path((p.get('source') or {}).get('original_root') or '/nonexistent')
                root_license_names = [x.name for x in source_root.iterdir()
                                      if x.is_file() and x.name.lower().startswith(('license', 'licence', 'copying', 'copyright'))] if source_root.is_dir() else []
                samples.append({'repo_url': url, 'design_id': row['design_id'], 'generic_cells': generic,
                                'release': release, 'source_root_exists': source_root.is_dir(),
                                'root_license_names': root_license_names})
    overlaps = {}
    for name, control in controls.items():
        large = [c for c in control['submitted'] if c['size_bin'] == 'large']
        urls = sorted({c['repo_url'] for c in large})
        overlaps[name] = {'submitted_large_designs': len(large), 'distinct_large_repositories': len(urls),
                          'known': sum(u in repos for u in urls),
                          'repositories': [{'repo_url': u, 'known': u in repos,
                            **({k: repos[u][k] for k in ('state', 'acquisition_status', 'size_bytes', 'priority', 'design_likelihood', 'license_hint')} if u in repos else {})} for u in urls]}
    bridges = {}
    for path in sorted((root / 'replenishment').glob('round*/expander_bridge.json')):
        for c in read(path, {}).get('candidates', []):
            bridges[c['expander_design_id']] = c
    checked_ids = {p['candidate_id'].removeprefix('exp_') for p in pre}
    estimated_unchecked = [did for did in bridges if did not in checked_ids
                          and generic_by_id.get(did) is not None
                          and 10000 <= generic_by_id[did] * 7 < 100000]
    return {'root': str(root), 'heartbeat': read(root / 'heartbeat.json'),
            'last_completed_round': resume['round_index'],
            'last_completed_selection': resume['selection_payload'],
            'prechecks': len(pre), 'qualified_raw': len(qualified),
            'qualified_raw_size_counts': dict(collections.Counter(bin_for(p.get('mapped_cells')) for p in qualified)),
            'failures': dict(collections.Counter(p.get('failure_class') for p in pre if not p.get('publishable_qualified'))),
            'precheck_seconds': round(sum(p.get('seconds', 0) for p in pre), 1),
            'slow_checks_ge_1100s': [{'candidate_id': p['candidate_id'], 'failure_class': p.get('failure_class'),
                                    'seconds': p.get('seconds')} for p in pre if p.get('seconds', 0) >= 1100],
            'deferred': dict(collections.Counter(p['reason'] for p in lines(root / 'precheck_deferred.jsonl'))),
            'frontier_repositories': len(frontier),
            'frontier_states': dict(collections.Counter(r['state'] + '/' + r['acquisition_status'] for r in frontier)),
            'over_64mib': sum((r['size_bytes'] or 0) > 64 * 2**20 for r in frontier),
            'queries': queries, 'planner_rounds': lines(root / 'planner_rounds.jsonl'),
            'planner_usage': read(root / 'planner_usage.json'),
            'corpus_counts': [{'generic_bin': k[0], 'synthesis_valid': k[1], 'release_policy': k[2], 'count': n}
                              for k, n in sorted(corpus_counts.items())],
            'corpus_designs': len(designs), 'quarantined_midlarge_license_reasons': dict(license_reasons),
            'quarantined_midlarge_samples': samples, 'control_overlap': overlaps,
            'bridged_candidates': len(bridges), 'unchecked_estimated_large_candidates': len(estimated_unchecked),
            'generic_mapped_pairs': ratios,
            'latest_log_ages_seconds': {str(p.relative_to(root)): round(time.time() - p.stat().st_mtime)
                                       for p in root.rglob('*.log') if p.is_file() and time.time() - p.stat().st_mtime < 300}}


result = {'captured_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
          'snapshot_consistency': 'Each DB read is transactional; live files and separate DBs are read at different times.',
          'quota': QUOTA, 'native': {}}
for arm in ('native-codex', 'native-dsh'):
    result['native'][arm] = native(arm)
result['r2g'] = {arm: r2g(arm, result['native']) for arm in ('r2g-expander-deepseek', 'r2g-expander-gpt')}
result['campaign_bindings'] = {cid: bindings(BASE / cid / 'campaign')
                               for cid in ('exp2-v5b-20261114', 'exp2-v5s-20261114')}
result['disk'] = subprocess.check_output(['df', '-B1', '/data'], text=True)
print(json.dumps(result, indent=2, ensure_ascii=False))
