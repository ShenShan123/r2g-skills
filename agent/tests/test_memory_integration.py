"""Exercise the new agent -> existing skill -> TEHM seam with private stores."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from r2g_agent import knowledge
from r2g_agent.paths import repo_root
from r2g_agent.pipeline import Pipeline


def test_agent_defaults_to_private_tehm_without_seeding_legacy():
    e = knowledge.env()
    assert e['R2G_MEMORY_BACKEND'] == 'tehm'
    assert 'R2G_KNOWLEDGE_DB' not in e
    assert str(knowledge.r2g_home()) in e['TEHM_DB']
    assert knowledge.ensure()['action'] == 'ready'
    assert Path(e['TEHM_DB']).is_file()
    assert not knowledge.paths()['db'].exists()
    assert knowledge.learn() == (True, 'TEHM rebuilt during ingest')


def test_backend_is_validated_and_cannot_switch(monkeypatch):
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'typo')
    with pytest.raises(ValueError, match='invalid'):
        knowledge.env()
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'none')
    assert knowledge.env() == {'R2G_MEMORY_BACKEND': 'none'}
    assert knowledge.ensure()['action'] == 'disabled'
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'tehm')
    with pytest.raises(RuntimeError, match='already opened'):
        knowledge.env()


def test_explicit_external_store_is_preserved(tmp_path, monkeypatch):
    db, art = tmp_path / 'external/t.sqlite', tmp_path / 'objects'
    monkeypatch.setenv('TEHM_DB', str(db))
    monkeypatch.setenv('TEHM_ARTIFACTS_ROOT', str(art))
    assert knowledge.env()['TEHM_DB'] == str(db)
    assert knowledge.env()['TEHM_ARTIFACTS_ROOT'] == str(art)
    knowledge.ensure()
    assert db.is_file()


def _project(tmp_path):
    p = tmp_path / 'design'
    (p / 'constraints').mkdir(parents=True)
    (p / 'reports').mkdir()
    (p / 'constraints/config.mk').write_text('export DESIGN_NAME = test\nexport PLATFORM = nangate45\nexport CORE_UTILIZATION = 40\n')
    row = {'fix_session_id': 'integration', 'iter': 1, 'check': 'drc', 'strategy': 'density_relief',
           'violation_class': 'metal', 'from_stage': 'floorplan', 'before': 5, 'after': 5,
           'verdict': 'no_improvement', 'rolled_back': False, 'config_delta': {'CORE_UTILIZATION': '30'}}
    (p / 'reports/fix_log.jsonl').write_text(json.dumps(row) + '\n')
    return p


def test_actual_ingest_cli_is_idempotent_and_does_not_open_legacy(tmp_path):
    project = _project(tmp_path)
    knowledge.ensure()
    env = {**os.environ, **knowledge.env()}
    script = repo_root() / 'r2g-skills/signoff-loop/knowledge/ingest_run.py'
    for _ in range(2):
        result = subprocess.run([sys.executable, str(script), str(project)], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'memory_backend=tehm' in result.stdout
    with sqlite3.connect(env['TEHM_DB']) as conn:
        assert conn.execute('SELECT count(*) FROM tehm_transitions').fetchone()[0] == 1
    assert not knowledge.paths()['db'].exists()


@pytest.mark.parametrize('backend', ['none', 'tehm', 'legacy'])
def test_pipeline_ingest_command_matches_backend(backend, tmp_path, monkeypatch):
    monkeypatch.setenv('R2G_MEMORY_BACKEND', backend)
    p = Pipeline.__new__(Pipeline)
    p.ingest, p.memory_env = True, knowledge.env()
    commands = []
    p._sh = lambda *a, **k: commands.append(a[3]) or 0
    p._tail = lambda *a: ''
    monkeypatch.setattr(knowledge, 'learn', lambda: (True, 'ok'))
    p._ingest('job', 'design', tmp_path)
    assert ('--db' in commands[0]) == (backend == 'legacy')


def test_diagnose_subprocess_uses_same_backend(tmp_path):
    project = _project(tmp_path)
    knowledge.ensure()
    env = {**os.environ, **knowledge.env()}
    script = repo_root() / 'r2g-skills/signoff-loop/scripts/reports/diagnose_signoff_fix.py'
    result = subprocess.run([sys.executable, str(script), str(project), '--check', 'drc'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['memory_backend'] == 'tehm'
    assert not knowledge.paths()['db'].exists()


def test_frozen_store_is_not_modified_by_agent_startup(monkeypatch):
    import hashlib
    knowledge.ensure()
    db = knowledge.tehm_paths()['db']
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setenv('R2G_MEMORY_READ_ONLY_EVAL', '1')
    assert knowledge.ensure()['read_only'] is True
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_mcp_v1_tool_registration_contract():
    from r2g_agent.mcp_server import build
    assert build().name == 'r2g'


def test_clean_legacy_checkout_initializes_empty_user_store(monkeypatch):
    monkeypatch.setenv('R2G_MEMORY_BACKEND', 'legacy')
    assert not knowledge.shipped_store()[0].exists()
    assert knowledge.ensure()['action'] == 'initialized'
    with sqlite3.connect(knowledge.paths()['db']) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
        conn.execute('CREATE TABLE user_marker(value TEXT)')
        conn.execute("INSERT INTO user_marker VALUES ('keep')")
    assert knowledge.ensure()['action'] == 'up_to_date'
    with sqlite3.connect(knowledge.paths()['db']) as conn:
        assert conn.execute('SELECT value FROM user_marker').fetchone()[0] == 'keep'
    assert knowledge.learn()[0]
