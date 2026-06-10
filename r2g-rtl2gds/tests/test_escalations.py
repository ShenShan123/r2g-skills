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
