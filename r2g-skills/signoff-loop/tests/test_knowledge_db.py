"""Tests for knowledge_db module: schema bootstrap and family inference."""
from __future__ import annotations

import knowledge_db


def test_connect_sets_busy_timeout(tmp_knowledge_dir):
    """The campaign runs a pool of ingest subprocesses against one DB; without a
    busy_timeout a concurrent writer fails instantly with 'database is locked'
    and the driver swallows it, silently dropping a run. connect() must arm a
    nonzero busy_timeout so writers wait-and-retry instead (parity with
    journal_db.connect). See repair_run_status / campaign honesty."""
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy >= 30000
    conn.close()


def test_ensure_schema_creates_tables(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"runs", "failure_events", "config_lineage"}.issubset(names)
    conn.close()


def test_ensure_schema_is_idempotent(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()


def test_ensure_schema_backfills_null_flow_scope(tmp_knowledge_dir):
    """Legacy rows (pre-2026-07-09 schema-only migration) carry NULL flow_scope.

    The runs.flow_scope contract is 'full' | 'synth_only' (knowledge README
    invariant 33). NULL is benign only while no reader filters ='full' — a
    latent silent-drop for any future one. ensure_schema must backfill
    NULL/'' → 'full' (every pre-flow_scope row was a full-flow run) while
    never touching an explicit 'synth_only'.
    """
    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.execute(
        "INSERT INTO runs (run_id, project_path, ingested_at, flow_scope) VALUES "
        "('legacy1', '/p/legacy1', '2026-07-01T00:00:00+00:00', NULL), "
        "('legacy2', '/p/legacy2', '2026-07-01T00:00:00+00:00', ''), "
        "('synth1', '/p/synth1', '2026-07-09T00:00:00+00:00', 'synth_only')")
    conn.commit()

    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    rows = dict(conn.execute("SELECT run_id, flow_scope FROM runs").fetchall())
    assert rows == {"legacy1": "full", "legacy2": "full", "synth1": "synth_only"}
    conn.close()


def test_ensure_schema_merges_legacy_quoted_symptom_ids(tmp_knowledge_dir):
    """Pre-2026-07-04 symptoms stored KLayout classes verbatim ("'m3.2'"), minting
    ids that fragment the index against post-normalization rows ('m3.2'). The
    promoted density_relief recipe was stranded under the legacy quoted id while
    new occurrences keyed the canonical id (failure-patterns #28). ensure_schema
    must re-key legacy rows to the canonical id, re-point every dependent, and on
    a recipe_status collision keep the judged/terminal state.
    """
    import symptom as sym

    db_path = tmp_knowledge_dir / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    legacy_sig = {"check": "drc", "class": "'m3.2'", "predicates": {}}
    canon_sig = {"check": "drc", "class": "m3.2", "predicates": {}}
    legacy_id, canon_id = sym.symptom_id(legacy_sig), sym.symptom_id(canon_sig)

    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class, predicates_json, "
                 "symptom_schema_version, first_seen) VALUES (?, 'drc', ?, '{}', 1, "
                 "'2026-06-13T00:00:00Z')", (legacy_id, "'m3.2'"))
    conn.execute("INSERT INTO symptoms (symptom_id, check_type, class, predicates_json, "
                 "symptom_schema_version, first_seen) VALUES (?, 'drc', 'm3.2', '{}', 1, "
                 "'2026-07-04T00:00:00Z')", (canon_id,))
    conn.execute("INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
                 "verdict, ts) VALUES (?, 'm/3', 'sky130hd', 'density_relief', 'win', "
                 "'2026-07-01T00:00:00Z')", (legacy_id,))
    # collision: promoted under legacy must displace the canonical candidate
    conn.execute("INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
                 "status, updated_at) VALUES (?, 'm/3', 'sky130hd', 'density_relief', "
                 "'promoted', '2026-07-01T00:00:00Z')", (legacy_id,))
    conn.execute("INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
                 "status, updated_at) VALUES (?, 'm/3', 'sky130hd', 'density_relief', "
                 "'candidate', '2026-07-05T00:00:00Z')", (canon_id,))
    # no-collision: a legacy-only row is simply re-keyed
    conn.execute("INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
                 "status, updated_at) VALUES (?, 's/1', 'sky130hd', 'density_relief', "
                 "'candidate', '2026-07-01T00:00:00Z')", (legacy_id,))
    conn.commit()

    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")

    # legacy symptom row merged away; canonical keeps the earliest first_seen
    syms = dict(conn.execute("SELECT symptom_id, first_seen FROM symptoms").fetchall())
    assert legacy_id not in syms
    assert syms[canon_id] == "2026-06-13T00:00:00Z"
    # dependents re-pointed
    assert conn.execute("SELECT COUNT(*) FROM ab_trials WHERE symptom_id=?",
                        (legacy_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ab_trials WHERE symptom_id=?",
                        (canon_id,)).fetchone()[0] == 1
    # collision resolved: promoted (judged) wins over candidate (queue)
    rows = conn.execute("SELECT status FROM recipe_status WHERE symptom_id=? AND "
                        "design_class='m/3'", (canon_id,)).fetchall()
    assert rows == [("promoted",)]
    # no-collision row re-keyed intact
    rows = conn.execute("SELECT status FROM recipe_status WHERE symptom_id=? AND "
                        "design_class='s/1'", (canon_id,)).fetchall()
    assert rows == [("candidate",)]
    assert conn.execute("SELECT COUNT(*) FROM recipe_status WHERE symptom_id=?",
                        (legacy_id,)).fetchone()[0] == 0
    conn.close()


def test_infer_family_direct_mapping(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("aes128_core", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("RocketTile", families) == "tinyRocket"


def test_infer_family_pattern_fallback(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("aes_new_variant", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("bp_something", families) == "bp_multi_top"


def test_infer_family_unknown_returns_first_token(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("foobar_top", families) == "foobar"


# --- is_success: the shared learnable-success predicate --------------------

def test_is_success_strict_pass():
    assert knowledge_db.is_success({
        "orfs_status": "pass", "drc_status": "clean",
        "lvs_status": "clean", "rcx_status": "complete",
    })


def test_is_success_relaxed_positive_lvs_clean():
    # partial run, but LVS clean (positive signal) and nothing failed
    assert knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": None,
        "lvs_status": "clean", "rcx_status": None,
    })


def test_is_success_all_none_is_false():
    # No positive signoff signal anywhere — absence is not success.
    assert not knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": None,
        "lvs_status": None, "rcx_status": None,
    })


def test_is_success_failed_lvs_is_false():
    assert not knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean",
        "lvs_status": "incomplete", "rcx_status": "complete",
    })
    assert not knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean",
        "lvs_status": "fail", "rcx_status": "complete",
    })


