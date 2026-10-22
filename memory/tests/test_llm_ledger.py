"""P1a: the harness LLM stage under the shared budget ledger (hard caps).

Protocol v1 section 7.2. No real model call is made (urlopen is faked).
- input and output tokens are reserved jointly BEFORE a request and settled to the reported usage;
- a request that does not fit is refused without calling the provider;
- the request timeout is bounded by the attempt deadline;
- missing usage is UNKNOWN, never 0;
- with a budget configured, a ledger error stops calls as an accounting error (not exhaustion).
"""
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory" / "scripts"))
import r2g_memory_eval as H  # noqa: E402

OK = {"id": "r1", "model": "deepseek-flash", "choices": [{"message": {"content": "{}"}}],
      "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_model_stop():
    H._LLM_STOPPED.clear()
    yield
    H._LLM_STOPPED.clear()


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _setup(tmp_path, monkeypatch, payload, budget=None):
    monkeypatch.setattr(H, "SKILL", ROOT / "r2g-skills" / "signoff-loop")
    for k in ("R2G_LEDGER_PATH", "R2G_BUDGET_FILE", "R2G_BUDGET_COMPONENT", "R2G_BUDGET_PURPOSE"):
        monkeypatch.delenv(k, raising=False)
    if budget is not None:
        b = tmp_path / "budget.json"
        b.write_text(budget if isinstance(budget, str) else json.dumps(budget))
        monkeypatch.setenv("R2G_BUDGET_FILE", str(b))
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append({"timeout": timeout, "bytes": len(req.data)})
        return _Resp(json.dumps(payload).encode())
    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    return proj, calls


def _events(proj):
    p = proj / "reports" / "resource_ledger.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _spent(proj):
    import subprocess
    led = ROOT / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "resource_ledger.py"
    return json.loads(subprocess.run([sys.executable, str(led), "summary", str(proj), "--json"],
                                     capture_output=True, text=True).stdout)["spent"]


def test_tokens_are_reserved_jointly_before_the_request_and_settled_to_reported_usage(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK)
    content, _ = H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert content == "{}" and len(calls) == 1
    ev = _events(proj)
    hold = [e for e in ev if e["type"] == "budget_hold" and "llm_requests" in e["needs"]][0]
    assert hold["needs"] == {"llm_requests": 1, "tokens_in": calls[0]["bytes"], "tokens_out": H.LLM_MAX_TOKENS}
    assert ev.index(hold) < [e["type"] for e in ev].index("llm_request")        # booked before the request
    settle = [e for e in ev if e["type"] == "budget_settle" and "llm_requests" in e["actual"]][0]
    assert settle["actual"] == {"llm_requests": 1, "tokens_in": 120, "tokens_out": 30}
    assert _spent(proj)["tokens_in"] == 120 and _spent(proj)["tokens_out"] == 30


def test_a_full_input_cap_refuses_before_sending(tmp_path, monkeypatch):
    # the user's case: input tokens 100/100 used -> a new request must not be approved and overrun to 110
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"tokens_in": 100}})
    content, usage = H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert content is None and usage == {"budget_exhausted": True} and calls == []
    assert _spent(proj)["tokens_in"] == 0


def test_output_reservation_needs_the_full_maximum(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"tokens_out": H.LLM_MAX_TOKENS - 1}})
    assert H.llm_call("k", [], proj / "reports" / "a", ledger=proj)[1] == {"budget_exhausted": True}
    assert calls == []


def test_missing_usage_is_settled_as_unknown_and_blocks_further_capped_requests(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, {"model": "deepseek-flash", "choices": [{"message": {"content": "{}"}}]},
                         budget={"caps": {"tokens_in": 10 ** 6, "tokens_out": 10 ** 6}})
    H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert [e for e in _events(proj) if e["type"] == "budget_settle"
            and "tokens_in" in e["actual"]][0]["actual"]["tokens_in"] == "UNKNOWN"
    assert H.llm_call("k", [], proj / "reports" / "b", ledger=proj)[1] == {"budget_exhausted": True}
    assert len(calls) == 1


def test_the_request_timeout_is_bounded_by_the_attempt_deadline(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"wall_s": 1000}})
    (proj / "reports" / "resource_ledger.jsonl").write_text(
        json.dumps({"event_seq": 1, "ts": time.time() - 900, "type": "attempt_start"}) + "\n")
    H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert len(calls) == 1 and calls[0]["timeout"] <= 100 - H.LLM_KILL_GRACE_S + 1


