"""Restore only memory visible at the original start of this one task."""
import hashlib
from pathlib import Path
import shutil
import sqlite3


def seed_task_history(attempt_root, cell):
    arm_root = Path(attempt_root) / 'hosts/swe-agent' / cell['arm']
    if arm_root.exists():
        raise ValueError('recovery cell already started; no implicit retry')
    arm_root.mkdir(parents=True)
    if cell['arm'] == 'native':
        return
    src = Path(cell['history_snapshot'])
    db = src / 'tehm.sqlite'
    if hashlib.sha256(db.read_bytes()).hexdigest() != cell['history_db_sha256']:
        raise ValueError('original task-start memory changed')
    (arm_root / 'memory').mkdir()
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('original task-start memory failed integrity check')
        with sqlite3.connect(arm_root / 'memory/tehm.sqlite') as dest:
            source.backup(dest)
    shutil.copytree(src / 'artifacts', arm_root / 'artifacts')
