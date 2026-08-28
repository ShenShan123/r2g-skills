"""Crystallization pipeline (design doc 20.5, 26 Phase 5, test list 27.1).

Effect groups -> role-normalize -> joint anti-unification -> candidate rules ->
persisted into tehm_rules + tehm_rule_sources. Singletons never crystallize;
output is deterministic and idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tehm.adapters.r2g_evidence import capture_r2g_project
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.build_rules import crystallize_all
from tehm.lifecycle.rule_status import enter_shadow, set_status

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANTENNA_PROJ = FIXTURES / "project_antenna_fix"


def _repeat_record(base: dict, i: int, *, knob: str = "PLACE_DENSITY_LB_ADDON",
                   value: str | None = None) -> dict:
    rec = json.loads(json.dumps(base))
    rec["record_id"] = f"cry_{i}"
    rec["lineage_id"] = f"lineage_{i}"
    rec["design_id"] = f"design_{i}"
    rec["episode"] = {"episode_id": f"ep_cry_{i}", "lineage_id": f"lineage_{i}",
                      "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
    rec["action"]["payload"]["config_edits"] = {knob: value or f"0.1{i}"}
    rec["before"]["config"][knob] = "0.10"
    rec["after"]["config"][knob] = value or f"0.1{i}"
    rec["observation_delta"]["first_divergence"]["before"] = 10 + i
    return rec


def _capture_repeats(tmp_tehm, sample_record_dict, n: int = 3):
    conn, store, _ = tmp_tehm
    for i in range(n):
        capture(conn, store, ExecutionRecord.from_dict(_repeat_record(sample_record_dict, i)))


def test_crystallize_produces_rule(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rules = crystallize_all(conn)
    assert len(rules) == 1
    rule = rules[0]
    # Phase 6: the audit has run; the rule is admissible, not a raw candidate.
    assert rule["validity_status"] in ("VALIDATED", "PROVISIONAL_VALID")
    assert rule["transformation_family"] == "ANTENNA_DIODE_REPAIR"
    # the three instances share the knob (same name) but differ in VALUE:
    # knob stays concrete, the value becomes a hole; check shared -> concrete
    assert rule["before_pattern"]["target_check"] == "drc"
    assert rule["before_pattern"]["knob"] == "PLACE_DENSITY_LB_ADDON"
    assert rule["after_pattern"]["rewrite.value"].startswith("$H")
    assert "TARGET_FAILURE_REMOVED" in rule["obligations"]
    assert rule["provenance"]["source_episodes"]


def test_rule_persisted_with_sources(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    crystallize_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_rule_sources").fetchone()[0] == 3
    row = conn.execute(
        "SELECT before_pattern_json, merge_trace_digest, validity_status "
        "FROM tehm_rules").fetchone()
    assert "knob" in row["before_pattern_json"]
    assert row["merge_trace_digest"].startswith("au_")
    assert row["validity_status"] in ("VALIDATED", "PROVISIONAL_VALID")


def test_crystallize_rolls_back_rule_and_sources_on_persist_failure(
        tmp_tehm, sample_record_dict):
    """A failed derived rebuild cannot expose a partial rule projection."""
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    import tehm.crystallization.build_rules as build_rules

    original = build_rules._persist_rule

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected rule persistence failure")

    import pytest
    with patch.object(build_rules, "_persist_rule", side_effect=write_then_fail):
        with pytest.raises(RuntimeError, match="injected rule persistence failure"):
            crystallize_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_rule_sources").fetchone()[0] == 0
    assert not conn.in_transaction


def test_crystallize_commit_false_preserves_outer_transaction(
        tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    conn.execute("CREATE TEMP TABLE crystallize_outer_marker (value TEXT)")
    conn.execute("INSERT INTO crystallize_outer_marker VALUES ('keep')")

    rules = crystallize_all(conn, commit=False, retire_stale=False)

    assert rules
    assert conn.in_transaction
    assert conn.execute(
        "SELECT value FROM crystallize_outer_marker").fetchone()[0] == "keep"
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 0


def test_idempotent_recrystallize(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rules1 = crystallize_all(conn)
    rules2 = crystallize_all(conn)
    assert rules1[0]["rule_id"] == rules2[0]["rule_id"]
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 1


def test_promoted_rule_recrystallize_requires_exact_projection(
        tmp_tehm, sample_record_dict):
    """A normal rebuild cannot rewrite a production-authoritative rule."""
    conn, _, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rule = crystallize_all(conn)[0]
    rule_id = rule["rule_id"]
    enter_shadow(conn, rule_id=rule_id, target_scope="drc")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="candidate")
    set_status(conn, rule_id=rule_id, target_scope="drc", status="promoted")

    # An exact replay remains a no-op, including after promotion.
    assert crystallize_all(conn)[0]["rule_id"] == rule_id

    import copy
    import pytest
    import tehm.crystallization.build_rules as build_rules
    tampered = copy.deepcopy(rule)
    tampered["validity_profile"] = {
        **tampered["validity_profile"], "tampered": True}
    with pytest.raises(ValueError, match="promoted rule is immutable"):
        build_rules._persist_rule(conn, tampered, commit=False)
    stored = conn.execute(
        "SELECT validity_profile_json FROM tehm_rules WHERE rule_id=?",
        (rule_id,)).fetchone()
    assert json.loads(stored["validity_profile_json"]) == rule["validity_profile"]


def test_singletons_never_crystallize(tmp_tehm):
    """The 3-strategy antenna fixture is instance-dominated -> zero rules."""
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    rules = crystallize_all(conn)
    assert rules == []
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 0


def test_dry_run_writes_nothing(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rules = crystallize_all(conn, dry_run=True)
    assert len(rules) == 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 0


def test_crystallize_audits_and_stores_validity(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rules = crystallize_all(conn)
    assert rules[0]["validity_status"] in ("VALIDATED", "PROVISIONAL_VALID")
    assert "V2" in {g["name"] for g in rules[0]["validity_profile"]["gates"]}
    row = conn.execute(
        "SELECT validity_status, validity_profile_json, risk_profile_json "
        "FROM tehm_rules").fetchone()
    assert row["validity_status"] == rules[0]["validity_status"]
    assert '"gates"' in row["validity_profile_json"]
    assert row["risk_profile_json"]  # serialized [] is fine


def test_crystallize_deterministic_across_runs(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    rules1 = crystallize_all(conn)
    rules2 = crystallize_all(conn)
    assert rules1[0]["provenance"]["merge_trace"] == \
        rules2[0]["provenance"]["merge_trace"]
    assert rules1[0]["before_pattern"] == rules2[0]["before_pattern"]


def test_honesty_still_green_after_crystallize(tmp_tehm, sample_record_dict):
    conn, store, tmp = tmp_tehm
    _capture_repeats(tmp_tehm, sample_record_dict, n=3)
    crystallize_all(conn)
    from tehm import honesty

    all_ok, report = honesty.run_all(conn, store, tmp / "tehm.sqlite")
    assert all_ok, report
