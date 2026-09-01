"""P1 current-valid-state resolution and relation firewall tests."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tehm import db
from tehm.state import (
    StateResolutionError, ensure_state_schema, load_resolution_snapshot,
    record_relation, resolve_current_state, verify_resolution_snapshot,
)


def _rule(conn: sqlite3.Connection, rule_id: str, *, scope: str = "global") -> None:
    conn.execute(
        """INSERT INTO tehm_rules (
               rule_id, domain, before_pattern_json, after_pattern_json,
               hard_preconditions_json, context_profile_json, obligations_json,
               validity_status, validity_profile_json, confidence_json,
               utility_json, risk_profile_json, predicate_schema_version,
               role_schema_version, crystallizer_version, merge_trace_digest,
               created_at, updated_at)
           VALUES (?, 'rtl', '{}', '{}', '[]', '{}', '[]', 'VALIDATED',
                   '{}', '{}', '{}', '{}', 'predicate-v0.1', 'role-v0.1',
                   'test', 'sha256:test', 'now', 'now')""", (rule_id,))
    conn.execute(
        """INSERT INTO tehm_rule_status
           (rule_id, target_scope, status, status_version, provenance_json,
            updated_at)
           VALUES (?, ?, 'promoted', 1, '{}', 'now')""", (rule_id, scope))
    conn.commit()


def test_relation_replay_and_scope_aware_supersession_are_shadow_only(tmp_tehm):
    conn, _, _ = tmp_tehm
    _rule(conn, "rule-old")
    _rule(conn, "rule-new")
    before = conn.execute(
        "SELECT status, status_version FROM tehm_rule_status "
        "WHERE rule_id='rule-old'").fetchone()
    receipt = record_relation(
        conn, source_type="rule", source_id="rule-new", relation_type="SUPERSEDES",
        target_type="rule", target_id="rule-old",
        scope={"compatibility_profile": "rtl.fsm.v1"},
        evidence_refs=("transition-witness",))
    replay = record_relation(
        conn, source_type="rule", source_id="rule-new", relation_type="SUPERSEDES",
        target_type="rule", target_id="rule-old",
        scope={"compatibility_profile": "rtl.fsm.v1"},
        evidence_refs=("transition-witness",))
    assert replay.to_dict() == receipt.to_dict()

    state = resolve_current_state(
        conn, {"target_scope": "global", "compatibility_profile": "rtl.fsm.v1"})
    assert state.active_rules == ("rule-new",)
    assert state.shadow_relation_ids == (receipt.relation_id,)
    assert state.suppressed[0].object_id == "rule-old"
    assert state.suppressed[0].replacement_id is None

    # Resolution is derived state only: the lifecycle row is untouched.
    after = conn.execute(
        "SELECT status, status_version FROM tehm_rule_status "
        "WHERE rule_id='rule-old'").fetchone()
    assert tuple(after) == tuple(before)
    verified = verify_resolution_snapshot(conn, state.resolution_id)
    assert verified.relation_count == 1

    # The relation is not authority-bound, so a production-mode resolver must
    # abstain instead of allowing shadow evidence to affect runtime state.
    with pytest.raises(StateResolutionError, match="UNRESOLVED_AUTHORITY"):
        resolve_current_state(conn, {"compatibility_profile": "rtl.fsm.v1"},
                              mode="production", persist=False)

    # A different scope does not consume the relation.
    other = resolve_current_state(
        conn, {"target_scope": "global", "compatibility_profile": "rtl.fsm.v2"},
        persist=False)
    assert other.active_rules == ("rule-new", "rule-old")
    assert other.suppressed == ()


def test_informational_relation_is_not_authority_gated_or_suppressing(tmp_tehm):
    conn, _, _ = tmp_tehm
    _rule(conn, "rule-a")
    _rule(conn, "rule-b")
    relation = record_relation(
        conn, source_type="rule", source_id="rule-b", relation_type="SPECIALIZES",
        target_type="rule", target_id="rule-a", evidence_refs=("semantic-witness",))
    state = resolve_current_state(conn, {"target_scope": "global"},
                                  mode="production", persist=False)
    assert set(state.active_rules) == {"rule-a", "rule-b"}
    assert state.suppressed == ()
    assert relation.relation_id in state.shadow_relation_ids


def test_relation_cycle_is_fail_closed(tmp_tehm):
    conn, _, _ = tmp_tehm
    _rule(conn, "rule-a")
    _rule(conn, "rule-b")
    record_relation(conn, source_type="rule", source_id="rule-a",
                    relation_type="SUPERSEDES", target_type="rule",
                    target_id="rule-b", evidence_refs=("witness-a",))
    record_relation(conn, source_type="rule", source_id="rule-b",
                    relation_type="SUPERSEDES", target_type="rule",
                    target_id="rule-a", evidence_refs=("witness-b",))
    with pytest.raises(StateResolutionError, match="CYCLE_CONFLICT"):
        resolve_current_state(conn, {"target_scope": "global"}, persist=False)


def test_authority_mismatch_and_contradiction_abstain(tmp_tehm):
    conn, _, _ = tmp_tehm
    _rule(conn, "rule-a")
    _rule(conn, "rule-b")
    record_relation(
        conn, source_type="rule", source_id="rule-a", relation_type="CONTRADICTS",
        target_type="rule", target_id="rule-b", evidence_refs=("witness",))
    record_relation(
        conn, source_type="rule", source_id="rule-a", relation_type="SUPERSEDES",
        target_type="rule", target_id="rule-b", evidence_refs=("witness-2",),
        authority_ref="authority-does-not-exist")
    with pytest.raises(StateResolutionError, match="UNRESOLVED_AUTHORITY"):
        resolve_current_state(conn, {"target_scope": "global"}, persist=False)

    conn.execute(
        "DELETE FROM tehm_memory_relations WHERE authority_ref=?",
        ("authority-does-not-exist",))
    conn.commit()
    state = resolve_current_state(conn, {"target_scope": "global"}, persist=False)
    assert state.active_rules == ()
    assert state.unresolved_conflicts[0].startswith("AMBIGUOUS_CURRENT_STATE:")


def test_corrupt_relation_and_snapshot_are_rejected(tmp_tehm):
    conn, _, _ = tmp_tehm
    _rule(conn, "rule-old")
    _rule(conn, "rule-new")
    relation = record_relation(
        conn, source_type="rule", source_id="rule-new", relation_type="SUPERSEDES",
        target_type="rule", target_id="rule-old", evidence_refs=("witness",))
    state = resolve_current_state(conn, {"target_scope": "global"})

    conn.execute("UPDATE tehm_memory_relations SET relation_digest='sha256:tampered'")
    conn.commit()
    with pytest.raises(ValueError, match="digest mismatch"):
        resolve_current_state(conn, {"target_scope": "global"}, persist=False)

    # Restore the relation only through the immutable content, then tamper the
    # snapshot payload.  The resolver must reject the persisted derived state.
    conn.execute("DELETE FROM tehm_memory_relations")
    record_relation(
        conn, source_type="rule", source_id="rule-new", relation_type="SUPERSEDES",
        target_type="rule", target_id="rule-old", evidence_refs=("witness",))
    conn.execute(
        "UPDATE tehm_state_resolution_snapshots SET active_rules_json=? "
        "WHERE resolution_id=?", (json.dumps(["rule-old"]), state.resolution_id))
    conn.commit()
    with pytest.raises(StateResolutionError, match="snapshot digest mismatch"):
        load_resolution_snapshot(conn, state.resolution_id)


def test_state_tables_are_added_to_an_existing_v4_store(tmp_path):
    path = tmp_path / "v4.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tehm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tehm_meta VALUES ('schema_version', 'tehm-v4');
    """)
    ensure_state_schema(conn)
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tehm_memory_relations", "tehm_state_resolution_snapshots"} <= tables
    assert conn.execute(
        "SELECT value FROM tehm_meta WHERE key='schema_version'").fetchone()[0] == "tehm-v4"
    conn.close()
