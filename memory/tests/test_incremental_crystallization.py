"""Affected-group crystallization and revision lineage tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tehm.canonical.capture import ExecutionRecord, capture
from tehm.evolution import crystallize_affected_groups, preview_affected_groups
from tehm.rtl.rtl_evidence import build_rtl_execution_record
from tehm.rtl.rtl_oracle import IcarusOracle


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def test_incremental_crystallization_emits_revision_and_equivalence(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    transition_ids = []
    for name in ("req_ack_bug", "req_ack_bug2"):
        transition_ids.append(capture(
            conn, store, build_rtl_execution_record(
                PROJECTS / name, oracle=oracle, store=store)).transition_id)
    report = crystallize_affected_groups(conn, transition_ids, campaign_id="live")
    assert report.affected_effect_keys
    assert report.rules
    assert report.affected_group_keys
    assert report.full_rebuild_equivalent is True
    assert report.raw_evidence_preserved is True
    assert report.raw_evidence_before_digest == report.raw_evidence_after_digest
    assert conn.execute("SELECT COUNT(*) FROM tehm_rule_revisions").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM tehm_memory_events WHERE event_type='CONSOLIDATION_TRIGGERED'").fetchone()[0] == 1


def test_incremental_preview_is_read_only_and_matches_full_rebuild(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    transition_ids = []
    for name in ("req_ack_bug", "req_ack_bug2"):
        transition_ids.append(capture(
            conn, store, build_rtl_execution_record(
                PROJECTS / name, oracle=oracle, store=store)).transition_id)
    before = {
        "rules": conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0],
        "sources": conn.execute(
            "SELECT COUNT(*) FROM tehm_rule_sources").fetchone()[0],
        "revisions": conn.execute(
            "SELECT COUNT(*) FROM tehm_rule_revisions").fetchone()[0],
        "events": conn.execute(
            "SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0],
    }
    first = preview_affected_groups(conn, transition_ids, campaign_id="live")
    second = preview_affected_groups(conn, transition_ids, campaign_id="live")
    assert first.mode == "preview"
    assert first.rules
    assert first.affected_group_keys
    assert first.full_rebuild_equivalent is True
    assert first.raw_evidence_preserved is True
    assert first.raw_evidence_before_digest == first.raw_evidence_after_digest
    assert first.full_rebuild_rule_ids
    assert first.to_dict() == second.to_dict()
    after = {
        "rules": conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0],
        "sources": conn.execute(
            "SELECT COUNT(*) FROM tehm_rule_sources").fetchone()[0],
        "revisions": conn.execute(
            "SELECT COUNT(*) FROM tehm_rule_revisions").fetchone()[0],
        "events": conn.execute(
            "SELECT COUNT(*) FROM tehm_memory_events").fetchone()[0],
    }
    assert after == before


def test_incremental_scope_preserves_compatibility_group_boundary(
        tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    transition_ids = {"profile_a": [], "profile_b": []}
    for profile in transition_ids:
        for index in range(2):
            record = json.loads(json.dumps(sample_record_dict))
            record["record_id"] = f"compat_{profile}_{index}"
            record["lineage_id"] = f"lineage_{profile}_{index}"
            record["episode"] = {
                "episode_id": f"episode_{profile}_{index}",
                "lineage_id": f"lineage_{profile}_{index}",
                "step_index": 0,
                "terminal_status": "VERIFIED_REPAIR",
            }
            record["action"]["payload"]["compatibility_profile"] = profile
            value = f"0.{4 + index + (10 if profile == 'profile_b' else 0)}"
            record["action"]["payload"]["config_edits"] = {
                "PLACE_DENSITY_LB_ADDON": value}
            record["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = value
            transition_ids[profile].append(capture(
                conn, store, ExecutionRecord.from_dict(record)).transition_id)

    report = crystallize_affected_groups(
        conn, transition_ids["profile_a"], campaign_id="live")
    assert report.affected_group_keys
    assert {profile for _, profile in report.affected_group_keys} == {"profile_a"}
    assert len(report.rules) == 1
    witnesses = {
        transition_id
        for values in report.rules[0]["provenance"]
        .get("source_episode_transitions", {}).values()
        for transition_id in values
    }
    assert witnesses == set(transition_ids["profile_a"])
    assert report.full_rebuild_equivalent is True


def test_incremental_divergence_rolls_back_derived_update(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    transition_ids = []
    for name in ("req_ack_bug", "req_ack_bug2"):
        transition_ids.append(capture(
            conn, store, build_rtl_execution_record(
                PROJECTS / name, oracle=oracle, store=store)).transition_id)

    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_rules", "tehm_rule_sources",
                      "tehm_rule_revisions", "tehm_memory_events")}
    with patch(
            "tehm.evolution.incremental_crystallize._equivalence",
            return_value=(False, ("unexpected-rule",))):
        with pytest.raises(RuntimeError, match="diverged from full rebuild"):
            crystallize_affected_groups(conn, transition_ids, campaign_id="live")
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before