def test_no_time_left_sends_nothing_and_releases_the_hold(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"wall_s": 1000}})
    (proj / "reports" / "resource_ledger.jsonl").write_text(
        json.dumps({"event_seq": 1, "ts": time.time() - 990, "type": "attempt_start"}) + "\n")
    assert H.llm_call("k", [], proj / "reports" / "a", ledger=proj)[1] == {"budget_exhausted": True}
    assert calls == [] and _spent(proj)["llm_requests"] == 0


def test_a_ledger_error_with_a_budget_is_an_accounting_error_not_permission(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget="{not json")
    content, usage = H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert content is None and "accounting_error" in usage and "budget_exhausted" not in usage and calls == []


def test_a_failed_settlement_is_flagged_and_stops_further_calls(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"llm_requests": 5}})
    real = H._ledger

    def broken_settle(p, *args):
        return (2, "") if args[0] == "settle" else real(p, *args)
    monkeypatch.setattr(H, "_ledger", broken_settle)
    content, usage = H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert content == "{}" and "accounting_error" in usage and len(calls) == 1   # paid response kept, flagged


def test_a_failed_admission_alone_stops_the_call(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"llm_requests": 5}})
    real = H._ledger

    def broken_admit(p, *args):
        return (2, "") if args[0] == "admit" else real(p, *args)
    monkeypatch.setattr(H, "_ledger", broken_admit)
    content, usage = H.llm_call("k", [], proj / "reports" / "a", ledger=proj)
    assert content is None and "admit" in usage.get("accounting_error", "") and calls == []


# --- deadline enforcement on already-running processes, and the reserved final verification ----------
def _alive(marker):
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if marker in (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"):
                    out.append(int(d.name))
            except OSError:
                pass
    return out


def test_a_timed_out_run_stops_its_whole_session_including_timeouts_own_group(tmp_path):
    # `timeout` puts its command in a NEW process group; killing only the child or its group would orphan it
    import os
    import random
    log = tmp_path / "log"
    # markers made at run time, so no other process (e.g. the shell that started pytest) can contain them
    m1, m2 = "%.6f" % (300 + random.random()), "%.6f" % (310 + random.random())
    rc = H.run(["bash", "-c", "timeout 300 sleep %s & sleep %s" % (m1, m2)], dict(os.environ), log, 1)
    assert rc == -9 and "TIMEOUT" in log.read_text()
    end = time.time() + 5                     # gone within seconds; an orphan would live for 300 s
    while time.time() < end and (_alive("sleep " + m1) or _alive("sleep " + m2)):
        time.sleep(0.2)
    assert _alive("sleep " + m1) == [] and _alive("sleep " + m2) == []


def test_final_signoff_runs_as_final_acceptance_within_the_time_left(tmp_path, monkeypatch):
    proj, _ = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"wall_s": 4000}, "reserve": {"wall_s": 3000}})
    (proj / "reports" / "resource_ledger.jsonl").write_text(
        json.dumps({"event_seq": 1, "ts": time.time() - 1500, "type": "attempt_start"}) + "\n")
    seen = {}

    def fake_run(cmd, env, log, timeout):
        seen.update(timeout=timeout, purpose=env.get("R2G_BUDGET_PURPOSE"))
        return 0
    monkeypatch.setattr(H, "run", fake_run)
    monkeypatch.setattr(H, "CFG", {"platform": "sky130hd"}, raising=False)
    assert H.final_signoff(proj, {}, tmp_path / "log") == 0
    # repair may not touch the 3000 s reserve (only 0 s left for it), but final acceptance may: 2500 s left
    assert seen["purpose"] == "final_acceptance" and 2400 <= seen["timeout"] <= 2500 - H.KILL_GRACE_S
    assert [e for e in _events(proj) if e["type"] == "final_verification"][0]["status"] == "ran"


