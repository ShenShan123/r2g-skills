"""ORFS Agent-consumer contracts: no provider or EDA execution."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import t2_tehm_agent_memory as M
import t2a_runtime as T
from test_repair_turns import ep, H, OK, _Resp, _spent
from test_t2a_runtime import manifest


def test_observed_signature_is_failure_identity_not_chosen_action():
    from tehm.causal.mechanism import orfs_signature
    a = {"check": "drc", "violation_class": "m3.2", "platform": "sky130hd"}
    assert orfs_signature(a) == ("flow.signoff:drc:m3.2", "orfs:sky130hd")
    assert orfs_signature({**a, "strategy": "different"}) == orfs_signature(a)
    assert orfs_signature({}) == ("flow.signoff:UNKNOWN:UNKNOWN", None)
    assert orfs_signature({**a, "violation_class": "li.3"}) != orfs_signature(a)


@pytest.mark.parametrize("decision", ["NO_SKILL", "ABSTAIN", "INAPPLICABLE"])
def test_negative_route_never_dumps_history(decision):
    text, ids, claims = M.render_selected(None, SimpleNamespace(decision=decision),
                                         {}, [{"secret_unrelated_history": "do not inject"}])
    assert text == M.NO_MEMORY and not ids and not claims


def test_native_router_on_empty_store_no_write(tmp_path):
    from tehm_backend import TehmMemoryBackend
    project = tmp_path / "task"
    store = project / ".tehm-store"
    b = TehmMemoryBackend(db_path=store / "tehm.sqlite", artifact_root=store / "artifacts")
    b._open()
    b.close()
    path = tmp_path / "construction.json"
    M.save(path, {"shared_units_sha256": "fixture", "units": []})
    memory = M.AgentMemory(project, path)
    before = M.sha(memory.db)
    context, ids = memory.context("current", {"check": "drc", "violation_class": "m3.2", "platform": "sky130hd"})
    assert context == M.NO_MEMORY and ids == []
    assert memory.last_receipt["decision"]["decision"] == "NO_SKILL"
    assert M.sha(memory.db) == before


def test_missing_schema_is_technical_not_no_skill(tmp_path):
    import sqlite3
    project = tmp_path / "task"
    (project / ".tehm-store").mkdir(parents=True)
    sqlite3.connect(project / ".tehm-store/tehm.sqlite").close()
    p = tmp_path / "c.json"
    M.save(p, {"shared_units_sha256": "fixture", "units": []})
    memory = M.AgentMemory(project, p)
    with pytest.raises((ValueError, sqlite3.Error)):
        memory.context("current", {"check": "drc"})


def test_actual_request_binding_rejects_changed_context(tmp_path):
    messages = [{"role": "user", "content": "selected knowledge only"}]
    receipt = {"request_prefix": "repair-turn-1", "messages_sha256": M.digest(messages)}
    assert not M.verify_request(tmp_path, receipt)["serialized_request_present"]
    p = tmp_path / "repair-turn-1.try1.request.json"
    M.save(p, {"messages": messages})
    assert M.verify_request(tmp_path, receipt)["serialized_request_present"]
    M.save(p, {"messages": [{"role": "user", "content": "different"}]})
    with pytest.raises(ValueError, match="differs"):
        M.verify_request(tmp_path, receipt)


@pytest.mark.parametrize("context", [M.NO_MEMORY, "Memory: applicable validated mechanism knowledge.\nfixture"])
def test_tm0_route_to_real_request_serializer_and_turn_ledger(ep, monkeypatch, context):
    """Positive fixture tests consumption wiring, not granting real knowledge authority."""
    project, calls, _ = ep
    monkeypatch.setattr(H, "_task_failure", lambda p: ("drc", "m3.2", 18))
    monkeypatch.setattr(H, "observation", lambda p: {"drc": 18})
    sent = []
    def transport(req, timeout=0):
        sent.append(json.loads(req.data))
        return _Resp(json.dumps({**OK, "choices": [{"message": {"content": "{}"}}]}).encode())
    monkeypatch.setattr(H.urllib.request, "urlopen", transport)
    class Memory:
        arm = "TM0"
        def context(self, *args, **kwargs):
            return context, ["selected-unit"] if context != M.NO_MEMORY else []
        def request_receipt(self, messages, turn):
            return {"messages_sha256": M.digest(messages), "request_prefix": f"repair-turn-{turn}", "turn": turn}
    env = {**manifest(project), "R2G_TEHM_AGENT_CONSUMER": M.VERSION}
    result, _ = T.strict_turns(H, project, "TM0", env, "unused",
        SimpleNamespace(from_project=lambda *a: {"check": "drc"}), Memory())
    assert len(result) == len(sent) == 3
    assert _spent(project, "repair_attempts") == 3 and _spent(project, "llm_requests") == 3
    assert all(context in body["messages"][-1]["content"] for body in sent)
    assert all(row["memory_consumption"]["serialized_request_present"] for row in result)
    events = [json.loads(l) for l in (project / "reports/resource_ledger.jsonl").read_text().splitlines()]
    assert len([r for r in events if r["type"] == "tehm_agent_consumption"]) == 3


def test_tm0_context_without_registration_is_rejected(ep):
    project, _, _ = ep
    with pytest.raises(ValueError, match="mismatch"):
        T.strict_turns(H, project, "TM0", manifest(project), "unused", None,
                      SimpleNamespace(arm="TM0"))


@pytest.mark.parametrize("level", ["L1_EXECUTED_INTERVENTION", "L2_CONTROLLED_INTERVENTION"])
def test_renderer_checks_level_and_selects_only_routed_witness(monkeypatch, level):
    """Synthetic renderer fixture, NOT an authority/causal-validation receipt."""
    import sqlite3
    from tehm.knowledge.claims import MechanismKnowledge
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tehm_mechanism_knowledge(knowledge_id,version,causal_path_ids_json);
        CREATE TABLE tehm_causal_paths(path_id,source_transitions_json);
        CREATE TABLE tehm_transitions(transition_id,source_state_id,target_state_id);
        CREATE TABLE tehm_episode_steps(transition_id,episode_id);
        CREATE TABLE tehm_views(owner_id,view_type);
        INSERT INTO tehm_mechanism_knowledge VALUES('fixture',1,'["p"]');
        INSERT INTO tehm_mechanism_knowledge VALUES('unrelated',1,'["other"]');
        INSERT INTO tehm_causal_paths VALUES('p','["t"]');
        INSERT INTO tehm_transitions VALUES('t','s0','s1');
    """)
    claim = MechanismKnowledge(
        knowledge_id="fixture", version=1, mechanism_family="flow.signoff:drc:m3.2",
        compatibility_profile="orfs:sky130hd", antecedent={}, intervention={},
        mediated_effects=(), expected_outcome={}, positive_applicability=({"check": "drc"},),
        negative_applicability=(), preserved_obligations=("fresh target verification",),
        known_failure_modes=(), causal_path_ids=("p",), evidence_level=level,
        support_lineages=("fixture-a", "fixture-b"), status="validated")
    monkeypatch.setattr(M, "selected_claims", lambda c, d, s: [claim])
    decision = SimpleNamespace(decision="CONSIDER", selected_path_ids=("p",))
    sit = {"check": "drc", "mechanism_family": claim.mechanism_family,
           "compatibility_profile": claim.compatibility_profile}
    units = [{"transition_id": "t", "unit_id": "eligible", "source_handoff_id": "other-ip",
              "raw_row": {"situation": sit}, "shared_label": {"edits": {"CORE_UTILIZATION": "12"}},
              "evidence_level": level},
             {"transition_id": "unselected", "do_not_render": "UNRELATED_CASE_SECRET"}]
    if level.startswith("L1"):
        with pytest.raises(ValueError, match="below"):
            M.render_selected(conn, decision, sit, units)
    else:
        context, ids, claims = M.render_selected(conn, decision, sit, units)
        assert ids == ["eligible"] and claims == ["fixture@1"]
        assert "UNRELATED_CASE_SECRET" not in context
        assert "fresh target verification" in context
        monkeypatch.setattr(M, "CONTEXT_BYTES", 1)
        with pytest.raises(ValueError, match="byte limit"):
            M.render_selected(conn, decision, sit, units)


