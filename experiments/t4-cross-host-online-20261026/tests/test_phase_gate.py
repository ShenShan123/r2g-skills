import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from phase_gate import inspect, require_clear
from bridge import digest


@pytest.mark.parametrize("ending", ["known", "unknown", "open"])
def test_unknown_and_open_never_become_zero_or_allow_new_episode(tmp_path, ending):
    path = tmp_path / "hosts/rtlscout/native/episodes/t/provider/requests.jsonl"
    path.parent.mkdir(parents=True)
    events = [{"event": "request_started", "request_local_id": "r1"}]
    if ending != "open":
        events.append({"event": "request_ended", "request_local_id": "r1",
                       "usage": {"total_tokens": 12} if ending == "known" else "UNKNOWN"})
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    before = path.read_bytes()
    status = inspect(tmp_path)
    assert status["online_requests"] == 1
    assert status["usage_complete"] == (ending == "known")
    if ending == "known":
        assert require_clear(tmp_path)["known_tokens"] == 12
    else:
        with pytest.raises(RuntimeError): require_clear(tmp_path)
        assert status["unknown_requests"] or status["open_requests"]
    assert path.read_bytes() == before


def test_skip_authorization_is_exact_and_never_turns_usage_into_known(tmp_path):
    ledger = "hosts/rtlscout/native/episodes/t/provider/requests.jsonl"
    path = tmp_path / ledger
    path.parent.mkdir(parents=True)
    end = {"event": "request_ended", "request_local_id": "old", "usage": "UNKNOWN"}
    rows = [{"event": "request_started", "request_local_id": "old"}, end]
    path.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
    auth = tmp_path / "inputs/continuation-authorization-20261026.json"
    auth.parent.mkdir()
    auth.write_text(json.dumps({"authorized": True, "no_episode_reruns": True,
        "acknowledged_unknowns": [{"ledger": ledger, "request_local_id": "old", "end_event_sha256": digest(end)}]}))
    assert require_clear(tmp_path)["new_paid_admissions_allowed"]
    assert inspect(tmp_path)["known_tokens"] == 0 and not inspect(tmp_path)["usage_complete"]
    rows += [{"event": "request_started", "request_local_id": "new"},
             {"event": "request_ended", "request_local_id": "new", "usage": "UNKNOWN"}]
    path.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
    with pytest.raises(RuntimeError): require_clear(tmp_path)


def test_never_started_rule_rejects_any_existing_revision(tmp_path):
    from continuation import unstarted
    unstarted("rtlscout", "native", "fifo_sync4", root=tmp_path)
    (tmp_path / "hosts/rtlscout/native/episodes/fifo_sync4__old").mkdir(parents=True)
    with pytest.raises(ValueError): unstarted("rtlscout", "native", "fifo_sync4", root=tmp_path)
    with pytest.raises(ValueError): unstarted("rtlscout", "tehm", "simple_adder", root=tmp_path)
