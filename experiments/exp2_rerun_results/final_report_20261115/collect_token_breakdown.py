"""Read-only token audit; run on 203. No model requests or pricing assumptions."""
import datetime
import hashlib
import json
from pathlib import Path

BASE = Path('/data/yangao/exp2_rerun/runs')
result = {'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'arms': []}


def read(path):
    return json.loads(path.read_text())


def source(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


for method in ['native-codex', 'native-dsh', 'r2g-expander-gpt', 'r2g-expander-deepseek']:
    campaign = 'exp2-v5b-20261114' if method.startswith('native-') else 'exp2-v5fix2-20261115'
    root = BASE / campaign / 'campaign/method_runs' / (method + '.batch1.formal')
    submission = read(root / 'submission.json')['resource_usage']
    sources = [source(root / 'submission.json')]
    notes = []
    if method.startswith('native-'):
        path = root / 'harness/usage.json'
        sources.append(source(path))
        calls = read(path)['calls']
        inp = sum(c['input_tokens'] for c in calls)
        cached = sum(c['cache_read_input_tokens'] for c in calls)
        output = sum(c['output_tokens'] for c in calls)
        reasoning = sum(c.get('reasoning_tokens', 0) for c in calls)
        count = len(calls)
        status = 'per_call_ledger'
        if method == 'native-dsh':
            previous_path = root / 'runner_state.pre_restart_20261114_141818.json'
            previous = read(previous_path)
            sources.append(source(previous_path))
            incident_path = root / 'INCIDENT_20261114_141818.json'
            sources.append(source(incident_path))
            cutoff_ms = datetime.datetime.strptime(read(incident_path)['at'], '%Y-%m-%dT%H:%M:%S%z').timestamp() * 1000
            sessions = []
            for path in sorted((root / 'harness/dsh_home/storages/session_projcache/sessions').glob('*.json')):
                record = read(path)['record']
                if record['identity']['createdAt'] >= cutoff_ms:
                    continue
                usage = record['rows']['tokenUsage']['val']['totals']
                sessions.append(usage)
                sources.append(source(path))
            session_input = sum(s['uncachedInputTokens'] + s['cacheReadTokens'] for s in sessions)
            session_output = sum(s['outputTokens'] for s in sessions)
            assert len(sessions) == 10
            assert session_output == previous['usage']['output'] == 198447
            assert previous['usage']['reasoning'] == 0
            inp += previous['usage']['input']
            output += previous['usage']['output']
            count += previous['turn_count']
            cached += sum(s['cacheReadTokens'] for s in sessions)
            input_gap = previous['usage']['input'] - session_input
            assert input_gap == 5120
            # The old proxy ledger was overwritten on restart. Preserve the gap
            # as unclassified instead of silently allocating it to cache misses.
            status = 'recovered_cache_lower_bound'
            notes.append({'pre_restart_sessions': len(sessions),
                          'pre_restart_calls': previous['turn_count'],
                          'post_restart_calls': len(calls),
                          'pre_restart_session_input_tokens': session_input,
                          'pre_restart_recorded_input_tokens': previous['usage']['input'],
                          'input_tokens_without_cache_classification': input_gap,
                          'cache_upper_bound_tokens': cached + input_gap,
                          'reason': 'Pre-restart per-call ledger unavailable; session cache counters retained. '
                                    'All pre-restart output tokens reconcile. Do not allocate the input-counter gap.'})
    else:
        path = root / 'planner_calls.jsonl'
        usage_path = root / 'planner_usage.json'
        sources += [source(path), source(usage_path)]
        calls = [json.loads(line)['usage'] for line in path.read_text().splitlines() if line.strip()]
        aggregate = read(usage_path)
        sums = {key: sum(c[key] for c in calls) for key in ['input', 'cached_input', 'output', 'reasoning', 'total']}
        assert all(sums[key] == aggregate[key] for key in sums)
        inp, cached, reasoning = sums['input'], sums['cached_input'], sums['reasoning']
        output, count = sums['output'] + reasoning, len(calls)
        status = 'planner_recorded_usage'
        if method == 'r2g-expander-gpt':
            notes.append('Cached input is the recorded planner value (0). The parser defaults missing upstream cache details to 0; this does not establish provider-side cache behavior.')
    assert inp == submission['input_tokens']
    assert output == submission['output_tokens'] + submission['reasoning_tokens']
    assert reasoning == submission['reasoning_tokens']
    assert count == submission['llm_turns']
    assert inp + output == submission['total_tokens']
    assert 0 <= cached <= inp
    result['arms'].append({'method_id': method, 'model_calls': count,
                           'input_tokens_including_cache': inp,
                           'cached_input_tokens_recorded_or_recovered': cached,
                           'cache_evidence_status': status,
                           'output_tokens_including_reasoning': output,
                           'output_tokens_excluding_reasoning': output - reasoning,
                           'reasoning_tokens': reasoning, 'total_tokens': inp + output,
                           'notes': notes, 'sources': sources})

print(json.dumps(result, ensure_ascii=False, indent=2))