def test_is_success_symmetric_matcher_is_true():
    assert knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean",
        "lvs_status": "fail", "lvs_mismatch_class": "symmetric_matcher",
        "rcx_status": "complete",
    })


def test_is_success_symmetric_matcher_only_on_fail_verdict():
    # Defensive: symmetric_matcher is only a clean-layout signal on a 'fail'
    # verdict. If a future path sets it on an incomplete/crash LVS, the real
    # failure must NOT leak through as a success.
    assert not knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean",
        "lvs_status": "incomplete", "lvs_mismatch_class": "symmetric_matcher",
        "rcx_status": "complete",
    })


def test_is_success_clean_beol_is_true():
    assert knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean_beol",
        "lvs_status": "clean", "rcx_status": "complete",
    })


# --- families.json curation -------------------------------------------------

def test_curated_families_map_dominant_ip_prefixes(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    infer = lambda n: knowledge_db.infer_family(n, families)
    # Anchored (^prefix_) pins: every underscore-separated IP design maps via
    # the curated family. AXI-stream / AXI-lite must NOT be swallowed by ^axi_.
    assert infer("axis_fifo") == "axis"
    assert infer("axil_crossbar") == "axil"
    assert infer("axi_crossbar") == "axi"
    assert infer("axi_register") == "axi"
    # IGNORECASE: ^i2c_ matches I2C_master, ^spi_ matches SPI_Master.
    assert infer("I2C_master") == "i2c"
    assert infer("SPI_Master") == "spi"
    assert infer("eth_mac_1g") == "eth"
    assert infer("uart_tx") == "uart"
    # We intentionally do NOT over-split: both AXI bus designs share family
    # 'axi' (bus_heavy behavior is handled by suggest_config's clamp).
    assert infer("axi_crossbar") == infer("axi_register") == "axi"
    # Conservative anchoring: ambiguous run-together names fall through to the
    # honest split('_')[0] singleton fallback instead of being force-merged.
    assert infer("spider") == "spider"          # NOT "spi"
    assert infer("axildouble") == "axildouble"  # NOT "axil"


# --- Task 1: generalize column migration to multiple tables -----------------

def test_migrate_adds_columns_to_multiple_tables(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fe_cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_events)")}
    rv_cols = {r[1] for r in conn.execute("PRAGMA table_info(run_violations)")}
    ft_cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_trajectories)")}
    assert {"symptom_id", "signature_json"} <= fe_cols
    assert {"symptom_id", "signature_json"} <= rv_cols
    assert {"symptom_id", "signature_json"} <= ft_cols
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()


