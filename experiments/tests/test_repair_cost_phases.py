import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('cost_phases', Path(__file__).parents[1] / 'summarize_agent_repair_cost.py')
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def call(identity, created, total):
    return {'call_id': identity, 'created': created, 'usage_reported': True,
            'stream_complete': True, 'total_tokens': total}


def test_live_dispatch_without_initial_snapshot_is_not_explanation():
    dispatch, explain = summary.phase_usage({'calls': [call('a', 10, 100)]}, {}, {})
    assert dispatch['totals']['total_tokens'] == 100
    assert explain['model_calls'] == 0


def test_explanation_boundary_includes_recovered_dispatch_calls():
    raw = {'calls': [call('first', 10, 100), call('retry', 20, 200), call('explain', 40, 300)]}
    dispatch, explain = summary.phase_usage(raw, {}, {'explanation_started': '1970-01-01T00:00:30+00:00'})
    assert dispatch['totals']['total_tokens'] == 300
    assert explain['totals']['total_tokens'] == 300
