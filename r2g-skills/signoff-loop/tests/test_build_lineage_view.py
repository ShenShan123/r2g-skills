"""Golden/deterministic tests for build_lineage_view.py (read-only projection)."""
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

# Make scripts/dashboard/ importable to exercise the render helpers (HTML
# escaping). Importing the module is side-effect-free: it computes BASE/OUT at
# import but does not call main(), so no filesystem writes occur.
_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "scripts" / "dashboard"
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))
import generate_multi_project_dashboard as dashboard  # noqa: E402


def _insert(conn, **row):
    defaults = dict.fromkeys([
        "run_id", "project_path", "design_name", "design_family", "platform",
        "ingested_at", "core_utilization", "place_density_lb_addon",
        "synth_hierarchical", "abc_area", "die_area", "clock_period_ns",
        "extra_config_json", "orfs_status", "orfs_fail_stage", "wns_ns", "tns_ns",
        "timing_tier", "cell_count", "area_um2", "power_mw",
        "drc_status", "drc_violations", "lvs_status", "rcx_status",
        "total_elapsed_s", "stage_times_json",
    ])
    defaults.update(row)
    defaults["ingested_at"] = "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def _insert_lineage(conn, design_name, platform, current_run_id, previous_run_id,
                    diff, created_at, current_outcome="partial"):
    conn.execute(
        "INSERT INTO config_lineage "
        "(design_name, platform, current_run_id, previous_run_id, diff_json, "
        " current_outcome, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (design_name, platform, current_run_id, previous_run_id,
         json.dumps(diff, sort_keys=True), current_outcome, created_at),
    )


