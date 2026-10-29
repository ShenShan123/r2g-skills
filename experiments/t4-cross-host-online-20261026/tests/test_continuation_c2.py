import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from continuation_c2 import ROSTER, unstarted, verify_files
from bridge import digest
from phase_gate import inspect, require_clear


def test_c2_roster_is_exactly_five_and_excludes_every_started_or_software_task(tmp_path):
    assert ROSTER == [("rtlfixer", "tehm", "2012_q2b"), ("rtlfixer", "native", "2012_q2fsm"),
                      ("rtlfixer", "tehm", "2012_q2fsm"), ("rtlscout", "native", "fifo_sync4"),
                      ("rtlscout", "tehm", "fifo_sync4")]
    for row in ROSTER:
        unstarted(*row, root=tmp_path)
    for row in [("rtlfixer", "native", "2012_q2b"), ("rtlscout", "native", "simple_adder"),
                ("rtlscout", "tehm", "simple_adder"), ("mini-swe-agent", "tehm", "astropy__astropy-12907")]:
        with pytest.raises(ValueError): unstarted(*row, root=tmp_path)
    (tmp_path / "hosts/rtlfixer/tehm/episodes/2012_q2b__old").mkdir(parents=True)
    with pytest.raises(ValueError): unstarted("rtlfixer", "tehm", "2012_q2b", root=tmp_path)


def test_c2_authorization_preserves_unknown_and_does_not_waive_the_next_one(tmp_path):
    relative = "hosts/rtlfixer/native/episodes/old/provider/requests.jsonl"
    ledger = tmp_path / relative
    ledger.parent.mkdir(parents=True)
    end = {"event": "request_ended", "request_local_id": "third", "usage": "UNKNOWN"}
    rows = [{"event": "request_started", "request_local_id": "third"}, end]
    ledger.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    parent = inputs / "continuation-authorization-20261026.json"
    parent.write_text('{"authorized": true, "no_episode_reruns": true}')
    auth = inputs / "continuation-authorization-c2.json"
    auth.write_text(json.dumps({"authorized": True, "no_episode_reruns": True,
        "prior_authorization": {"path": str(parent.relative_to(tmp_path)), "sha256": hashlib.sha256(parent.read_bytes()).hexdigest()},
        "acknowledged_unknowns": [{"ledger": relative, "request_local_id": "third", "end_event_sha256": digest(end)}]}))
    assert require_clear(tmp_path)["new_paid_admissions_allowed"]
    assert not inspect(tmp_path)["usage_complete"] and inspect(tmp_path)["known_tokens"] == 0
    rows += [{"event": "request_started", "request_local_id": "fourth"},
             {"event": "request_ended", "request_local_id": "fourth", "usage": "UNKNOWN"}]
    ledger.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
    with pytest.raises(RuntimeError): require_clear(tmp_path)
    parent.write_text('{}')
    with pytest.raises(ValueError): inspect(tmp_path)


def test_c2_hash_check_rejects_drift_and_escape(tmp_path):
    p = tmp_path / "worker.py"
    p.write_text("original")
    hashes = {"worker.py": hashlib.sha256(p.read_bytes()).hexdigest()}
    verify_files(tmp_path, hashes)
    p.write_text("changed")
    with pytest.raises(ValueError): verify_files(tmp_path, hashes)
    with pytest.raises(ValueError): verify_files(tmp_path, {"../outside": "x"})


def test_c2_controller_never_calls_an_executed_episode_a_setup_failure(tmp_path):
    from dispatch_c2 import terminal_status
    provider = tmp_path / "provider"
    provider.mkdir()
    (provider / "requests.jsonl").write_text('{"event":"request_started","request_local_id":"real"}\n')
    assert terminal_status(tmp_path, 1) == "EXECUTED_TECHNICAL_STOP_NO_RECEIPT"
    (tmp_path / "terminal-accounting.json").write_text('{"status":"ACCOUNTING_FAILURE"}')
    assert terminal_status(tmp_path, 1) == "ACCOUNTING_AND_RECORDER_FAILURE"
    (tmp_path / "episode.json").write_text('{"status":"ACCOUNTING_FAILURE"}')
    assert terminal_status(tmp_path, 2) == "ACCOUNTING_FAILURE"
    (tmp_path / "episode.json").write_text('{"status":"ACCEPTED"}')
    assert terminal_status(tmp_path, 0) == "ACCEPTED"
    assert terminal_status(tmp_path, 1) == "TECHNICAL_STOP_WITH_RECEIPT"


def test_c2_actual_worker_clis_accept_the_registered_flag_without_calls():
    script = "run_fixer.py" if sys.version_info[:2] == (3, 10) else "run_scout.py"
    result = subprocess.run([sys.executable, str(ROOT / "adapters" / script), "--help"],
                            capture_output=True, text=True, check=True)
    assert "--continuation-c2" in result.stdout
