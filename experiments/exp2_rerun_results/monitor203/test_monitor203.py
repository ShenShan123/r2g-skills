import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('monitor203', Path(__file__).with_name('monitor203.py'))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def fixture_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'BASE', tmp_path)
    monkeypatch.setattr(monitor, 'STATE', tmp_path/'monitoring')
    monkeypatch.setattr(monitor, 'FLOW', tmp_path/'flow')
    monitor.STATE.mkdir()
    campaign = tmp_path/'runs/case/campaign'
    output = campaign/'evaluation/native-codex.batch1/example'
    output.mkdir(parents=True)
    net = output/'mapped_netlist.v'
    net.write_text('mapped netlist evidence')
    original = monitor.FLOW/'results/sky130hd/top/exp1_example/1_2_yosys.v'
    original.parent.mkdir(parents=True)
    original.write_bytes(net.read_bytes())
    objects = monitor.FLOW/'objects/sky130hd/top/exp1_example'
    objects.mkdir(parents=True)
    (objects/'temp.bin').write_text('temporary')
    result = {'candidate_id':'example', 'synthesis':{'mapped_netlist':str(net),
        'mapped_netlist_sha256':monitor.sha(net), 'source_mapped_netlist':str(original),
        'flow_variant':'exp1_example'}}
    (output/'result.json').write_text(json.dumps(result))
    report = campaign/'report.json'
    report.write_text(json.dumps({'results':[result]}))
    submission = campaign/'submission.json'
    submission.write_text(json.dumps({'candidates':[{'candidate_id':'example','repo_url':'https://github.com/test/rtl','commit':'a'*40}]}))
    batch = {'status':'complete', 'evaluation_path':str(report), 'evaluation_sha256':monitor.sha(report),
             'submission_path':str(submission), 'submission_sha256':monitor.sha(submission)}
    return campaign, batch, net, original, objects


def test_completed_removes_only_duplicates_and_is_idempotent(tmp_path, monkeypatch):
    campaign, batch, net, original, objects = fixture_batch(tmp_path, monkeypatch)
    outcome = monitor.cleanup_batch(campaign, 'native-codex', batch)
    assert outcome['deleted_directories'] == 2
    assert net.read_text() == 'mapped netlist evidence'
    assert (net.parent/'result.json').is_file()
    assert not original.parent.exists() and not objects.exists()
    assert monitor.cleanup_batch(campaign, 'native-codex', batch)['status'] == 'already_cleaned'


def test_inflight_keeps_all_files(tmp_path, monkeypatch):
    campaign, batch, net, original, objects = fixture_batch(tmp_path, monkeypatch)
    batch['status'] = 'evaluating'
    assert monitor.cleanup_batch(campaign, 'native-codex', batch)['deleted_directories'] == 0
    assert original.exists() and objects.exists() and net.exists()


def test_changed_evidence_blocks_deletion(tmp_path, monkeypatch):
    campaign, batch, net, original, objects = fixture_batch(tmp_path, monkeypatch)
    net.write_text('corrupt')
    with pytest.raises(AssertionError, match='retained netlist'):
        monitor.cleanup_batch(campaign, 'native-codex', batch)
    assert original.exists() and objects.exists()


def test_only_source_copy_is_kept(tmp_path, monkeypatch):
    campaign, batch, net, original, objects = fixture_batch(tmp_path, monkeypatch)
    key = monitor.hashlib.sha256(('https://github.com/test/rtl|'+'a'*40).encode()).hexdigest()[:20]
    cache = campaign/'evaluation/native-codex.batch1/_source_cache'/key
    cache.mkdir(parents=True)
    (cache/'rtl.v').write_text('source')
    monitor.cleanup_batch(campaign, 'native-codex', batch)
    assert (cache/'rtl.v').read_text() == 'source'


def test_journal_accepts_snapshot_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'STATE', tmp_path)
    monitor.journal({'at':'snapshot-time','action':'hourly_check'})
    assert json.loads((tmp_path/'events.jsonl').read_text())['at'] == 'snapshot-time'
