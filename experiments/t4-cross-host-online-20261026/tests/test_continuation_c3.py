import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters'))


def test_c3_roster_is_only_the_never_started_scout_tehm(tmp_path):
    from continuation_c3 import ROSTER, unstarted
    assert ROSTER == [('rtlscout', 'tehm', 'fifo_sync4')]
    unstarted(*ROSTER[0], root=tmp_path)
    for row in [('rtlscout', 'native', 'fifo_sync4'), ('rtlfixer', 'tehm', '2012_q2b'),
                ('mini-swe-agent', 'tehm', 'astropy__astropy-12907')]:
        with pytest.raises(ValueError): unstarted(*row, root=tmp_path)
    (tmp_path / 'hosts/rtlscout/tehm/episodes/fifo_sync4__any_revision').mkdir(parents=True)
    with pytest.raises(ValueError): unstarted(*ROSTER[0], root=tmp_path)


def test_c3_exact_unknown_grant_keeps_usage_unknown_and_blocks_future_loss(tmp_path):
    from bridge import digest
    from phase_gate import inspect, require_clear
    ledger = tmp_path / 'hosts/rtlscout/native/episodes/fifo_sync4__c2/provider/requests.jsonl'
    ledger.parent.mkdir(parents=True)
    ended = {'event': 'request_ended', 'request_local_id': 'fourth', 'usage': 'UNKNOWN'}
    rows = [{'event': 'request_started', 'request_local_id': 'fourth'}, ended]
    ledger.write_text(''.join(json.dumps(e)+'\n' for e in rows))
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'continuation-authorization-c3.json').write_text(json.dumps({
        'authorized': True, 'no_episode_reruns': True,
        'acknowledged_unknowns': [{'ledger': str(ledger.relative_to(tmp_path)),
                                 'request_local_id': 'fourth', 'end_event_sha256': digest(ended)}]}))
    assert require_clear(tmp_path)['new_paid_admissions_allowed']
    assert not inspect(tmp_path)['usage_complete']
    assert inspect(tmp_path)['known_tokens'] == 0 and 'UNKNOWN' in ledger.read_text()
    with ledger.open('a') as f:
        f.write(json.dumps({'event': 'request_started', 'request_local_id': 'fifth'})+'\n')
        f.write(json.dumps({'event': 'request_ended', 'request_local_id': 'fifth', 'usage': 'UNKNOWN'})+'\n')
    with pytest.raises(RuntimeError): require_clear(tmp_path)


def test_c3_runtime_worker_exposes_the_registered_flag_without_requests():
    result = subprocess.run([sys.executable, str(ROOT/'adapters/run_scout.py'), '--help'],
                            capture_output=True, text=True, check=True)
    assert '--continuation-c3' in result.stdout


def test_c3_refuses_unregistered_runtime_or_core_changes():
    from continuation_c3 import verify_base_delta
    base = {'adapters/run_scout.py': 'old', 'code/memory/core.py': 'core'}
    verify_base_delta(base, {'adapters/run_scout.py': 'new', 'code/memory/core.py': 'core'})
    with pytest.raises(ValueError):
        verify_base_delta(base, {'adapters/run_scout.py': 'new', 'code/memory/core.py': 'changed'})
    with pytest.raises(ValueError):
        verify_base_delta(base, {'adapters/run_scout.py': 'new'})
