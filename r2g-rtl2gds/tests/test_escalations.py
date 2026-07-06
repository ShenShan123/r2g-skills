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


def test_synth_memory_residual_is_valid_reason(tmp_path):
    """process_one escalates `synth_memory_residual` when synth_memory_relax cannot clear
    a too-large memory (needs a fakeram macro) — engineer_loop.py:912. It MUST be in REASONS,
    else open_escalation raises ValueError, the worker crashes, and the design is mislabeled
    `worker_exc:ValueError` (burying the honest reason). Same latent-crash class as the
    2026-06-23 place_density_residual gap (2026-06-30)."""
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="uart2axi4_x", project_path="/p/uart2axi4_x", run_id="r1",
        reason="synth_memory_residual", notes="memory too large even at cap; needs fakeram")
    assert escalations.list_open(conn)[0]["escalation_id"] == eid
    assert "synth_memory_residual" in escalations.REASONS


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


def test_incomplete_missing_header_is_valid_reason(tmp_path):
    """process_one escalates unresolved-`include synth aborts as
    'incomplete_missing_header'. Registering it here is REQUIRED: an unregistered
    reason makes open_escalation raise ValueError, which crashes the loop worker and
    mislabels the design 'worker_exc:ValueError' (24 designs, 2026-07-02 sky130 round)."""
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="riscv_x", project_path="/p/riscv_x", run_id="r1",
        reason="incomplete_missing_header", notes="unresolved `include")
    assert escalations.list_open(conn)[0]["escalation_id"] == eid


def test_synth_timeout_is_valid_reason(tmp_path):
    """process_one escalates yosys wall-clock synth timeouts as 'synth_timeout'
    (a large design, not a crash). Must be registered or open_escalation crashes the worker."""
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="lenet_x", project_path="/p/lenet_x", run_id="r1",
        reason="synth_timeout", notes="yosys hit ORFS_TIMEOUT")
    assert escalations.list_open(conn)[0]["escalation_id"] == eid


def test_all_loop_emitted_reasons_are_registered():
    """SYSTEMIC GUARD (prevents the 6th recurrence of the worker_exc:ValueError bug class).

    Every `reason = "<literal>"` the engineer_loop emits before calling
    escalations.open_escalation MUST be in escalations.REASONS, or the worker crashes at
    runtime with 'unknown escalation reason'. This has silently regressed FIVE times
    (place_density_residual, pin_overflow_residual, synth_memory_residual, pdn_strap_residual,
    then incomplete_missing_header + synth_timeout). Parse the loop source and assert the
    whitelist covers every reason it can produce, so a new reason can never ship unregistered.
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    # Every open_escalation EMITTER, not only the loop (2026-07-05: ab_runner's
    # repeated_regression was outside the sweep — a 7th-recurrence hole).
    sources = [root / "scripts" / "loop" / "engineer_loop.py",
               root / "knowledge" / "ab_runner.py"]
    emitted = set()
    for src in sources:
        emitted |= set(re.findall(r'reason\s*=\s*"([a-z_]+)"', src.read_text()))
    assert emitted, "parser found no reason literals -- did the sources move?"
    missing = sorted(emitted - set(escalations.REASONS))
    assert not missing, (
        f"escalation reason(s) emitted but not registered in escalations.REASONS: "
        f"{missing} -- open_escalation will raise ValueError and crash the worker on these.")


def test_signoff_stuck_scan_routes_separately_from_catalog_exhausted():
    """2026-07-05: a stuck DRC/LVS scan (diagnose STOPs before any strategy runs) is
    NOT an exhausted catalog — 13/37 of the sky130 round's catalog_exhausted
    escalations were this class, routed to the wrong runbook."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "loop"))
    import engineer_loop as el
    assert el._signoff_escalation_reason({"drc": "stuck", "lvs": "clean"}) == \
        "signoff_stuck_scan"
    assert el._signoff_escalation_reason({"drc": "fail", "lvs": "stuck"}) == \
        "signoff_stuck_scan"
    assert el._signoff_escalation_reason({"drc": "fail", "lvs": "fail"}) == \
        "catalog_exhausted"
    assert el._signoff_escalation_reason({}) == "catalog_exhausted"
    assert "signoff_stuck_scan" in escalations.REASONS   # worker must not crash on it
