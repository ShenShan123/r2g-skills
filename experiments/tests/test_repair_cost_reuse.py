import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('repair_cost', Path(__file__).parents[1] / 'measure_agent_repair_cost.py')
cost = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cost)


def test_full_population_reuses_completed_receipt_without_model_or_eda(tmp_path, monkeypatch):
    old = tmp_path / 'pilot' / 'cases' / 'nangate45_C1'
    old.mkdir(parents=True)
    state = {'case_id': 'nangate45_C1', 'initial_started': 'timestamp', 'status': 'complete'}
    summary = {'case_id': 'nangate45_C1', 'status': 'complete', 'tokens': 42}
    (old / 'state.json').write_text(json.dumps(state))
    (old / 'summary.json').write_text(json.dumps(summary))
    before = (old / 'state.json').read_bytes()
    monkeypatch.setattr(cost, 'stream_chat', lambda *a, **k: pytest.fail('duplicate API call'))
    monkeypatch.setattr(cost, 'prepare_input', lambda *a, **k: pytest.fail('replaced original input'))
    args = SimpleNamespace(output=tmp_path / 'full', dry_run=False, job_timeout=1, poll_seconds=.01)
    case = {'id': 'nangate45_C1', 'reuse_case_directory': str(old)}
    assert cost.run_case(case, args) == summary
    assert cost.run_case(case, args) == summary
    assert (old / 'state.json').read_bytes() == before
    assert (args.output / 'cases' / case['id']).resolve() == old


def test_reuse_requires_matching_attempt_receipt(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    (old / 'state.json').write_text(json.dumps({'case_id': 'other', 'initial_started': 'time'}))
    args = SimpleNamespace(output=tmp_path / 'full', dry_run=True)
    with pytest.raises(ValueError, match='identity'):
        cost.reuse_case({'id': 'wanted', 'reuse_case_directory': str(old)}, args)


def test_input_preserves_original_compilation_defines_without_strategy_metadata(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'dut.v').write_text('module dut; endmodule\n')
    (src / 'design.json').write_text('{"origin":"historical_recipe"}')
    case = {'id': 'case', 'input_root': str(src), 'top': 'dut', 'source_files': ['dut.v'],
            'defines': ['WIDTH=8'], 'historical_category': 'do-not-inject'}
    result = cost.prepare_input(case, tmp_path / 'output')
    manifest = json.loads((result / 'design.json').read_text())
    assert manifest['defines'] == ['WIDTH=8']
    assert 'origin' not in manifest and 'historical_category' not in manifest


def test_status_get_timeout_retries_without_another_model_post(monkeypatch):
    calls = []
    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return b'{"status":"running"}'
    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise TimeoutError('temporary loaded host')
            return Reply()
    monkeypatch.setattr(cost.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(cost.time, 'sleep', lambda *a: None)
    monkeypatch.setattr(cost, 'stream_chat', lambda *a, **k: pytest.fail('GET retry spent model tokens'))
    assert cost.get('http://localhost', '/api/jobs/one') == {'status': 'running'}
    assert len(calls) == 2
