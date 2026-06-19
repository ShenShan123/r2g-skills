"""Escalation queue (spec §5.5): the loop records, the agent drains."""
import escalations
import knowledge_db


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "knowledge.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def test_open_and_list(tmp_path):
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="aes_x", project_path="/p/aes_x", run_id="r1",
        reason="catalog_exhausted", symptom_id="deadbeef00000001",
        notes="3 strategies tried, residual 4 violations")
    rows = escalations.list_open(conn)
    assert len(rows) == 1 and rows[0]["escalation_id"] == eid
    assert rows[0]["reason"] == "catalog_exhausted"


def test_duplicate_open_for_same_design_reason_is_noop(tmp_path):
    conn = _conn(tmp_path)
    escalations.open_escalation(conn, design="aes_x", project_path="/p/aes_x",
                                run_id="r1", reason="unknown_symptom")
    escalations.open_escalation(conn, design="aes_x", project_path="/p/aes_x",
                                run_id="r2", reason="unknown_symptom")
    assert len(escalations.list_open(conn)) == 1


def test_resolve_marks_drained(tmp_path):
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(conn, design="aes_x",
                                      project_path="/p/aes_x", run_id="r1",
                                      reason="unseen_crash")
    escalations.resolve(conn, eid, status="drained",
                        notes="authored shadow strategy cts_skip_clone")
    assert escalations.list_open(conn) == []


def test_invalid_reason_rejected(tmp_path):
    conn = _conn(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        escalations.open_escalation(conn, design="x", project_path="/p/x",
                                    run_id="r", reason="bogus")


def test_route_congestion_residual_is_valid_reason(tmp_path):
    """A route abort whose route_relief fixer is exhausted/inapplicable escalates
    with this KNOWN-residual reason (not unseen_crash) — 2026-06-17."""
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="chacha_x", project_path="/p/chacha_x", run_id="r1",
        reason="route_congestion_residual", notes="util at floor")
    assert escalations.list_open(conn)[0]["escalation_id"] == eid


def test_resolve_for_design_closes_all_open(tmp_path):
    """When a later run drives a design clean, every open escalation for it is
    auto-drained so the queue is not a graveyard of superseded aborts (2026-06-17)."""
    conn = _conn(tmp_path)
    escalations.open_escalation(conn, design="d1", project_path="/p/d1",
                                run_id="r1", reason="unseen_crash")
    escalations.open_escalation(conn, design="d1", project_path="/p/d1",
                                run_id="r2", reason="catalog_exhausted")
    escalations.open_escalation(conn, design="d2", project_path="/p/d2",
                                run_id="r3", reason="unseen_crash")
    n = escalations.resolve_for_design(conn, "d1", notes="went clean")
    assert n == 2
    open_designs = {r["design"] for r in escalations.list_open(conn)}
    assert open_designs == {"d2"}            # d1 closed, d2 untouched
    assert escalations.resolve_for_design(conn, "d1") == 0   # idempotent no-op
