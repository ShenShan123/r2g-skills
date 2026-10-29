import io
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters'))
sys.path.insert(0, str(ROOT))


def chunks(*, usage=True, done=True, model='deepseek-flash'):
    values = [
        {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': 'think'}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {'content': 'hello', 'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"com'}}]}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'mand":"ls"}'}}]}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
    ]
    if usage:
        values[-1]['usage'] = {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}
    text = ': keepalive\n\n' + ''.join('data: ' + json.dumps({'id': 'response_1', 'model': model, **v}) + '\n\n' for v in values)
    return (text + ('data: [DONE]\n\n' if done else '')).encode()


class Response(io.BytesIO):
    headers = {'content-type': 'text/event-stream', 'x-request-id': 'request-1'}
    status = 200


def test_provider_streams_once_and_assembles_tool_calls(tmp_path):
    from meter import MeteredProvider
    provider = MeteredProvider(tmp_path / 'provider', 'fake-test-key')
    calls = []

    def send(request, **kwargs):
        calls.append(json.loads(request.data))
        return Response(chunks())

    result = provider.request({'messages': [{'role': 'user', 'content': 'test'}]}, transport=send)
    assert len(calls) == 1
    assert calls[0]['stream'] is True
    assert calls[0]['stream_options']['include_usage'] is True
    assert result['choices'][0]['message']['tool_calls'][0]['function'] == {'name': 'bash', 'arguments': '{"command":"ls"}'}
    assert result['choices'][0]['message']['reasoning_content'] == 'think'
    rows = [json.loads(x) for x in provider.journal.path.read_text().splitlines()]
    assert len([x for x in rows if x['event'] == 'request_started']) == 1
    assert rows[-1]['usage']['total_tokens'] == 10


def test_stream_without_usage_stops_without_retry(tmp_path):
    from meter import MeteredProvider, UnknownUsage
    provider = MeteredProvider(tmp_path / 'provider', 'fake-test-key')
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        return Response(chunks(usage=False))
    with pytest.raises(UnknownUsage):
        provider.request({'messages': []}, transport=send)
    with pytest.raises(UnknownUsage):
        provider.request({'messages': []}, transport=send)
    assert calls == [1]
    rows = [json.loads(x) for x in provider.journal.path.read_text().splitlines()]
    assert next(x for x in rows if x['event'] == 'request_ended')['usage'] == 'UNKNOWN'


def test_truncated_stream_retains_known_usage_but_never_executes(tmp_path):
    from meter import MeteredProvider, IncompleteResponse
    provider = MeteredProvider(tmp_path / 'provider', 'fake-test-key')
    with pytest.raises(IncompleteResponse):
        provider.request({'messages': []}, transport=lambda *a, **k: Response(chunks(done=False)))
    rows = [json.loads(x) for x in provider.journal.path.read_text().splitlines()]
    assert rows[-1]['usage']['total_tokens'] == 10
    assert rows[-1]['response_complete'] is False


def test_no_artificial_three_request_limit(tmp_path):
    from meter import MeteredProvider
    provider = MeteredProvider(tmp_path / 'provider', 'fake-test-key')
    for _ in range(4):
        assert provider.request({'messages': []}, transport=lambda *a, **k: Response(chunks()))['usage']['total_tokens'] == 10


def test_provider_partial_transport_failure_is_unknown_and_one_attempt(tmp_path):
    from meter import MeteredProvider, UnknownUsage
    provider = MeteredProvider(tmp_path / 'provider', 'fake-test-key')
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise ConnectionResetError('test reset')
    with pytest.raises(UnknownUsage):
        provider.request({'messages': []}, transport=send)
    assert calls == [1]
    assert provider.blocked is True


def test_submission_uses_exact_repository_bytes_not_pty_display(tmp_path):
    from run_swe import validated_submission
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'a.py').write_text('old\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base'], check=True)
    (tmp_path / 'a.py').write_text('new\n')
    (tmp_path / 'new.py').write_text('new file\n')
    class Agent:
        info = {'exit_status': 'submitted', 'submission': 'corrupt display patch'}
    patch, receipt = validated_submission(Agent(), tmp_path)
    assert '+new file' in patch and '+new' in patch
    assert receipt['native_patch_matches_worktree'] is False
    assert receipt['producer'] == 'exact private worktree git diff --binary HEAD'


