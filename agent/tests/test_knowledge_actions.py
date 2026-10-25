"""Synthetic isolated stores exercise consultation, never production promotion."""
import copy
import json

import pytest

from r2g_agent import knowledge
from tehm import db
from tehm.ids import rule_id as make_rule_id
from tehm.lifecycle.rule_status import set_status


PROFILE = "synthetic-test:yosys-pinned:nangate45"


@pytest.fixture
def memory(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TEHM_DB", str(path))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    conn = db.connect(path)
    db.ensure_schema(conn)
    project = tmp_path / "project"
    project.mkdir()
    context = {"project_dir": str(project), "design_id": "fixture-design", "platform": "nangate45",
               "check": "drc", "target_scope": "project:fixture:drc", "source_snapshot_id": "fixture-input-sha256",
               "compatibility_profile": PROFILE, "cfg": {"CORE_UTILIZATION": "40"},
               "reports": {"drc": {"status": "violations", "total_violations": 3}},
               "situation": {"platform": "nangate45", "violation_class": "metal"}}
    yield conn, context
    conn.close()


def _rule(conn, *, lifecycle="promoted", scope="project:fixture:drc", profile=PROFILE, knob="CORE_UTILIZATION"):
    before = {"type": "SYNTHETIC_TEST_FIX", "target_check": "drc", "knob": knob,
              "compatibility_profile": profile}
    after = {"rewrite.value": "30", "execution.rerun_from": "floorplan", "execution.recheck": "drc",
             "verification.verdict": "PASS"}  # synthetic protocol fixture, never real EDA evidence
    conditions = ["situation.platform==nangate45", "situation.violation_class==metal"]
    obligations = ["TARGET_FAILURE_REMOVED"]
    rid = make_rule_id(domain="flow.signoff", before_pattern=before, after_pattern=after,
                       hard_preconditions=conditions, obligations=obligations)
    row = {"rule_id": rid, "domain": "flow.signoff", "before_pattern_json": json.dumps(before),
           "after_pattern_json": json.dumps(after), "hard_preconditions_json": json.dumps(conditions),
           "context_profile_json": json.dumps({"compatibility_profile": profile}),
           "obligations_json": json.dumps(obligations), "validity_status": "VALIDATED",
           "validity_profile_json": "{}", "confidence_json": "{}", "utility_json": "{}",
           "risk_profile_json": "[]", "predicate_schema_version": "fixture", "role_schema_version": "fixture",
           "crystallizer_version": "synthetic-fixture-only", "merge_trace_digest": "fixture",
           "created_at": "2026-01-01", "updated_at": "2026-01-01"}
    conn.execute("INSERT INTO tehm_rules (%s) VALUES (%s)" % (",".join(row), ",".join("?" for _ in row)), tuple(row.values()))
    conn.execute("INSERT INTO tehm_rule_sources VALUES (?,?,?,?,?)",
                 (rid, "synthetic-fixture-episode", "{}", '{"synthetic":true}', "fixture"))
    set_status(conn, rule_id=rid, target_scope=scope, status=lifecycle, provenance={"synthetic_fixture": True})
    conn.commit()
    return rid


def test_same_backend_proposal_carries_scope_evidence_and_version_without_writes(memory):
    conn, context = memory
    rid = _rule(conn)
    before = list(conn.iterdump())
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "CONSIDER", result
    assert result["execution_authorized"] is False
    item = result["candidates"][0]
    assert item["memory_id"] == rid and item["lifecycle"]["status_version"] == 1
    assert item["target_scope"] == context["target_scope"]
    assert item["source_evidence"]["episodes"] == ["synthetic-fixture-episode"]
    assert item["conditions"]["compatibility_profile"] == PROFILE
    assert item["action"]["payload"]["config_edits"] == {"CORE_UTILIZATION": "30"}
    assert item["proposal"]["binding"]["status"] == "BOUND"
    assert result["binding"]["source_snapshot_id"] == context["source_snapshot_id"]
    assert result["decision_digest"] == knowledge.lookup_actions(context)["decision_digest"]
    assert list(conn.iterdump()) == before
    assert conn.execute("SELECT count(*) FROM tehm_activations").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["shadow", "candidate", "demoted", "quarantined", "retired"])
def test_unpromoted_rules_never_become_chat_actions(memory, status):
    conn, context = memory
    _rule(conn, lifecycle=status)
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "NO_SKILL" and result["candidates"] == []


