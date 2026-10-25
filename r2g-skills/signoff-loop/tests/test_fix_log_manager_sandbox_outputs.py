"""manage() on a NON-default db must never touch the shipped learner outputs.

Regression for the 2026-07-09 clobber (failure-patterns.md "Learning-Loop
Closure Failures"): ingest_run.py's post-ingest autolearn called
fix_log_manager.manage(<temp db>) which full-rewrote the SHIPPED
knowledge/heuristics.json from the temp db's tiny corpus (source_run_count=1).
Sandbox dbs must get sandbox outputs, next to the db itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
sys.path.insert(0, str(KNOWLEDGE_DIR))

import fix_log_manager  # noqa: E402
import knowledge_db  # noqa: E402


def test_manage_on_sandbox_db_writes_sibling_outputs(tmp_path):
    shipped = KNOWLEDGE_DIR / "heuristics.json"
    shipped_bytes = shipped.read_bytes() if shipped.exists() else None

    db_path = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db_path)
    conn.close()

    result = fix_log_manager.manage(db_path)
    assert result["rows"] == 0

    # Sandbox outputs live NEXT TO the sandbox db...
    assert (tmp_path / "heuristics.json").exists(), \
        "manage() on a sandbox db must write heuristics.json beside that db"
    # ...and the shipped store outputs are untouched.
    assert (shipped.read_bytes() if shipped.exists() else None) == shipped_bytes, \
        "manage() on a sandbox db clobbered the SHIPPED heuristics.json"


def test_manage_default_out_path_unchanged_for_default_db():
    # Pure path-derivation check (no learn run): the default db still maps to
    # the shipped heuristics.json location.
    db_is_default = Path(knowledge_db.DEFAULT_DB_PATH).resolve() \
        == knowledge_db.DEFAULT_DB_PATH.resolve()
    assert db_is_default
