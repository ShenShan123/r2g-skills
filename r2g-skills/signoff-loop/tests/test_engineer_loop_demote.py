"""engineer_loop `demote` CLI verb (Workstream C).

The contradiction probe emits a paste-ready `demote` command; this is the verb
it pastes into. It flips a promoted recipe_status row to 'shadow' and records the
operator reason in provenance. Never auto-applied — operator-invoked only.
"""
from __future__ import annotations

from pathlib import Path

import engineer_loop
import knowledge_db


def _seed_promoted(conn, *, symptom_id, design_class, platform, strategy):
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, provenance, generation, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (symptom_id, design_class, platform, strategy, "promoted",
         "ab_trial:1", 1, "2026-06-18T00:00:00Z"))
    conn.commit()


def test_demote_cli_flips_promoted_to_shadow(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed_promoted(conn, symptom_id="deadbeef00000001",
                   design_class="logic/small", platform="sky130hd",
                   strategy="density_relief")
    conn.close()

    # The CLI handler opens knowledge_db.connect() (no path) -> DEFAULT_DB_PATH.
    monkeypatch.setattr(knowledge_db, "DEFAULT_DB_PATH", db, raising=False)

    rc = engineer_loop.main([
        "demote",
        "--symptom", "deadbeef00000001",
        "--design-class", "logic/small",
        "--platform", "sky130hd",
        "--strategy", "density_relief",
        "--reason", "structural contradiction with util_raise on CORE_UTILIZATION",
    ])
    assert rc == 0

    conn = knowledge_db.connect(db)
    row = conn.execute(
        "SELECT status, provenance FROM recipe_status WHERE symptom_id=? AND "
        "design_class=? AND platform=? AND strategy=?",
        ("deadbeef00000001", "logic/small", "sky130hd", "density_relief")
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "shadow"
    assert "structural contradiction" in row[1]


def test_demote_cli_records_reason_for_absent_grandfathered(tmp_path, monkeypatch):
    """Demoting a grandfathered (absent-row) recipe creates a shadow row carrying
    the reason — the operator can shadow a recipe that never had an explicit row."""
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    conn.close()
    monkeypatch.setattr(knowledge_db, "DEFAULT_DB_PATH", db, raising=False)

    rc = engineer_loop.main([
        "demote",
        "--symptom", "cafebabe00000002",
        "--design-class", "unknown/unknown",
        "--platform", "nangate45",
        "--strategy", "period_relax",
        "--reason", "structural contradiction with clock_uncertainty on CLOCK_PERIOD",
    ])
    assert rc == 0
    conn = knowledge_db.connect(db)
    row = conn.execute(
        "SELECT status, provenance FROM recipe_status WHERE symptom_id=? AND "
        "strategy=?", ("cafebabe00000002", "period_relax")).fetchone()
    conn.close()
    assert row[0] == "shadow"
    assert "structural contradiction" in row[1]