def test_promoted_other_scope_cannot_leak_into_current_scope(memory):
    conn, context = memory
    _rule(conn, scope="project:other:drc")
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "NO_SKILL"
    assert result["candidates"][0]["action"] is None
    assert "not_promoted_in_target_scope" in result["candidates"][0]["reasons"]


@pytest.mark.parametrize("value,decision,applicability", [
    (None, "ABSTAIN", "UNRESOLVED"), ("UNKNOWN", "ABSTAIN", "UNRESOLVED"),
    ("antenna", "NO_SKILL", "INAPPLICABLE")])
def test_missing_evidence_is_distinct_from_a_concrete_mismatch(memory, value, decision, applicability):
    conn, context = memory
    _rule(conn)
    context["situation"]["violation_class"] = value
    result = knowledge.lookup_actions(context)
    assert result["decision"] == decision
    assert result["candidates"][0]["applicability"] == applicability
    assert result["candidates"][0]["action"] is None
    assert context["situation"]["violation_class"] == value


def test_missing_or_different_tool_profile_never_returns_an_action(memory):
    conn, context = memory
    _rule(conn, profile=None)
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "ABSTAIN"
    assert "rule_compatibility_profile_unresolved" in result["candidates"][0]["reasons"]
    conn.execute("DELETE FROM tehm_rules")
    conn.execute("DELETE FROM tehm_rule_status")
    conn.execute("DELETE FROM tehm_rule_sources")
    _rule(conn, profile="different-tool-version")
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "NO_SKILL"
    assert "compatibility_profile_mismatch" in result["candidates"][0]["reasons"]


def test_live_wal_demotion_invalidates_a_previous_decision(memory):
    conn, context = memory
    rid = _rule(conn)
    assert knowledge.lookup_actions(context)["decision"] == "CONSIDER"
    # Keep writer open: this update lives in WAL, which immutable=1 would miss.
    set_status(conn, rule_id=rid, target_scope=context["target_scope"], status="demoted",
               provenance={"synthetic_fixture": True, "reason": "counterexample"})
    conn.commit()
    context["expected_rule_versions"] = {rid: 1}
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "ABSTAIN", result
    assert result["stale_rule_versions"] == [rid]
    assert result["candidates"] == []


def test_scope_specific_proposal_ignores_another_scopes_earlier_status(memory):
    conn, context = memory
    rid = _rule(conn, lifecycle="demoted", scope="first-other-scope")
    set_status(conn, rule_id=rid, target_scope=context["target_scope"], status="promoted",
               provenance={"synthetic_fixture": True})
    conn.commit()
    assert knowledge.lookup_actions(context)["decision"] == "CONSIDER"


def test_changed_source_snapshot_changes_decision_binding(memory):
    conn, context = memory
    _rule(conn)
    first = knowledge.lookup_actions(context)
    changed = {**context, "source_snapshot_id": "second-fixture-input-sha256"}
    second = knowledge.lookup_actions(changed)
    assert first["decision_digest"] != second["decision_digest"]


@pytest.mark.parametrize("field", ["target_scope", "source_snapshot_id", "compatibility_profile", "design_id"])
def test_incomplete_scope_does_not_guess_authority(memory, field):
    _, context = memory
    context.pop(field)
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "ABSTAIN" and result["candidates"] == []
    assert "missing_context:" + field in result["reasons"]


def test_missing_store_is_not_initialized(memory, monkeypatch, tmp_path):
    _, context = memory
    missing = tmp_path / "absent" / "tehm.sqlite"
    monkeypatch.setenv("TEHM_DB", str(missing))
    result = knowledge.lookup_actions(context)
    assert result["decision"] == "ABSTAIN" and not missing.parent.exists()


@pytest.mark.parametrize("backend", ["none", "legacy"])
def test_keyword_matches_remain_documentation_only(memory, monkeypatch, backend):
    _, context = memory
    monkeypatch.setenv("R2G_MEMORY_BACKEND", backend)
    result = knowledge.lookup("STA-0171", context=context)
    assert result["reference_kind"] == "documentation_only" and result["total"] > 0
    assert set(result["matches"][0]) == {"heading", "excerpt"}
    assert result["memory"]["backend"] == backend and result["memory"]["candidates"] == []


def test_keyword_hit_is_not_a_tehm_rule_when_store_is_empty(memory):
    _, context = memory
    original = copy.deepcopy(context)
    result = knowledge.lookup("ALDFF", context=context)
    assert result["total"] > 0 and result["memory"]["decision"] == "NO_SKILL"
    assert context == original
