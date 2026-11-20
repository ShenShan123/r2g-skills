"""Read-only follow-up: validate locked submissions and trace control-repo candidates."""
import collections
import datetime
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

BASE = Path('/data/yangao/exp2_rerun')
sys.path.insert(0, str(BASE / 'r2g-exp2-v5'))
shared = importlib.import_module('tools.run_experiment1_rtl_acquisition')
native_campaign = BASE / 'runs/exp2-v5b-20261114/campaign'
out = {'captured_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
       'submission_validation': {}, 'control_large_candidates': {}, 'r2g_control_traces': {}}
out['license_failure_details'] = {}
out['license_text_samples'] = []
controls = set()
for arm in ('native-codex', 'native-dsh'):
    root = native_campaign / 'method_runs' / (arm + '.batch1.formal')
    locked = native_campaign / 'submissions' / (arm + '.batch1.json')
    submission, errors = shared.validate_submission(locked, campaign_root=native_campaign)
    out['submission_validation'][arm] = {'errors': errors,
        'eligibility': shared.submission_score_eligibility(submission)}
    state = json.loads((root / 'runner_state.json').read_text())
    history = {h['candidate_id']: h for h in state['validation_history'] if h.get('precheck_qualified')}
    large = [c for c in submission['candidates'] if 10000 <= history[c['candidate_id']]['mapped_cells'] < 100000]
    controls.update(c['repo_url'].lower().rstrip('/') for c in large)
    out['control_large_candidates'][arm] = [{'repo_url': c['repo_url'], 'top_module': c['top_module'],
        'top_parameters': c['top_parameters'], 'rtl_files': c['rtl_files'],
        'mapped_cells': history[c['candidate_id']]['mapped_cells']} for c in large]

for arm in ('r2g-expander-deepseek', 'r2g-expander-gpt'):
    parent = BASE / 'runs/exp2-v5s-20261114/campaign/method_runs'
    corpus = parent / (arm + '.formal_state/expander_corpus')
    root = parent / (arm + '.batch1.formal')
    connection = sqlite3.connect((corpus / 'state/corpus.sqlite').as_uri() + '?mode=ro', uri=True)
    connection.execute('BEGIN')
    traced = []
    for did, release, payload in connection.execute('SELECT design_id,release_policy,payload_json FROM designs'):
        p = json.loads(payload)
        url = (p.get('provenance') or {}).get('repository_url', '').lower().rstrip('/')
        if url in controls:
            traced.append({'repo_url': url, 'design_id': did, 'release_policy': release,
                'license_evidence': (p.get('release') or {}).get('license_evidence'),
                'build': p.get('build'), 'family_id': p.get('family_id')})
    connection.close()
    checks = [json.loads(l) for l in (root / 'precheck_log.jsonl').read_text().splitlines()]
    out['r2g_control_traces'][arm] = {'corpus': traced,
        'prechecks': [p for p in checks if p['repo_url'].lower().rstrip('/') in controls]}
    details = []
    for p in checks:
        if p.get('failure_class') != 'license_evidence_incomplete':
            continue
        for result_path in (root / 'precheck').glob('round*/' + p['candidate_id'] + '/result.json'):
            result = json.loads(result_path.read_text())
            details.append({'candidate_id': p['candidate_id'], 'repo_url': p['repo_url'],
                'top_module': p['top_module'], 'mapped_cells': p.get('mapped_cells'),
                'technical_qualified': result.get('technical_qualified'),
                'source_evidence': {k: v for k, v in result.get('source_evidence', {}).items() if k.startswith('license_')},
                'license_identifiers': p.get('license_identifiers')})
    out['license_failure_details'][arm] = details
    sampled_urls = set()
    for bridge_path in sorted((root / 'replenishment').glob('round*/expander_bridge.json')):
        for c in json.loads(bridge_path.read_text()).get('candidates', []):
            url = c['repository_url']
            if not url.startswith('https://github.com/secworks/') or url in sampled_urls:
                continue
            if c['top_module'] not in ('aes', 'poly1305', 'blake2s', 'sha256_axi4'):
                continue
            sampled_urls.add(url)
            text_path = Path(c['source_root']) / 'LICENSE'
            out['license_text_samples'].append({'repo_url': url, 'path': str(text_path),
                'sha256': hashlib.sha256(text_path.read_bytes()).hexdigest(),
                'text': text_path.read_text(), 'bridge_evidence': c['license_evidence']})
print(json.dumps(out, indent=2))
