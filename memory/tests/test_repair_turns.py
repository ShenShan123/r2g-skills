"""T2a A2 acceptance tests (contract 5.2): the shared three-turn repair allowance. No real model call (faked transport).

A repair turn is reserved atomically before generation and shared by the whole (task, arm, repeat) episode; transport
retries of an identical request stay in the turn but are charged as requests; any returned candidate spends the turn;
helper (auxiliary) responses can never be applied as candidates.
"""
import concurrent.futures as cf
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory" / "scripts"))
import r2g_memory_eval as H  # noqa: E402

OK = {"id": "r", "model": "deepseek-flash", "choices": [{"message": {"content": json.dumps(
    {"set": {"CORE_UTILIZATION": "40"}, "unset": [], "rerun_from": "floorplan", "rationale": "x"})}}],
      "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}}
BAD = {**OK, "choices": [{"message": {"content": "not json at all"}}]}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def ep(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "SKILL", ROOT / "r2g-skills" / "signoff-loop")
    for k in ("R2G_LEDGER_PATH", "R2G_BUDGET_COMPONENT", "R2G_BUDGET_PURPOSE", "R2G_PHASE_LEDGER", "R2G_PHASE_BUDGET_FILE"):
        monkeypatch.delenv(k, raising=False)
    b = tmp_path / "budget.json"
    b.write_text(json.dumps({"budget_id": "ep", "caps": {"repair_attempts": H.REPAIR_TURNS, "llm_requests": 20,
                                                         "tokens_in": 10 ** 6, "tokens_out": 10 ** 6}}))
    monkeypatch.setenv("R2G_BUDGET_FILE", str(b))
    ph = tmp_path / "phase"
    (ph / "reports").mkdir(parents=True)
    pb = tmp_path / "phase.json"
    pb.write_text(json.dumps({"budget_id": "ph", "caps": {"llm_requests": 100, "tokens_in": 10 ** 7, "tokens_out": 10 ** 7}}))
    monkeypatch.setenv("R2G_PHASE_LEDGER", str(ph / "reports" / "resource_ledger.jsonl"))
    monkeypatch.setenv("R2G_PHASE_BUDGET_FILE", str(pb))
    monkeypatch.setenv("R2G_REQUIRE_LLM_BUDGET", "1")
    H._LLM_STOPPED.clear()
    H._APPLIED_TURNS.clear()
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text("export CORE_UTILIZATION = 60\n")
    calls = []

    def respond(payload):
        def fake(req, timeout=0):
            calls.append(1)
            return _Resp(json.dumps(payload).encode())
        monkeypatch.setattr(H.urllib.request, "urlopen", fake)
    respond(OK)
    yield proj, calls, respond
    H._APPLIED_TURNS.clear()


def _spent(proj, res):
    import subprocess
    led = ROOT / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "resource_ledger.py"
    out = subprocess.run([sys.executable, str(led), "summary", str(proj), "--json"], capture_output=True, text=True).stdout
    return json.loads(out)["spent"].get(res)


def _turn(proj, role="repair"):
    return H.llm_call("k", [{"role": "user", "content": "x"}], proj / "reports" / "q", ledger=proj, role=role)


def test_one_memory_plus_two_fallback_candidates_exhaust_the_allowance_and_a_fourth_is_refused(ep):
    proj, calls, _ = ep
    for i in range(3):                                  # e.g. an ExpeL/AWM adaptation call, then two fallback turns
        content, usage = _turn(proj)
        assert content and usage["repair_turn_id"]
    content, usage = _turn(proj)
    assert content is None and usage == {"repair_allowance_exhausted": True} and len(calls) == 3   # never sent
    assert _spent(proj, "repair_attempts") == 3


def test_concurrent_callers_cannot_exceed_the_allowance(ep):
    proj, calls, _ = ep
    _turn(proj)
    _turn(proj)
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda _: _turn(proj), range(4)))
    granted = [r for r in results if r[0] is not None]
    assert len(granted) == 1 and len(calls) == 3
    assert all(r[1] == {"repair_allowance_exhausted": True} for r in results if r[0] is None)


def test_a_helper_response_cannot_be_applied_as_a_candidate(ep):
    proj, calls, _ = ep
    content, usage = _turn(proj, role="auxiliary")
    assert content and "repair_turn_id" not in usage
    assert H.apply_llm(proj, content, turn=usage.get("repair_turn_id")) == (None, "not_a_repair_turn")
    assert _spent(proj, "repair_attempts") == 0 and _spent(proj, "llm_requests") == 1


