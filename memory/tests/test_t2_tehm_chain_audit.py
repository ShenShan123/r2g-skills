"""Offline evidence alignment must not turn missing data into working memory."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location(
    'chain_audit', Path(__file__).parents[1]/'scripts/t2_tehm_chain_audit.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


def test_store_reads_are_byte_preserving_and_missing_knowledge_is_unknown(tmp_path):
    p = tmp_path/'tehm.sqlite'
    conn = sqlite3.connect(p)
    conn.executescript('CREATE TABLE tehm_rules(rule_id,validity_status,before_pattern_json,validity_profile_json);'
                       'CREATE TABLE tehm_views(view_type);'
                       "INSERT INTO tehm_views VALUES('semantic');")
    conn.close()
    before = A.sha(p)
    report = A.read_store(p)
    assert report['counts']['tehm_mechanism_knowledge'] is None
    assert report['counts']['tehm_rules'] == 0
    assert report['views'] == {'semantic': 1}
    assert A.sha(p) == before
    assert sorted(f.name for f in tmp_path.iterdir()) == ['tehm.sqlite']


@pytest.mark.parametrize('change', ['modify', 'remove'])
def test_preservation_check_rejects_changed_evidence(tmp_path, change):
    p = tmp_path/'evidence.json'
    p.write_text('original')
    protected = {str(p): A.sha(p)}
    if change == 'modify':
        p.write_text('changed')
    else:
        p.unlink()
    with pytest.raises(ValueError, match='Protected evidence changed'):
        A.verify_protected(protected)


def test_preservation_check_accepts_unchanged(tmp_path):
    p = tmp_path/'evidence.json'
    p.write_text('original')
    A.verify_protected({str(p): A.sha(p)})


def test_episode_does_not_confuse_snapshot_with_router_or_attempt_with_memory(tmp_path):
    p = tmp_path/'episode.json'
    write(p, {'status': 'NOT_ACCEPTED', 'repair_turns': [
        {'status': 'FAILED', 'flow_launched': True},
        {'status': 'DUPLICATE_NOT_EXECUTED', 'flow_launched': False}]})
    write(tmp_path/'reports/resource_ledger.jsonl', {'type': 'memory_snapshot'})
    write(tmp_path/'reports/fix_log.jsonl', {'strategy': 'density_relief'})
    write(tmp_path/'repair-turn-1.try0.request.json', {'messages': [{'role': 'user', 'content': 'evidence'}]})
    result = A.episode_summary({'episode': str(p), 'design': 'x'})
    assert result['repair_generations'] == 2
    assert result['flows_launched_by_repair_generations'] == 1
    assert result['route_event_count'] == result['executed_memory_fix_rows'] == 0
    assert result['historical_prompt_injection'] == [False]


def test_episode_counts_real_memory_execution_and_context_separately(tmp_path):
    p = tmp_path/'episode.json'
    write(p, {'status': 'ACCEPTED'})
    write(tmp_path/'reports/resource_ledger.jsonl', {'type': 'memory_retrieval'})
    write(tmp_path/'reports/fix_log.jsonl', {'strategy': 'tehm_rule_x'})
    write(tmp_path/'repair-turn-1.try0.request.json', {'messages': [
        {'content': 'Frozen historical memory (not current-task evidence):\ncontext'}]})
    result = A.episode_summary({'episode': str(p), 'design': 'x'})
    assert result['executed_memory_fix_rows'] == result['retrieval_event_count'] == 1
    assert result['route_event_count'] == 0
    assert result['historical_prompt_injection'] == [True]


def test_cli_refuses_output_inside_retained_package(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['audit', '--root', str(tmp_path), '--out', str(tmp_path/'new.json')])
    with pytest.raises(ValueError, match='outside'):
        A.main()
    assert not (tmp_path/'new.json').exists()


def test_cli_refuses_overwriting_receipt(tmp_path, monkeypatch):
    out = tmp_path/'receipt.json'
    out.write_text('keep')
    monkeypatch.setattr('sys.argv', ['audit', '--root', str(tmp_path/'package'), '--out', str(out)])
    with pytest.raises(ValueError, match='new'):
        A.main()
    assert out.read_text() == 'keep'