def _open_db(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn, db_path


def _seed(conn):
    # 3 learnable successes (partial + clean signoff) for aes_xcrypt/nangate45.
    for i in range(3):
        _insert(conn, run_id=f"aes_ok_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                orfs_status="partial", drc_status="clean", lvs_status="clean",
                rcx_status="complete", timing_tier="clean")
    # 2 unknown failures (no positive signoff) — not learnable, distinct status.
    for i in range(2):
        _insert(conn, run_id=f"aes_bad_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                orfs_status="unknown", drc_status=None, lvs_status=None,
                rcx_status=None)
    # 1 partial with only drc_clean_beol (positive, but family below threshold).
    _insert(conn, run_id="spi_beol_0", design_name="spi_master",
            design_family="spi", platform="nangate45",
            orfs_status="partial", drc_status="clean_beol", lvs_status="clean",
            rcx_status="complete")


def test_build_view_is_deterministic(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    v1 = build_lineage_view.build_view(db_path)
    v2 = build_lineage_view.build_view(db_path)
    assert v1 == v2
    assert set(v1.keys()) == {"health", "provenance", "fix_effectiveness",
                              "contradictions"}
    # Purity: no timestamp leaks into build_view output.
    assert "generated_at" not in v1


def test_health_numbers_exact(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    # No heuristics.json present yet → empty.
    view = build_lineage_view.build_view(db_path,
                                         heuristics_path=tmp_knowledge_dir / "nope.json")
    h = view["health"]
    assert h["total_runs"] == 6
    assert h["orfs_status_counts"] == {"partial": 4, "unknown": 2}
    # sorted keys
    assert list(h["orfs_status_counts"].keys()) == sorted(h["orfs_status_counts"].keys())
    assert h["pct_partial_or_unknown"] == 100.0
    assert h["signoff_positive"] == {
        "lvs_clean": 4, "drc_clean": 3, "drc_clean_beol": 1, "rcx_complete": 4,
    }
    # learnable_pairs uses knowledge_db.is_success: only aes_xcrypt/nangate45
    # has >= 3 successes (the 3 partial+clean rows). spi has 1; bad aes are not
    # successes.
    assert h["learnable_pairs"] == 1
    assert h["heuristics_populated"] is False
    assert h["heuristics_family_count"] == 0
    assert h["min_successful_required"] == 3


def test_learnable_pairs_agrees_with_is_success(tmp_knowledge_dir):
    """The health count must exactly equal a direct is_success tally."""
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)

    import sqlite3
    c = sqlite3.connect(db_path)
    cur = c.execute("SELECT * FROM runs")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    c.close()
    groups: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["design_family"], r["platform"])
        groups.setdefault(key, 0)
        if knowledge_db.is_success(r):
            groups[key] += 1
    expected = sum(1 for v in groups.values() if v >= 3)
    assert view["health"]["learnable_pairs"] == expected


def test_heuristics_populated_true_when_file_present(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    heur = tmp_knowledge_dir / "heuristics.json"
    heur.write_text(json.dumps({
        "min_successful_runs_required": 5,
        "families": {"aes_xcrypt": {"platforms": {"nangate45": {}}}},
    }))
    view = build_lineage_view.build_view(db_path, heuristics_path=heur)
    h = view["health"]
    assert h["heuristics_populated"] is True
    assert h["heuristics_family_count"] == 1
    assert h["min_successful_required"] == 5


def test_heuristics_malformed_never_raises(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    heur = tmp_knowledge_dir / "heuristics.json"
    heur.write_text("{ this is not json")
    view = build_lineage_view.build_view(db_path, heuristics_path=heur)
    assert view["health"]["heuristics_populated"] is False
    assert view["health"]["heuristics_family_count"] == 0


def test_provenance_chain_ordered_and_parsed(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    # Three runs forming a 2-edge chain r1 -> r2 -> r3.
    for rid, util, drc in [("r1", 20.0, "clean"), ("r2", 25.0, "clean_beol"),
                           ("r3", 30.0, "clean")]:
        _insert(conn, run_id=rid, design_name="cordic", design_family="cordic",
                platform="nangate45", core_utilization=util,
                orfs_status="partial", drc_status=drc, lvs_status="clean",
                rcx_status="complete", timing_tier="met")
    # Insert edges out of chain order to prove the walk linearizes them. The
    # r2->r3 edge is inserted FIRST (lower id, later created_at) and r1->r2 second.
    _insert_lineage(conn, "cordic", "nangate45", "r3", "r2",
                    {"changed": {"CORE_UTILIZATION": {"old": "25", "new": "30"}},
                     "added": {}, "removed": {}},
                    created_at="2026-05-02T00:00:00Z")
    _insert_lineage(conn, "cordic", "nangate45", "r2", "r1",
                    {"changed": {"CORE_UTILIZATION": {"old": "20", "new": "25"}},
                     "added": {"ROUTING_LAYER_ADJUSTMENT": "0.1"}, "removed": {}},
                    created_at="2026-05-01T00:00:00Z")
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)
    prov = view["provenance"]
    assert len(prov) == 1
    entry = prov[0]
    assert entry["design_name"] == "cordic"
    assert entry["platform"] == "nangate45"
    assert entry["edge_count"] == 2

    edges = entry["edges"]
    # Ordered prev->cur: r1->r2 first, then r2->r3 (root walk, not insert order).
    assert edges[0]["previous_run_id"] == "r1"
    assert edges[0]["current_run_id"] == "r2"
    assert edges[1]["previous_run_id"] == "r2"
    assert edges[1]["current_run_id"] == "r3"

    # diff parsed.
    assert edges[0]["diff"]["changed"]["CORE_UTILIZATION"]["new"] == "25"
    assert edges[0]["diff"]["added"] == {"ROUTING_LAYER_ADJUSTMENT": "0.1"}

    # outcome_delta populated [prev, cur] for the four fields.
    od = edges[0]["outcome_delta"]
    assert od["orfs_status"] == ["partial", "partial"]
    assert od["drc_status"] == ["clean", "clean_beol"]
    assert od["lvs_status"] == ["clean", "clean"]
    assert od["timing_tier"] == ["met", "met"]


def test_provenance_missing_run_row_yields_none(tmp_knowledge_dir):
    """An edge whose run row is absent surfaces None outcome fields, no raise."""
    conn, db_path = _open_db(tmp_knowledge_dir)
    _insert(conn, run_id="present", design_name="foo", design_family="foo",
            platform="nangate45", orfs_status="partial", drc_status="clean",
            lvs_status="clean", rcx_status="complete", timing_tier="met")
    # Insert a stub run for the FK then point the edge's previous at a deleted-
    # style absent id by referencing a run we don't add to run_outcomes lookup.
    # (FK requires real rows; use 'present' as previous and 'present' as a
    #  second run for current.)
    _insert(conn, run_id="present2", design_name="foo", design_family="foo",
            platform="nangate45", orfs_status="partial", drc_status="clean",
            lvs_status="clean", rcx_status="complete", timing_tier="met")
    _insert_lineage(conn, "foo", "nangate45", "present2", "present",
                    {"changed": {}, "added": {"X": "1"}, "removed": {}},
                    created_at="2026-05-01T00:00:00Z")
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)
    entry = view["provenance"][0]
    od = entry["edges"][0]["outcome_delta"]
    assert od["orfs_status"] == ["partial", "partial"]


def test_build_view_does_not_modify_db(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    _seed(conn)
    conn.commit()
    conn.close()

    mtime_before = db_path.stat().st_mtime_ns
    # Calling build_view must not write to the DB.
    build_lineage_view.build_view(db_path)
    build_lineage_view.build_view(db_path)
    assert db_path.stat().st_mtime_ns == mtime_before


def test_empty_db_health(tmp_knowledge_dir):
    conn, db_path = _open_db(tmp_knowledge_dir)
    conn.commit()
    conn.close()
    view = build_lineage_view.build_view(db_path)
    h = view["health"]
    assert h["total_runs"] == 0
    assert h["orfs_status_counts"] == {}
    assert h["pct_partial_or_unknown"] == 0.0
    assert h["learnable_pairs"] == 0
    assert view["provenance"] == []


def test_provenance_fanout_and_cycle_no_drop_no_hang(tmp_knowledge_dir):
    """Non-linearizable lineage (fan-out + cycle) must not drop, dup, or hang.

    Production ChipTop is a fan-out (multiple currents share one root), so this
    exercises the _order_edges fallback the clean-chain test never reaches.
    """
    conn, db_path = _open_db(tmp_knowledge_dir)
    # Fan-out + continuation in design "fan": r0->r1, r0->r2, r2->r3.
    for rid in ("r0", "r1", "r2", "r3"):
        _insert(conn, run_id=rid, design_name="fan", design_family="fan",
                platform="nangate45", orfs_status="partial", drc_status="clean",
                lvs_status="clean", rcx_status="complete", timing_tier="met")
    _insert_lineage(conn, "fan", "nangate45", "r1", "r0",
                    {"changed": {}, "added": {"A": "1"}, "removed": {}},
                    created_at="2026-05-01T00:00:00Z")
    _insert_lineage(conn, "fan", "nangate45", "r2", "r0",
                    {"changed": {}, "added": {"B": "1"}, "removed": {}},
                    created_at="2026-05-02T00:00:00Z")
    _insert_lineage(conn, "fan", "nangate45", "r3", "r2",
                    {"changed": {}, "added": {"C": "1"}, "removed": {}},
                    created_at="2026-05-03T00:00:00Z")

    # A 2-edge cycle in a separate design: a->b, b->a. Must return both, no hang.
    for rid in ("a", "b"):
        _insert(conn, run_id=rid, design_name="cyc", design_family="cyc",
                platform="nangate45", orfs_status="partial", drc_status="clean",
                lvs_status="clean", rcx_status="complete", timing_tier="met")
    _insert_lineage(conn, "cyc", "nangate45", "b", "a",
                    {"changed": {}, "added": {"X": "1"}, "removed": {}},
                    created_at="2026-05-01T00:00:00Z")
    _insert_lineage(conn, "cyc", "nangate45", "a", "b",
                    {"changed": {}, "added": {"Y": "1"}, "removed": {}},
                    created_at="2026-05-02T00:00:00Z")
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)
    by_design = {p["design_name"]: p for p in view["provenance"]}

    fan = by_design["fan"]
    assert fan["edge_count"] == 3
    fan_pairs = [(e["previous_run_id"], e["current_run_id"]) for e in fan["edges"]]
    # Every edge appears exactly once — no drop, no duplicate.
    assert sorted(fan_pairs) == sorted([("r0", "r1"), ("r0", "r2"), ("r2", "r3")])
    assert len(fan_pairs) == len(set(fan_pairs)) == 3

    cyc = by_design["cyc"]
    assert cyc["edge_count"] == 2
    cyc_pairs = {(e["previous_run_id"], e["current_run_id"]) for e in cyc["edges"]}
    assert cyc_pairs == {("a", "b"), ("b", "a")}


def test_render_helpers_escape_html(tmp_knowledge_dir):
    """Lock in HTML escaping: hostile design_name / diff values must be escaped."""
    conn, db_path = _open_db(tmp_knowledge_dir)
    hostile_name = "evil<script>"
    hostile_val = '\'"><svg onload=alert(1)>'
    for rid in ("p", "c"):
        _insert(conn, run_id=rid, design_name=hostile_name,
                design_family="evil", platform="nangate45",
                orfs_status="partial", drc_status="clean", lvs_status="clean",
                rcx_status="complete", timing_tier="met")
    _insert_lineage(conn, hostile_name, "nangate45", "c", "p",
                    {"changed": {"CORE_UTILIZATION": {"old": "20", "new": hostile_val}},
                     "added": {}, "removed": {}},
                    created_at="2026-05-01T00:00:00Z")
    conn.commit()
    conn.close()

    view = build_lineage_view.build_view(db_path)

    prov_html = dashboard.tuning_provenance_panel(view["provenance"])
    health_html = dashboard.knowledge_health_strip(view["health"])

    for raw in ("<script>", "<svg onload"):
        assert raw not in prov_html, f"unescaped {raw!r} leaked into provenance HTML"
        assert raw not in health_html, f"unescaped {raw!r} leaked into health HTML"
    # Positive check: the escaped forms are present where the hostile data flows.
    assert "&lt;script&gt;" in prov_html
    assert "&lt;svg onload" in prov_html