# --- Task 2: symptoms table + indexes ---------------------------------------

def test_symptoms_table_and_indexes_exist(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(symptoms)")}
    assert {"symptom_id", "check_type", "class", "predicates_json",
            "symptom_schema_version", "first_seen"} <= cols
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_symptoms_check_class" in idx
    assert "idx_fix_events_symptom" in idx
    assert "idx_run_violations_symptom" in idx
    assert "idx_fix_traj_symptom" in idx
    conn.close()


# --- Fmax (feat/fmax-search merge 2026-06-19): the per-stage setup-slack columns
# migrate onto a legacy DB. The legacy fixture is the FULL base schema with ONLY
# those 3 columns stripped — a realistic pre-slack-column store (base columns like
# design_family/design_name/platform, which schema.sql indexes, are present). A
# 3-column toy table would instead trip HEAD's executescript on the runs indexes;
# HEAD's ensure_schema treats base columns as always-present and only ALTER-adds
# genuinely-new columns via _ADDED_COLUMNS["runs"]. ------------------------------

def test_staged_slack_columns_exist_after_ensure_schema(tmp_knowledge_dir):
    """ensure_schema must ALTER the three per-stage setup-slack columns into a
    runs.sqlite created before they existed (live forward migration)."""
    import re
    schema = (tmp_knowledge_dir / "schema.sql").read_text(encoding="utf-8")
    legacy_ddl = re.sub(
        r"^\s*(?:floorplan_setup_ws|place_setup_ws|finish_setup_ws)\b.*\n",
        "", schema, flags=re.M)
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    conn.executescript(legacy_ddl)
    pre = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert not ({"floorplan_setup_ws", "place_setup_ws", "finish_setup_ws"} & pre), \
        "legacy fixture must NOT already carry the slack columns"
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert {"floorplan_setup_ws", "place_setup_ws", "finish_setup_ws"} <= cols
    conn.close()


# --- heuristics.json read API (folded in from query_knowledge.py, 2026-07-18) ---

def _write_heur(tmp_knowledge_dir, payload: dict):
    import json
    (tmp_knowledge_dir / "heuristics.json").write_text(json.dumps(payload))


def test_get_family_heuristics_hit(tmp_knowledge_dir):
    _write_heur(tmp_knowledge_dir, {
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10, "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 30, "median": 25},
                        "place_density_lb_addon": {"min_safe": 0.15, "median": 0.20},
                    },
                },
            },
        },
    })
    result = knowledge_db.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is not None
    assert result["core_utilization"]["median"] == 25
    assert result["sample_size"] == 10


def test_get_family_heuristics_miss(tmp_knowledge_dir):
    _write_heur(tmp_knowledge_dir, {"families": {}})
    result = knowledge_db.get_family_heuristics(
        "nonexistent", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None


def test_get_family_heuristics_no_file(tmp_knowledge_dir):
    result = knowledge_db.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None


def test_get_deterioration_and_closing_period(tmp_path):
    import json as _json
    h = tmp_path / "heuristics.json"
    h.write_text(_json.dumps({"families": {"alu": {"platforms": {"nangate45": {
        "closing_period": {"min": 7.4, "median": 8.5, "n": 4},
        "slack_deterioration": {"d_fp_pl": {"ns_p90": 0.3, "pct_p90": 0.03},
                                "d_pl_fin": {"ns_p90": 0.2, "pct_p90": 0.02},
                                "n": 4},
    }}}}}), encoding="utf-8")
    assert knowledge_db.get_closing_period("alu", "nangate45", heuristics_path=h)["min"] == 7.4
    assert knowledge_db.get_deterioration("alu", "nangate45", heuristics_path=h)["n"] == 4
    assert knowledge_db.get_deterioration("nope", "nangate45", heuristics_path=h) is None


def test_now_local_stamps_offset():
    """Invariant 32: the ONE canonical stamp is system-local with numeric offset."""
    import datetime as _dt
    ts = knowledge_db.now_local()
    assert not ts.endswith("Z")
    parsed = _dt.datetime.fromisoformat(ts)
    assert parsed.utcoffset() is not None
