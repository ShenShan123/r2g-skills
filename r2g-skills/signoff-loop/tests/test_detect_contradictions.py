"""Deterministic recipe-contradiction probe (Workstream C).

A STRUCTURAL contradiction: for the SAME symptom signature, two strategies that
applied OPPOSITE directions on the SAME config knob where BOTH reached a
successful outcome. Direction is data-driven from the actual {old,new} deltas in
config_lineage.diff_json (the strategy is tied to the edge via fix_events:
symptom_id + strategy + the knob's new value). Superseded pairs are excluded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import knowledge_db

# Make scripts/reports/ importable for the standalone probe + lineage view.
_REPORTS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "reports"
if str(_REPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(_REPORTS_DIR))
import detect_contradictions  # noqa: E402
import build_lineage_view  # noqa: E402


def _open_db(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn, db_path


def _insert_run(conn, run_id, design_name, platform="sky130hd",
                orfs_status="partial", drc_status="clean", lvs_status="clean",
                rcx_status="complete"):
    conn.execute(
        "INSERT INTO runs (run_id, project_path, design_name, design_family, "
        "platform, ingested_at, orfs_status, drc_status, lvs_status, rcx_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, f"/tmp/{run_id}", design_name, design_name, platform,
         "2026-06-18T00:00:00Z", orfs_status, drc_status, lvs_status, rcx_status))


def _insert_fix_event(conn, *, symptom_id, strategy, design_name, knob, new_val,
                      platform="sky130hd", verdict="cleared", after_status="clean",
                      session=None):
    session = session or f"{design_name}-{strategy}"
    conn.execute(
        "INSERT INTO fix_events (fix_session_id, project_path, design_name, "
        "design_family, platform, check_type, violation_class, iter, strategy, "
        "after_status, verdict, config_delta_json, symptom_id, ts, provenance) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session, f"/tmp/{design_name}", design_name, design_name, platform,
         "drc", "m3.2", 0, strategy, after_status, verdict,
         json.dumps({knob: new_val}), symptom_id, "2026-06-18T00:00:00Z", "live"))


def _insert_lineage(conn, *, design_name, platform, current_run_id,
                    previous_run_id, knob, old, new, is_success=True,
                    created_at="2026-06-18T00:00:00Z"):
    diff = {"added": {}, "removed": {},
            "changed": {knob: {"old": str(old), "new": str(new)}}}
    outcome = json.dumps({"is_success": is_success}, sort_keys=True)
    conn.execute(
        "INSERT INTO config_lineage (design_name, platform, current_run_id, "
        "previous_run_id, diff_json, current_outcome, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (design_name, platform, current_run_id, previous_run_id,
         json.dumps(diff, sort_keys=True), outcome, created_at))


def _insert_recipe_status(conn, *, symptom_id, design_class, platform, strategy,
                          status, generation=1):
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, provenance, generation, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (symptom_id, design_class, platform, strategy, status, "test",
         generation, "2026-06-18T00:00:00Z"))


# ── (a) opposite directions on the SAME knob, both successful -> 1 hit ──────
def test_opposite_directions_flagged(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000001"
    knob = "CORE_UTILIZATION"
    # strategy A: RAISE 20 -> 25, succeeded.
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    # strategy B: LOWER 20 -> 15, succeeded.
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15)
    conn.commit()
    conn.close()

    conn2 = knowledge_db.connect(db_path)
    hits = detect_contradictions.find_contradictions(conn2)
    conn2.close()

    assert len(hits) == 1, hits
    h = hits[0]
    assert h["symptom_id"] == sid
    assert h["knob"] == knob
    assert {h["strategy_a"], h["strategy_b"]} == {"util_raise", "density_relief"}
    assert {h["dir_a"], h["dir_b"]} == {"raise", "lower"}
    assert h["evidence_a"]["successes"] >= 1
    assert h["evidence_b"]["successes"] >= 1
    assert h["demote_command"].startswith(
        "python3 scripts/loop/engineer_loop.py demote")
    # the demote target must be one of the two strategies + carry the knob + other.
    assert "--symptom %s" % sid in h["demote_command"]
    assert "--strategy" in h["demote_command"]
    assert knob in h["demote_command"]


# ── (b) SAME direction on the same knob -> 0 hits ───────────────────────────
def test_same_direction_not_flagged(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000002"
    knob = "CORE_UTILIZATION"
    for tag, design, new in (("s1", "dA", 15), ("s2", "dB", 12)):
        _insert_run(conn, f"{design}_prev", design)
        _insert_run(conn, f"{design}_cur", design)
        _insert_fix_event(conn, symptom_id=sid, strategy=tag, design_name=design,
                          knob=knob, new_val=str(new))
        _insert_lineage(conn, design_name=design, platform="sky130hd",
                        current_run_id=f"{design}_cur",
                        previous_run_id=f"{design}_prev",
                        knob=knob, old=20, new=new)
    conn.commit()
    conn.close()

    conn2 = knowledge_db.connect(db_path)
    hits = detect_contradictions.find_contradictions(conn2)
    conn2.close()
    assert hits == []


# ── (c) superseded pair -> excluded ─────────────────────────────────────────
def test_superseded_pair_excluded(tmp_knowledge_dir):
    """strategy B's evidence run was later superseded (it is the previous_run_id
    of a strictly-later improved edge) -> the contradiction must be suppressed."""
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000003"
    knob = "CORE_UTILIZATION"
    # strategy A: RAISE 20 -> 25, succeeded, never superseded.
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    # strategy B: LOWER 20 -> 15 on designB, BUT rB_cur was later improved upon
    # (it is the previous_run_id of a strictly-later edge rB_cur -> rB_next).
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB")
    _insert_run(conn, "rB_next", "designB")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15,
                    created_at="2026-06-18T00:00:00Z")
    # the SUPERSEDING edge: rB_cur is now somebody's previous_run_id, improved.
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_next", previous_run_id="rB_cur",
                    knob=knob, old=15, new=18,
                    created_at="2026-06-19T00:00:00Z")
    conn.commit()
    conn.close()

    conn2 = knowledge_db.connect(db_path)
    hits = detect_contradictions.find_contradictions(conn2)
    conn2.close()
    assert hits == []


# ── severity: both promoted -> 'high' ───────────────────────────────────────
def test_severity_high_when_both_promoted(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000004"
    knob = "CORE_UTILIZATION"
    dc = "logic/small"
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15)
    # Both strategies promoted -> high severity. design_class derived from fix_event
    # is 'unknown/unknown' (no design_class col here); seed recipe_status to match.
    for strat in ("util_raise", "density_relief"):
        _insert_recipe_status(conn, symptom_id=sid, design_class="unknown/unknown",
                              platform="sky130hd", strategy=strat, status="promoted")
    conn.commit()
    conn.close()

    conn2 = knowledge_db.connect(db_path)
    hits = detect_contradictions.find_contradictions(conn2)
    conn2.close()
    assert len(hits) == 1
    assert hits[0]["severity"] == "high"


# ── failed-outcome strategy excluded (BOTH must be successful) ──────────────
def test_unsuccessful_strategy_not_flagged(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000005"
    knob = "CORE_UTILIZATION"
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    # strategy B moved the knob the other way but did NOT succeed (verdict=regression,
    # lineage outcome is_success=False) -> not a valid contradiction.
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB", drc_status="fail")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15",
                      verdict="regression", after_status="fail")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15, is_success=False)
    conn.commit()
    conn.close()

    conn2 = knowledge_db.connect(db_path)
    hits = detect_contradictions.find_contradictions(conn2)
    conn2.close()
    assert hits == []


# ── empty DB never raises ───────────────────────────────────────────────────
def test_empty_db_no_hits(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    conn.commit()
    conn.close()
    conn2 = knowledge_db.connect(db_path)
    assert detect_contradictions.find_contradictions(conn2) == []
    conn2.close()


# ── (d) build_lineage_view exposes a 'contradictions' key ───────────────────
def test_build_view_has_contradictions_key(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000006"
    knob = "CORE_UTILIZATION"
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15)
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)
    assert "contradictions" in view
    assert isinstance(view["contradictions"], list)
    assert len(view["contradictions"]) == 1
    assert view["contradictions"][0]["knob"] == knob


# ── CLI --json emits a JSON list, exits 0 ───────────────────────────────────
def test_cli_json(tmp_knowledge_dir, capsys):
    conn, db_path = _open_db(tmp_knowledge_dir)
    sid = "deadbeef00000007"
    knob = "CORE_UTILIZATION"
    _insert_run(conn, "rA_prev", "designA")
    _insert_run(conn, "rA_cur", "designA")
    _insert_fix_event(conn, symptom_id=sid, strategy="util_raise",
                      design_name="designA", knob=knob, new_val="25")
    _insert_lineage(conn, design_name="designA", platform="sky130hd",
                    current_run_id="rA_cur", previous_run_id="rA_prev",
                    knob=knob, old=20, new=25)
    _insert_run(conn, "rB_prev", "designB")
    _insert_run(conn, "rB_cur", "designB")
    _insert_fix_event(conn, symptom_id=sid, strategy="density_relief",
                      design_name="designB", knob=knob, new_val="15")
    _insert_lineage(conn, design_name="designB", platform="sky130hd",
                    current_run_id="rB_cur", previous_run_id="rB_prev",
                    knob=knob, old=20, new=15)
    conn.commit()
    conn.close()

    rc = detect_contradictions.main(
        ["--db", str(db_path), "--heuristics", str(tmp_knowledge_dir / "nope.json"),
         "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["knob"] == knob