def test_frozen_delta_preserves_existing_nb_hooks_and_refuses_drift():
    import t2_tehm_agent_rerun as R
    base = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2-nb-memory-followup-20261020-r6/folds/C00876/code")
    for rel in ("memory/scripts/t2a_batch.py", "memory/scripts/t2a_runtime.py",
                "memory/tehm/causal/mechanism.py"):
        original = (base / rel).read_text()
        new = R.integrate(rel, original)
        compile(new, rel, "exec")
        assert "tehm_agent" in new or "orfs_signature" in new
        if rel.endswith("t2a_batch.py"):
            assert "resolve_area_reference(man)" in new
            assert "reg.get('paper_data',split=='TEST')" in new
        if rel.endswith("t2a_runtime.py"):
            assert "resolve_area_reference(manifest)" in new
    with pytest.raises(ValueError, match="exactly once"):
        R.integrate("memory/scripts/t2a_batch.py", "unrelated runtime")


def test_renderer_uses_resolved_active_authority_and_rejects_state_drift(monkeypatch):
    import tehm.state as state
    import tehm.retrieval.memory_router as router
    selected = SimpleNamespace(object_id="selected")
    unrelated = SimpleNamespace(object_id="other")
    monkeypatch.setattr(state, "resolve_current_state",
                        lambda *a, **kw: SimpleNamespace(resolution_id="bound"))
    calls = []
    def eligible(*args, **kwargs):
        calls.append(kwargs)
        return ([{"claim": selected, "path_ids": ("p",)},
                 {"claim": unrelated, "path_ids": ("elsewhere",)}], [], [], [])
    monkeypatch.setattr(router, "_knowledge_for_state", eligible)
    d = SimpleNamespace(resolved_state_id="bound", selected_path_ids=("p",))
    assert M.selected_claims(None, d, {"check": "drc"}) == [selected]
    assert calls[0]["scope"]["target_scope"] == "drc"
    with pytest.raises(ValueError, match="changed"):
        M.selected_claims(None, SimpleNamespace(resolved_state_id="stale", selected_path_ids=("p",)),
                          {"check": "drc"})


def test_finished_episode_cannot_pass_without_route_receipt(tmp_path):
    import t2_tehm_agent_rerun as R
    (tmp_path / "reports").mkdir()
    p = tmp_path / "reports/resource_ledger.jsonl"
    p.write_text("")
    assert not R.consumption_integrity(tmp_path / "episode.json")
    p.write_text(json.dumps({"type": "tehm_agent_route"}) + "\n")
    assert R.consumption_integrity(tmp_path / "episode.json")
    M.save(tmp_path / "repair-turn-1.try1.request.json", {"messages": []})
    assert not R.consumption_integrity(tmp_path / "episode.json")
