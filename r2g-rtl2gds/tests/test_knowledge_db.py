"""Tests for knowledge_db module: schema bootstrap and family inference."""
from __future__ import annotations

import knowledge_db


def test_ensure_schema_creates_tables(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"runs", "failure_events", "config_lineage"}.issubset(names)
    conn.close()


def test_ensure_schema_is_idempotent(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
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


def test_is_success_clean_beol_is_true():
    assert knowledge_db.is_success({
        "orfs_status": "partial", "drc_status": "clean_beol",
        "lvs_status": "clean", "rcx_status": "complete",
    })


# --- families.json curation -------------------------------------------------

def test_curated_families_map_dominant_ip_prefixes(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    infer = lambda n: knowledge_db.infer_family(n, families)
    # AXI-stream / AXI-lite must NOT be swallowed by the broader ^axi_ rule.
    assert infer("axis_fifo") == "axis"
    assert infer("axil_crossbar") == "axil"
    assert infer("axi_crossbar") == "axi"
    assert infer("axi_register") == "axi"
    # IGNORECASE: ^i2c matches I2C_master, ^spi matches SPI_Master.
    assert infer("I2C_master") == "i2c"
    assert infer("SPI_Master") == "spi"
    assert infer("eth_mac_1g") == "eth"
    # We intentionally do NOT over-split: both AXI bus designs share family
    # 'axi' (bus_heavy behavior is handled by suggest_config's clamp).
    assert infer("axi_crossbar") == infer("axi_register") == "axi"