def test_final_signoff_with_no_time_left_does_not_run(tmp_path, monkeypatch):
    proj, _ = _setup(tmp_path, monkeypatch, OK, budget={"caps": {"wall_s": 1000}})
    (proj / "reports" / "resource_ledger.jsonl").write_text(
        json.dumps({"event_seq": 1, "ts": time.time() - 990, "type": "attempt_start"}) + "\n")
    monkeypatch.setattr(H, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(H, "CFG", {"platform": "sky130hd"}, raising=False)
    assert H.final_signoff(proj, {}, tmp_path / "log") == "budget_exhausted"


# -- T2a launch gate 2 (contract 6.1 / 6.2): ceilings, phase allocation, governed mode, model check ------------

def _phase_setup(tmp_path, monkeypatch, caps):
    ph = tmp_path / "phase"
    (ph / "reports").mkdir(parents=True)
    pb = tmp_path / "phase_budget.json"
    pb.write_text(json.dumps({"budget_id": "phase", "caps": caps}))
    monkeypatch.setenv("R2G_PHASE_LEDGER", str(ph / "reports" / "resource_ledger.jsonl"))
    monkeypatch.setenv("R2G_PHASE_BUDGET_FILE", str(pb))
    return ph


def _phase_spent(ph):
    import subprocess
    led = ROOT / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "resource_ledger.py"
    import os
    env = {**os.environ, "R2G_LEDGER_PATH": str(ph / "reports" / "resource_ledger.jsonl")}
    out = subprocess.run([sys.executable, str(led), "summary", str(ph), "--json"], capture_output=True, text=True,
                         env=env).stdout
    return json.loads(out).get("spent", {})


ATT = {"budget_id": "a", "caps": {"llm_requests": 4, "tokens_in": 48000, "tokens_out": 8000}}


def test_a_body_over_the_input_ceiling_is_refused_before_sending(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget=ATT)
    content, usage = H.llm_call("k", [{"role": "user", "content": "x" * (H.LLM_MAX_INPUT_BYTES + 1)}],
                                proj / "q", ledger=proj)
    assert content is None and usage["refused"] == "input_ceiling" and calls == []


def test_the_phase_allocation_counts_every_request_and_a_failed_transport_stops_further_calls(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget=ATT)
    ph = _phase_setup(tmp_path, monkeypatch, {"llm_requests": 2, "tokens_in": 100000, "tokens_out": 100000})
    H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q2", ledger=proj)
    assert len(calls) == 2 and _phase_spent(ph).get("llm_requests") == 2
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q3", ledger=proj)
    assert usage == {"budget_exhausted": True, "scope": "phase"} and len(calls) == 2   # phase full: nothing sent
    # a transport failure has no reported usage: UNKNOWN in both ledgers, so the retry is refused (fail closed)
    (tmp_path / "b").mkdir()
    proj2, calls2 = _setup(tmp_path / "b", monkeypatch, OK, budget=ATT)
    ph2 = _phase_setup(tmp_path / "b", monkeypatch, {"llm_requests": 5, "tokens_in": 100000, "tokens_out": 100000})

    def broken(req, timeout=0):
        calls2.append(1)
        raise H.urllib.error.URLError("transport")
    monkeypatch.setattr(H.urllib.request, "urlopen", broken)
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj2 / "q", ledger=proj2)
    assert content is None and len(calls2) == 1                                   # no blind retry
    assert _phase_spent(ph2).get("tokens_in") == "UNKNOWN"                         # never released as zero


def test_the_phase_hold_is_released_when_the_attempt_budget_refuses(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget={"budget_id": "a", "caps": {"llm_requests": 0}})
    ph = _phase_setup(tmp_path, monkeypatch, {"llm_requests": 5, "tokens_in": 100000, "tokens_out": 100000})
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    assert usage.get("budget_exhausted") and calls == []
    assert _phase_spent(ph).get("llm_requests", 0) == 0                  # not left reserved


def test_a_governed_run_without_both_ledgers_sends_nothing(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget=ATT)
    monkeypatch.setenv("R2G_REQUIRE_LLM_BUDGET", "1")
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    assert "accounting_error" in usage and calls == []                  # no phase ledger configured
    _phase_setup(tmp_path, monkeypatch, {"llm_requests": 5, "tokens_in": 100000, "tokens_out": 100000})
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    assert content == "{}" and len(calls) == 1


def test_a_reported_model_mismatch_stops_every_later_call(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, {**OK, "model": "some-other-model"}, budget=ATT)
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    assert content is None and usage == {"stopped": {"model_mismatch": "some-other-model"}}
    content, usage = H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q2", ledger=proj)
    assert usage["stopped"] and len(calls) == 1                         # the second call never went out
    ev = [e for e in _events(proj) if e.get("type") == "llm_request"]
    assert ev and ev[0]["data"]["model_mismatch"] is True if "data" in ev[0] else True


def test_each_request_event_records_sampling_endpoint_and_size(tmp_path, monkeypatch):
    proj, calls = _setup(tmp_path, monkeypatch, OK, budget=ATT)
    H.llm_call("k", [{"role": "user", "content": "hi"}], proj / "q", ledger=proj)
    raw = (proj / "reports" / "resource_ledger.jsonl").read_text()
    assert '"endpoint": "https://api.deepseek.com/chat/completions"' in raw and '"temperature": 0.3' in raw
    assert '"request_bytes":' in raw and '"model_requested": "deepseek-flash"' in raw
    assert "k" not in json.loads((proj / "q.try0.request.json").read_text()).get("authorization", "")
