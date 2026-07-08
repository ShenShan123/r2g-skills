"""Tests for backfill_fix_events.py — mining historical fix transitions from
design_cases/_batch/*.jsonl into synthetic fix_events.

The real batch record shapes (confirmed against design_cases/_batch on
2026-06-06) are:
  antenna_fix_*.jsonl : {design, inst, status, before, after, wall_s}
  beol_drc_*.jsonl    : {design, inst, status, violations, drc_mode, wall_s}
  retry_pass*.jsonl   : {case, design, platform?, orfs, elapsed_s, from_stage, timeout}
  recover_pass*.jsonl : {case, orfs, elapsed_s, timeout, from_stage?, ...}  (no `design`)
  orfs_retry*.jsonl   : {case, design, platform, orfs, elapsed_s}
"""
from __future__ import annotations

import json

import knowledge_db
import backfill_fix_events


def _setup_conn(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "knowledge.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_backfill_antenna_fix(tmp_path, tmp_knowledge_dir):
    batch = tmp_path / "_batch"
    batch.mkdir()
    # Real antenna_fix record shape: design/before/after/status (no platform/strategy).
    (batch / "antenna_fix_2026.jsonl").write_text(
        json.dumps({"design": "verilog_ethernet_eth_demux", "inst": 1331,
                    "status": "fail", "before": 147, "after": 3, "wall_s": 567}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    row = conn.execute(
        "SELECT check_type, verdict, provenance, design_family, "
        "before_count, after_count, fix_session_id "
        "FROM fix_events").fetchone()
    assert row[0] == "drc"
    assert row[1] == "win"   # after=3 (>0) and < before -> win, not cleared
    assert row[2].startswith("backfill:antenna_fix")
    assert row[3]            # a family was inferred
    assert row[4] == 147 and row[5] == 3
    assert row[6]            # stable session id present
    conn.close()


def test_backfill_resolves_platform_from_config_mk(tmp_path, tmp_knowledge_dir):
    # A DRC record carries no platform; the design dir's config.mk does. The
    # backfill must use the real platform (so recipes land in the live bucket,
    # not 'unknown'), and fall back to the skill default when no config.mk exists.
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "antenna_fix_plat.jsonl").write_text(
        json.dumps({"design": "skywater_thing", "inst": 1, "status": "clean",
                    "before": 5, "after": 0, "wall_s": 10}) + "\n"
        + json.dumps({"design": "no_config_thing", "inst": 2, "status": "clean",
                      "before": 3, "after": 0, "wall_s": 10}) + "\n")
    # cases_root == batch.parent == tmp_path. Give the first design a config.mk.
    cfg = tmp_path / "skywater_thing" / "constraints"
    cfg.mkdir(parents=True)
    (cfg / "config.mk").write_text(
        "export DESIGN_NAME = skywater_thing\nexport PLATFORM = sky130hd\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    backfill_fix_events.backfill(batch, conn, fams)
    plats = dict(conn.execute(
        "SELECT design_name, platform FROM fix_events").fetchall())
    assert plats["skywater_thing"] == "sky130hd"     # resolved from config.mk
    assert plats["no_config_thing"] == "asap7"   # skill-default fallback
    assert None not in plats.values()                # never NULL
    conn.close()


def test_backfill_antenna_cleared(tmp_path, tmp_knowledge_dir):
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "antenna_fix_clean.jsonl").write_text(
        json.dumps({"design": "iccad2017_unit2_G", "inst": 2542,
                    "status": "clean", "before": 7, "after": 0, "wall_s": 387}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    verdict = conn.execute("SELECT verdict FROM fix_events").fetchone()[0]
    assert verdict == "cleared"   # after == 0
    conn.close()


def test_backfill_retry_pass_orfs(tmp_path, tmp_knowledge_dir):
    """retry_pass*/recover_pass*/orfs_retry -> check=orfs, violation_class=from_stage."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "retry_pass4.jsonl").write_text(
        json.dumps({"case": "verilog_ethernet_axis_baser_rx_64",
                    "design": "axis_baser_rx_64", "orfs": "pass", "elapsed_s": 572,
                    "timeout": 7200, "from_stage": "full"}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    row = conn.execute(
        "SELECT check_type, violation_class, from_stage, verdict, provenance, platform "
        "FROM fix_events").fetchone()
    assert row[0] == "orfs"
    assert row[1] == "full"          # violation_class from from_stage
    assert row[2] == "full"
    assert row[3] == "cleared"       # orfs == pass -> the run closed
    assert row[4].startswith("backfill:retry_pass")
    assert row[5] == "asap7"     # platform defaults to skill default when absent


def test_backfill_orfs_no_session_collision_on_shared_design(tmp_path, tmp_knowledge_dir):
    """Two orfs records with the same non-unique design='top' but distinct `case`
    dir-basenames must yield two distinct fix_events (no session-id collision)."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "orfs_retry5.jsonl").write_text(
        json.dumps({"case": "iccad2015_unit12_in1", "design": "top",
                    "platform": "nangate45", "orfs": "pass", "elapsed_s": 410}) + "\n" +
        json.dumps({"case": "iccad2015_unit18_in1", "design": "top",
                    "platform": "nangate45", "orfs": "pass", "elapsed_s": 530}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 2                    # both records survive UNIQUE(session,iter,strategy)
    sids = [r[0] for r in conn.execute(
        "SELECT fix_session_id FROM fix_events").fetchall()]
    assert len(set(sids)) == 2       # distinct session ids keyed on `case`
    conn.close()


def test_backfill_orfs_timeout_from_stage_sanitized(tmp_path, tmp_knowledge_dir):
    """A numeric/unknown from_stage (a leaked timeout) -> violation_class='full',
    from_stage=None — not a junk recipe bucket like fix_recipes['orfs']['14400']."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "retry_pass3.jsonl").write_text(
        json.dumps({"case": "wb2axip_axilsafety", "design": "axilsafety",
                    "platform": "nangate45", "orfs": "pass", "elapsed_s": 990,
                    "timeout": 14400, "from_stage": "14400"}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    row = conn.execute(
        "SELECT violation_class, from_stage FROM fix_events").fetchone()
    assert row[0] == "full"          # bogus stage replaced
    assert row[1] is None            # leaked timeout dropped
    conn.close()


def test_backfill_orfs_family_from_case(tmp_path, tmp_knowledge_dir):
    """design_family follows the canonical rule _explicit_family(design) or
    infer_family(case). For a koios record {case:koios_dla_like, design:myproject}
    the short module has no explicit family, so the family must come from the unique
    `case` dir-basename ('koios') — not the junk family 'myproject' the old code
    produced from the short top-module name (#5/#1)."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "orfs_retry9.jsonl").write_text(
        json.dumps({"case": "koios_dla_like",
                    "design": "myproject", "platform": "nangate45",
                    "orfs": "pass", "elapsed_s": 300}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    # Guard the premise: short module has no explicit family and the two ids
    # diverge, so this exercises the case-based inference path.
    assert backfill_fix_events._explicit_family("myproject", fams) is None
    assert (knowledge_db.infer_family("koios_dla_like", fams)
            != knowledge_db.infer_family("myproject", fams))
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    fam = conn.execute("SELECT design_family FROM fix_events").fetchone()[0]
    assert fam == knowledge_db.infer_family("koios_dla_like", fams)
    assert fam == "koios"
    conn.close()


def test_backfill_orfs_explicit_design_family_wins(tmp_path, tmp_knowledge_dir):
    """Canonical rule precedence: a curated DESIGN_NAME mapping/pattern still wins
    over the dir-basename, mirroring the live loop (ingest_run._project_family).
    Here `^udp_` -> 'udp' on the short module beats infer_family(case)='verilog'."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "orfs_retry7.jsonl").write_text(
        json.dumps({"case": "verilog_ethernet_udp_complete",
                    "design": "udp_complete", "platform": "nangate45",
                    "orfs": "pass", "elapsed_s": 300}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert backfill_fix_events._explicit_family("udp_complete", fams) == "udp"
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    fam = conn.execute("SELECT design_family FROM fix_events").fetchone()[0]
    assert fam == "udp"          # explicit family on DESIGN_NAME wins (canonical)
    conn.close()


def test_backfill_beol_drc(tmp_path, tmp_knowledge_dir):
    """beol_drc_* -> check=drc; `violations` is the after-count, status clean_beol."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "beol_drc_2026.jsonl").write_text(
        json.dumps({"design": "wb2axip_axilsafety", "inst": 2443,
                    "status": "clean_beol", "violations": 0, "drc_mode": "beol_only",
                    "wall_s": 33}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    row = conn.execute(
        "SELECT check_type, verdict, after_count, provenance FROM fix_events").fetchone()
    assert row[0] == "drc"
    assert row[1] == "cleared"       # violations == 0
    assert row[2] == 0
    assert row[3].startswith("backfill:beol_drc")
    conn.close()


def test_backfill_session_stable_and_idempotent(tmp_path, tmp_knowledge_dir):
    """Same design+file -> stable fix_session_id; re-running INSERTs nothing new."""
    batch = tmp_path / "_batch"
    batch.mkdir()
    (batch / "antenna_fix_x.jsonl").write_text(
        json.dumps({"design": "iccad2017_unit2_G", "before": 7, "after": 0,
                    "status": "clean"}) + "\n")
    conn = _setup_conn(tmp_knowledge_dir)
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n1 = backfill_fix_events.backfill(batch, conn, fams)
    sid1 = conn.execute("SELECT fix_session_id FROM fix_events").fetchone()[0]
    n2 = backfill_fix_events.backfill(batch, conn, fams)  # re-run
    rows = conn.execute("SELECT fix_session_id FROM fix_events").fetchall()
    assert n1 == 1
    assert len(rows) == 1          # INSERT OR IGNORE on re-run
    assert rows[0][0] == sid1      # stable session id
    conn.close()
