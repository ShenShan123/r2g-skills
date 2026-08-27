"""Phase-1 acceptance: legacy golden equivalence + process-level firewall."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SUGGEST = REPO / "r2g-skills" / "signoff-loop" / "knowledge" / "suggest_config.py"
INGEST = REPO / "r2g-skills" / "signoff-loop" / "knowledge" / "ingest_run.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "project_clean_run"


def _env(**updates):
    env = dict(os.environ)
    for key in ("R2G_MEMORY_BACKEND", "R2G_MEMORY_READ_ONLY_EVAL",
                "TEHM_DB", "TEHM_ARTIFACTS_ROOT"):
        env.pop(key, None)
    env.update({key: str(value) for key, value in updates.items()})
    return env


def test_explicit_legacy_suggest_is_byte_golden_with_default():
    default = subprocess.run(
        [sys.executable, str(SUGGEST), str(FIXTURE)],
        capture_output=True, env=_env(), timeout=30)
    explicit = subprocess.run(
        [sys.executable, str(SUGGEST), str(FIXTURE)],
        capture_output=True, env=_env(R2G_MEMORY_BACKEND="legacy"), timeout=30)
    assert default.returncode == explicit.returncode == 0
    assert default.stdout == explicit.stdout
    assert default.stderr == explicit.stderr


def _logical_dump(path: Path) -> dict:
    conn = sqlite3.connect(path)
    out = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        rows = []
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            normalized = []
            for col, value in zip(cols, row):
                if col in {"ts", "created_at", "updated_at", "ingested_at"}:
                    value = "<timestamp>"
                elif isinstance(value, str):
                    value = re.sub(
                        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.+-][^\" ]+)?",
                        "<timestamp>", value)
                normalized.append(value)
            rows.append(normalized)
        out[table] = sorted(rows, key=repr)
    conn.close()
    return out


def test_explicit_legacy_ingest_is_logically_golden_with_default(tmp_path):
    db_default, db_explicit = tmp_path / "default.sqlite", tmp_path / "explicit.sqlite"
    common = {"R2G_FIX_AUTOLEARN": "0"}
    default = subprocess.run(
        [sys.executable, str(INGEST), str(FIXTURE), "--db", str(db_default)],
        capture_output=True, text=True, env=_env(**common), timeout=30)
    explicit = subprocess.run(
        [sys.executable, str(INGEST), str(FIXTURE), "--db", str(db_explicit)],
        capture_output=True, text=True,
        env=_env(R2G_MEMORY_BACKEND="legacy", **common), timeout=30)
    assert default.returncode == explicit.returncode == 0
    assert _logical_dump(db_default) == _logical_dump(db_explicit)


def _audit_run(script: Path, argv: list[str], env: dict, log: Path):
    wrapper = r'''
import atexit, json, os, runpy, sys
seen = []
def hook(event, args):
    if event == "open" and args:
        p = args[0]
        if isinstance(p, (str, bytes, os.PathLike)):
            seen.append(os.fspath(p))
sys.addaudithook(hook)
atexit.register(lambda: open(os.environ["AUDIT_LOG"], "w").write(json.dumps(seen)))
sys.argv = [os.environ["TARGET_SCRIPT"], *json.loads(os.environ["TARGET_ARGV"])]
sys.path.insert(0, os.path.dirname(os.environ["TARGET_SCRIPT"]))
runpy.run_path(os.environ["TARGET_SCRIPT"], run_name="__main__")
'''
    audited = dict(env)
    audited.update({"AUDIT_LOG": str(log), "TARGET_SCRIPT": str(script),
                    "TARGET_ARGV": json.dumps(argv)})
    return subprocess.run([sys.executable, "-c", wrapper], capture_output=True,
                          text=True, env=audited, timeout=60)


def test_tehm_process_never_opens_legacy_authority(tmp_path):
    audit = tmp_path / "tehm-open.json"
    forbidden_db = tmp_path / "legacy-sentinel.sqlite"
    result = _audit_run(
        INGEST, [str(FIXTURE), "--db", str(forbidden_db)],
        _env(R2G_MEMORY_BACKEND="tehm",
             TEHM_DB=tmp_path / "tehm.sqlite",
             TEHM_ARTIFACTS_ROOT=tmp_path / "artifacts",
             R2G_TEHM_ROOT=REPO / "memory", R2G_FIX_AUTOLEARN="0"), audit)
    assert result.returncode == 0, result.stderr
    opened = [str(p) for p in json.loads(audit.read_text())]
    authority = [p for p in opened
                 if p.endswith("knowledge.sqlite") or p.endswith("heuristics.json")
                 or p == str(forbidden_db)]
    assert authority == []
    assert not forbidden_db.exists()


def test_legacy_process_never_opens_tehm_authority(tmp_path):
    audit = tmp_path / "legacy-open.json"
    tehm_sentinel = tmp_path / "tehm-sentinel.sqlite"
    tehm_sentinel.write_bytes(b"must not open")
    result = _audit_run(
        SUGGEST, [str(FIXTURE)],
        _env(R2G_MEMORY_BACKEND="legacy", TEHM_DB=tehm_sentinel), audit)
    assert result.returncode == 0, result.stderr
    opened = [str(p) for p in json.loads(audit.read_text())]
    assert str(tehm_sentinel) not in opened
