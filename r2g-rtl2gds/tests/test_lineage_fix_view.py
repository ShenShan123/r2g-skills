"""Read-only tests for the fix-effectiveness projection in build_lineage_view.py.

Seeds ``fix_trajectories`` (resolved + abandoned episodes) and asserts the new
``fix_effectiveness`` key reports, per (family, platform, check, violation_class),
each strategy's resolved/abandoned counts + clearance rate. No DB writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import knowledge_db

# Make scripts/reports/ importable for the standalone projection module.
_REPORTS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "reports"
if str(_REPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(_REPORTS_DIR))
import build_lineage_view  # noqa: E402


def _open_db(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn, db_path


def _insert_traj(conn, *, fix_session_id, design_family, platform, check_type,
                 violation_class, outcome, winning_strategy, n_iters=1,
                 design_name="dut", project_path=None, initial_count=10.0,
                 final_count=0.0, total_elapsed_s=12.0, path_json=None):
    if path_json is None:
        # A single verdict-bearing step that matches the outcome, so the
        # per-strategy success/failure tally (parsed from path_json) is coherent
        # with the episode outcome.
        verdict = "cleared" if outcome == "resolved" else "no_change"
        path_json = json.dumps([{"iter": 0, "strategy": winning_strategy or "none",
                                 "before": 10, "after": 0 if outcome == "resolved"
                                 else 8, "verdict": verdict}])
    conn.execute(
        "INSERT INTO fix_trajectories "
        "(fix_session_id, project_path, design_name, design_family, platform, "
        " check_type, violation_class, path_json, n_iters, outcome, "
        " winning_strategy, winning_config_json, failed_strategies_json, "
        " initial_count, final_count, total_elapsed_s) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fix_session_id, project_path or f"/tmp/{fix_session_id}", design_name,
         design_family, platform, check_type, violation_class,
         path_json, n_iters,
         outcome, winning_strategy, "{}", "[]", initial_count, final_count,
         total_elapsed_s),
    )


def test_fix_effectiveness_key_present_and_empty_when_no_table(tmp_knowledge_dir):
    """An empty (but present) fix_trajectories table yields an empty projection."""
    conn, db_path = _open_db(tmp_knowledge_dir)
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)
    assert "fix_effectiveness" in view
    assert view["fix_effectiveness"] == []
    # Additive: the prior keys still exist.
    assert "health" in view and "provenance" in view


def test_fix_effectiveness_per_strategy_counts_and_rate(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    # aes_xcrypt/nangate45 drc M2_SPACING:
    #   antenna_diode_repair: 3 resolved, 1 abandoned -> clearance 0.75
    #   route_density_relax:  1 resolved, 1 abandoned -> clearance 0.5
    for i in range(3):
        _insert_traj(conn, fix_session_id=f"a_res_{i}", design_family="aes_xcrypt",
                     platform="nangate45", check_type="drc",
                     violation_class="M2_SPACING", outcome="resolved",
                     winning_strategy="antenna_diode_repair")
    # Abandoned episodes mirror _build_trajectory: winning_strategy=None, but the
    # path_json records the strategy that failed (verdict no_change/regression).
    _insert_traj(conn, fix_session_id="a_ab_0", design_family="aes_xcrypt",
                 platform="nangate45", check_type="drc",
                 violation_class="M2_SPACING", outcome="abandoned",
                 winning_strategy=None,
                 path_json=json.dumps([
                     {"iter": 0, "strategy": "antenna_diode_repair",
                      "before": 10, "after": 10, "verdict": "no_change"}]))
    _insert_traj(conn, fix_session_id="r_res_0", design_family="aes_xcrypt",
                 platform="nangate45", check_type="drc",
                 violation_class="M2_SPACING", outcome="resolved",
                 winning_strategy="route_density_relax")
    _insert_traj(conn, fix_session_id="r_ab_0", design_family="aes_xcrypt",
                 platform="nangate45", check_type="drc",
                 violation_class="M2_SPACING", outcome="abandoned",
                 winning_strategy=None,
                 path_json=json.dumps([
                     {"iter": 0, "strategy": "route_density_relax",
                      "before": 10, "after": 12, "verdict": "regression"}]))
    # A distinct group (different family) to prove grouping isolation.
    _insert_traj(conn, fix_session_id="spi_res_0", design_family="spi",
                 platform="nangate45", check_type="lvs",
                 violation_class="net_mismatch", outcome="resolved",
                 winning_strategy="same_nets_seed")
    conn.commit()
    conn.close()

    mtime_before = db_path.stat().st_mtime_ns
    view = build_lineage_view.build_view(db_path)
    # Read-only: must not write the DB.
    assert db_path.stat().st_mtime_ns == mtime_before

    fx = view["fix_effectiveness"]
    assert isinstance(fx, list)

    # Locate the aes_xcrypt/nangate45/drc/M2_SPACING group.
    aes = [g for g in fx if g["design_family"] == "aes_xcrypt"
           and g["check_type"] == "drc"
           and g["violation_class"] == "M2_SPACING"]
    assert len(aes) == 1
    group = aes[0]
    assert group["platform"] == "nangate45"

    strats = {s["strategy"]: s for s in group["strategies"]}
    # No phantom strategy=None bucket: failures attribute to the named strategy
    # that was tried (read from path_json), never to a null row.
    assert set(strats) == {"antenna_diode_repair", "route_density_relax"}
    assert None not in strats
    assert all(s["strategy"] is not None for s in group["strategies"])

    diode = strats["antenna_diode_repair"]
    assert diode["resolved"] == 3
    assert diode["abandoned"] == 1
    assert diode["clearance_rate"] == 0.75

    relax = strats["route_density_relax"]
    assert relax["resolved"] == 1
    assert relax["abandoned"] == 1
    assert relax["clearance_rate"] == 0.5

    # Isolated group present.
    spi = [g for g in fx if g["design_family"] == "spi"]
    assert len(spi) == 1
    spi_strats = {s["strategy"]: s for s in spi[0]["strategies"]}
    assert spi_strats["same_nets_seed"]["resolved"] == 1
    assert spi_strats["same_nets_seed"]["abandoned"] == 0
    assert spi_strats["same_nets_seed"]["clearance_rate"] == 1.0


def test_fix_effectiveness_is_deterministic(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _insert_traj(conn, fix_session_id="x", design_family="f", platform="nangate45",
                 check_type="drc", violation_class="V", outcome="resolved",
                 winning_strategy="s1")
    conn.commit()
    conn.close()

    v1 = build_lineage_view.build_view(db_path)
    v2 = build_lineage_view.build_view(db_path)
    assert v1 == v2
    assert set(v1.keys()) == {"health", "provenance", "fix_effectiveness"}
