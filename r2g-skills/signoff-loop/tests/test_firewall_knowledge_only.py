"""Knowledge-only-learner firewall gate (CLAUDE.md "Closed Learning Loop").

The learner/inference/reporting path must read ONLY knowledge.sqlite + heuristics.json
and NEVER journal.sqlite / journal_db — so a fresh clone (which ships knowledge.sqlite
but gitignores journal.sqlite) behaves identically. This gate fails CI if any of the
four learner/inference files grows a journal dependency.

SCOPING IS LOAD-BEARING: an unscoped "no file imports journal_db" assert would
FALSE-FAIL — there are LEGITIMATE both-DB readers (knowledge_db.py, observe.py,
engineer_loop.py, ingest_run.py, ab_runner.py, escalations.py, and journal_db.py
itself, which since the 2026-07-18 merge carries the producer CLI + summarizer):
the loop driver, the ingest path, and the provenance/forensics tools rightly touch
both DBs (README inv 18 — both arm a busy_timeout so concurrent ingests wait, not
error). So this gate is scoped EXACTLY to the LEARNER_FILES set; the ALLOWLIST
documents the legit set we deliberately do NOT police here.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]

# The four learner/inference files that MUST stay journal-blind. learn_heuristics.py +
# mine_rules.py roll knowledge rows into heuristics.json; suggest_config.py +
# diagnose_signoff_fix.py are the runtime inference readers. (suggest_config.py lives in
# knowledge/, not scripts/reports/.)
LEARNER_FILES = [
    _SKILL_ROOT / "knowledge" / "learn_heuristics.py",
    _SKILL_ROOT / "knowledge" / "mine_rules.py",
    _SKILL_ROOT / "knowledge" / "suggest_config.py",
    _SKILL_ROOT / "scripts" / "reports" / "diagnose_signoff_fix.py",
]

# Legit both-DB readers — NOT policed by this gate (documented so a future reader does
# not mistake the scoping for an oversight). README inv 18: knowledge_db / journal_db
# both arm a busy_timeout so a concurrent ingest waits instead of erroring; the loop
# driver + ingest + forensics tools legitimately read/write both DBs.
ALLOWLIST = {
    "knowledge_db.py",        # the shared DB helper (defines connect/busy_timeout)
    "observe.py",             # operator forensics: joins knowledge + journal
    "engineer_loop.py",       # the autonomous driver: journals every action
    "ingest_run.py",          # back-fills run_id onto journal rows at ingest
    "ab_runner.py",           # records A/B trials + journals ab_launch
    "escalations.py",         # opens escalations + journals
    "journal_db.py",          # the journal module itself (lib + summarizer + CLI)
}


def _imports_journal_db(source: str) -> bool:
    """True if `source` imports journal_db (robust: parse the AST import nodes, so a
    journal_db mention inside a comment/string does not false-positive)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "journal_db" or alias.name.endswith(".journal_db")
                   for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "journal_db" or mod.endswith(".journal_db"):
                return True
    return False


def _writes_journal_sqlite(source: str) -> bool:
    """True if `source` references the journal.sqlite filename literally (a direct
    open/write path that bypasses the journal_db import — the other way to breach)."""
    return "journal.sqlite" in source


def test_learner_files_exist():
    """Guard: a renamed/moved learner file must not silently drop out of the gate."""
    for f in LEARNER_FILES:
        assert f.exists(), f"learner file missing (gate would silently pass): {f}"


def test_learner_files_do_not_import_journal_db():
    offenders = []
    for f in LEARNER_FILES:
        src = f.read_text(encoding="utf-8")
        if _imports_journal_db(src):
            offenders.append(f"{f.name}: imports journal_db")
        if _writes_journal_sqlite(src):
            offenders.append(f"{f.name}: references journal.sqlite directly")
    assert not offenders, (
        "knowledge-only-learner firewall breached — these inference/learner files "
        "touch the journal: " + "; ".join(offenders))


def test_learner_set_is_disjoint_from_allowlist():
    """Sanity: the policed learner set and the legit both-DB allowlist must not
    overlap — if a file were in both we would be policing a known both-DB reader (a
    guaranteed false-fail) or whitelisting a learner (a hole in the gate)."""
    learner_names = {f.name for f in LEARNER_FILES}
    assert learner_names.isdisjoint(ALLOWLIST), (
        "learner file also appears in the both-DB ALLOWLIST: "
        f"{learner_names & ALLOWLIST}")


def test_detector_actually_detects_journal_import():
    """The detector must be ABLE to fire (not theater): a synthetic source that
    imports journal_db / writes journal.sqlite must be flagged; a clean one must not."""
    assert _imports_journal_db("import journal_db\n") is True
    assert _imports_journal_db("from journal_db import connect\n") is True
    assert _imports_journal_db("import knowledge_db\n") is False
    # a mention only inside a string/comment must NOT trip the AST detector.
    assert _imports_journal_db("x = 'journal_db is off-limits'  # journal_db\n") is False
    assert _writes_journal_sqlite("open('journal.sqlite')\n") is True
    assert _writes_journal_sqlite("open('knowledge.sqlite')\n") is False
