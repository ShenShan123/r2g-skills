import contextlib
import json

import httpx
import pytest

from r2g_agent import agent_loop
from r2g_agent.config import ModelConfig
from r2g_agent.llm.anthropic import AnthropicAdapter
from r2g_agent.llm.openai_compat import OpenAICompatAdapter
from r2g_agent.llm.usage import normalize_usage


def stream(monkeypatch, events, status=200):
    requests = []
    class Response:
        status_code = status
        def read(self):
            return b'rejected'
        def iter_lines(self):
            for ev in events:
                if isinstance(ev, Exception):
                    raise ev
                yield 'data: ' + (ev if isinstance(ev, str) else json.dumps(ev))
    @contextlib.contextmanager
    def request(*args, **kwargs):
        requests.append(kwargs)
        yield Response()
    monkeypatch.setattr(httpx, 'stream', request)
    return requests


def test_openai_final_empty_choices_usage_and_cache(monkeypatch):
    raw = {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150,
           'prompt_cache_hit_tokens': 100, 'prompt_cache_miss_tokens': 20,
           'completion_tokens_details': {'reasoning_tokens': 12}}
    requests = stream(monkeypatch, [{'id': 'res1', 'choices': [], 'usage': raw}, '[DONE]'])
    events = list(OpenAICompatAdapter(ModelConfig().resolved(), 'secret').stream([], []))
    usage = next(e for e in events if e['type'] == 'usage')
    assert requests[0]['json']['stream_options'] == {'include_usage': True}
    assert usage['total_tokens'] == 150
    assert usage['cache_read_input_tokens'] == 100
    assert usage['uncached_input_tokens'] == 20
    assert usage['reasoning_tokens'] == 12
    assert usage['stream_complete'] and usage['response_id'] == 'res1'
    assert 'secret' not in json.dumps(usage)


def test_anthropic_cumulative_output_not_double_counted(monkeypatch):
    stream(monkeypatch, [
        {'type': 'message_start', 'message': {'id': 'm1', 'usage': {
            'input_tokens': 8, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 10,
            'output_tokens': 1}}},
        {'type': 'message_delta', 'usage': {'output_tokens': 7}},
        {'type': 'message_delta', 'usage': {'output_tokens': 12}},
        {'type': 'message_stop'},
    ])
    ad = AnthropicAdapter(ModelConfig(provider='anthropic').resolved(), 'secret')
    usage = next(e for e in ad.stream([], []) if e['type'] == 'usage')
    assert usage['input_tokens'] == 38
    assert usage['output_tokens'] == 12
    assert usage['total_tokens'] == 50
    assert usage['stream_complete']


@pytest.mark.parametrize('status,events', [(401, []), (200, ['[DONE]']),
    (200, [httpx.ReadTimeout('interrupted')])])
def test_unreported_usage_is_unknown(monkeypatch, status, events):
    stream(monkeypatch, events, status)
    ad = OpenAICompatAdapter(ModelConfig().resolved(), 'secret')
    usage = next(e for e in ad.stream([], []) if e['type'] == 'usage')
    assert usage['input_tokens'] is None and usage['total_tokens'] is None
    assert not usage['usage_reported']


def test_openai_standard_cached_counter():
    usage = normalize_usage({'prompt_tokens': 20, 'completion_tokens': 3,
                             'prompt_tokens_details': {'cached_tokens': 12}}, 'openai')
    assert usage['uncached_input_tokens'] == 8
    assert usage['cache_creation_input_tokens'] is None
    assert usage['total_tokens'] == 23


def test_call_ledger_survives_errors_and_deletes_with_session(fake_adapter):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    usage = {'type': 'usage', 'stream_complete': True,
             **normalize_usage({'prompt_tokens': 20, 'completion_tokens': 3}, 'openai')}
    ad = fake_adapter([[usage, {'type': 'tool_call', 'id': 't', 'name': 'list_examples', 'arguments': {}}],
                       [{'type': 'error', 'message': 'failed'}]])
    list(agent_loop.run_turn(ad, ss, sid, 'test'))
    ledger = agent_loop.SessionStore(ss.path).usage(sid)
    assert ledger['model_calls'] == 2
    assert ledger['complete_usage_calls'] == 1
    assert ledger['totals']['total_tokens'] is None
    assert ledger['calls'][0]['total_tokens'] == 23
    ss.erase(sid)
    assert ss.usage(sid)['model_calls'] == 0


def test_repeated_usage_snapshot_replaces_one_call(fake_adapter):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    usage = {'type': 'usage', 'stream_complete': True,
             **normalize_usage({'prompt_tokens': 20, 'completion_tokens': 3}, 'openai')}
    list(agent_loop.run_turn(fake_adapter([[usage, usage]]), ss, sid, 'test'))
    assert ss.usage(sid)['model_calls'] == 1
    assert ss.usage(sid)['totals']['total_tokens'] == 23


def test_partial_stream_does_not_claim_full_usage(fake_adapter):
    ss = agent_loop.SessionStore()
    sid = ss.create()
    usage = {'type': 'usage', 'stream_complete': False,
             **normalize_usage({'prompt_tokens': 20, 'completion_tokens': 3}, 'openai')}
    list(agent_loop.run_turn(fake_adapter([[usage]]), ss, sid, 'test'))
    report = ss.usage(sid)
    assert report['totals']['total_tokens'] is None
    assert report['observed_totals']['total_tokens'] == 23
    assert not report['usage_complete']


def test_usage_endpoint_existing_and_deleted_session(monkeypatch):
    from fastapi import HTTPException
    from r2g_agent import server
    ss = agent_loop.SessionStore()
    sid = ss.create()
    monkeypatch.setitem(server.STATE, 'sessions', ss)
    assert server.session_usage(sid)['model_calls'] == 0
    ss.erase(sid)
    with pytest.raises(HTTPException) as exc:
        server.session_usage(sid)
    assert exc.value.status_code == 404