def test_a_malformed_proposal_spends_its_turn_and_a_turn_yields_one_candidate(ep):
    proj, calls, respond = ep
    respond(BAD)
    content, usage = _turn(proj)
    assert H.apply_llm(proj, content, turn=usage["repair_turn_id"])[0] is None     # malformed: rejected
    assert _spent(proj, "repair_attempts") == 1                                     # but the turn is spent
    respond(OK)
    content, usage = _turn(proj)
    assert H.apply_llm(proj, content, turn=usage["repair_turn_id"])[0] == "floorplan"
    assert H.apply_llm(proj, content, turn=usage["repair_turn_id"]) == (None, "turn_already_used")


def test_transport_retries_are_charged_but_grant_no_extra_candidate(ep, monkeypatch):
    proj, calls, _ = ep
    n = {"i": 0}

    def flaky(req, timeout=0):                         # a 503 with usage reported, then success: one turn, two requests
        calls.append(1)
        n["i"] += 1
        if n["i"] == 1:
            raise H.urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(json.dumps(
                {"usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model": "deepseek-flash"}).encode()))
        return _Resp(json.dumps(OK).encode())
    monkeypatch.setattr(H.urllib.request, "urlopen", flaky)
    content, usage = H.llm_call('k', [{'role': 'user', 'content': 'repair'}], proj/'retry',
                              ledger=proj, backoff=(0,))
    assert content and len(calls) == 2
    assert _spent(proj, "llm_requests") == 2 and _spent(proj, "repair_attempts") == 1


def test_a_reranker_assisted_deterministic_success_has_zero_repair_turns_but_online_requests(ep):
    proj, calls, _ = ep
    content, usage = _turn(proj, role="auxiliary")      # e.g. a retrieval reranker; a frozen rule then executes
    assert content is not None
    assert _spent(proj, "repair_attempts") == 0 and _spent(proj, "llm_requests") == 1


def test_an_unknown_role_is_refused_before_sending(ep):
    proj, calls, _ = ep
    assert _turn(proj, role="planner")[1]["refused"].startswith("unknown_role") and calls == []


@pytest.mark.parametrize('content', ['{}', '{"set": {"CORE_UTILIZATION": "60"}}', '{"unset": ["DIE_AREA"]}'])
def test_empty_or_no_change_plan_spends_turn_without_a_rerun(ep, content):
    proj, calls, respond = ep
    respond({**OK, 'choices': [{'message': {'content': content}}]})
    response, usage = _turn(proj)
    before = (proj / 'constraints/config.mk').read_bytes()
    assert H.apply_llm(proj, response, turn=usage['repair_turn_id']) == (None, 'no_change')
    assert (proj / 'constraints/config.mk').read_bytes() == before
    assert _spent(proj, 'repair_attempts') == 1


@pytest.mark.parametrize('content', ['[]', 'null', '{"unset": [null]}'])
def test_malformed_shape_does_not_crash(ep, content):
    proj, _, _ = ep
    _, usage = _turn(proj)
    assert H.apply_llm(proj, content, turn=usage['repair_turn_id']) == (None, 'malformed_proposal')


@pytest.mark.parametrize('content', ['not json', '{}'])
def test_harness_continues_remaining_turns_without_launching_flows(ep, monkeypatch, content):
    from types import SimpleNamespace
    proj, calls, respond = ep
    respond({**OK, 'choices': [{'message': {'content': content}}]})
    monkeypatch.setattr(H, '_task_failure', lambda p: ('route', 'congestion', 1))
    monkeypatch.setattr(H, 'observation', lambda p: {})
    def no_flow(*args, **kwargs):
        pytest.fail('Rejected/no-change proposal must not launch a tool')
    monkeypatch.setattr(H, 'run', no_flow)
    rows, last = H.llm_assist(proj, 'NB', {}, 'k',
                             {'calls': 0, 'tokens': 0, 'max_calls': 10, 'max_tokens': 100000},
                             SimpleNamespace(from_project=lambda *args: {}))
    assert len(rows) == len(calls) == 3 and last is None
    assert all(row['flow_launched'] is False for row in rows)
    assert _spent(proj, 'repair_attempts') == 3