def test_error_exit_is_not_mislabelled_as_patch_failure(tmp_path):
    from run_swe import validated_submission
    class Agent:
        info = {'exit_status': 'exit_error', 'submission': None}
    with pytest.raises(ValueError, match='ended without submission'):
        validated_submission(Agent(), tmp_path)


def test_scope_keeps_successes_and_collector_pass_out():
    plan = json.loads((ROOT / 'inputs/plan.json').read_text())
    assert len(plan['retry_cells']) == 15
    assert not any(c['task'] in {'sympy__sympy-15349', 'sphinx-doc__sphinx-8593', 'astropy__astropy-12907'} for c in plan['retry_cells'])
    assert not any(c['task'] == 'sphinx-doc__sphinx-9673' and c['arm'] == 'tehm' for c in plan['retry_cells'])
    assert all('history_snapshot' in c for c in plan['retry_cells'] if c['arm'] == 'tehm')


def test_unknown_escapes_native_exception_requery_handlers(tmp_path, monkeypatch):
    import run_swe
    monkeypatch.setattr(run_swe, 'ROOT', tmp_path)
    provider = run_swe.BatchProvider(tmp_path / 'provider', 'fake-test-key')
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise ConnectionResetError('offline fixture')
    assert not issubclass(run_swe.EpisodeInterrupted, Exception)
    with pytest.raises(run_swe.EpisodeInterrupted):
        provider.request({'messages': []}, transport=send)
    assert calls == [1]
    assert provider.blocked is True


def test_payment_fault_blocks_next_request(tmp_path, monkeypatch):
    import run_swe
    import urllib.error
    monkeypatch.setattr(run_swe, 'ROOT', tmp_path)
    provider = run_swe.BatchProvider(tmp_path / 'provider', 'fake-test-key')
    def send(*args, **kwargs):
        raise urllib.error.HTTPError('https://api.deepseek.com/chat/completions', 402,
                                     'offline payment fixture', {}, io.BytesIO(b'{}'))
    with pytest.raises(run_swe.EpisodeInterrupted):
        provider.request({'messages': []}, transport=send)
    assert (tmp_path / 'reports/TECHNICAL-PAUSE.json').exists()
    next_provider = run_swe.BatchProvider(tmp_path / 'next-provider', 'fake-test-key')
    with pytest.raises(run_swe.UnknownUsage):
        next_provider.request({'messages': []}, transport=lambda *a, **k: pytest.fail('must not send'))
    assert not any(json.loads(line)['event'] == 'request_started' for line in next_provider.journal.path.read_text().splitlines())


def test_task_history_is_private_and_restores_only_old_snapshot(tmp_path):
    import hashlib
    import sqlite3
    from recovery_state import seed_task_history
    src = tmp_path / 'snapshot'
    src.mkdir()
    (src / 'artifacts').mkdir()
    with sqlite3.connect(src / 'tehm.sqlite') as conn:
        conn.execute('create table fixture (n integer)')
        conn.execute('insert into fixture values (1)')
    before = hashlib.sha256((src / 'tehm.sqlite').read_bytes()).hexdigest()
    cell = {'arm': 'tehm', 'history_snapshot': str(src), 'history_db_sha256': before}
    seed_task_history(tmp_path / 'retry', cell)
    dest = tmp_path / 'retry/hosts/swe-agent/tehm/memory/tehm.sqlite'
    with sqlite3.connect(dest) as conn:
        conn.execute('insert into fixture values (2)')
    with sqlite3.connect(src / 'tehm.sqlite') as conn:
        assert conn.execute('select count(*) from fixture').fetchone()[0] == 1
    with pytest.raises(ValueError, match='already started'):
        seed_task_history(tmp_path / 'retry', cell)
