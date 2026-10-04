"""Negative checks for the I2C v3 TRAIN lineage audit (each corruption must fail closed)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tehm.assets import r5_train_lineage_i2c_v3 as lineage

SCRATCH = Path('/data1/zhangdy/.cache/tmp')


def _copy_inputs(tmp: Path) -> Path:
    dest = tmp / 'inputs'
    shutil.copytree(lineage.INPUTS, dest)
    return dest


def _relog(inputs: Path, rel: str) -> None:
    for log in ('fetch-log.json', 'fetch-newest-log.json'):
        path = inputs / log
        rows = json.loads(path.read_text())
        for row in rows:
            if row['file'] == rel:
                row['sha256'] = hashlib.sha256((inputs / rel).read_bytes()).hexdigest()
        path.write_text(json.dumps(rows, indent=2, sort_keys=True) + '\n')


def _linked_train(tmp: Path) -> Path:
    """Hard-linked copy of the sealed TRAIN package (cheap; no symlinks)."""
    dest = tmp / 'train'
    for src in lineage.TRAIN.rglob('*'):
        target = dest / src.relative_to(lineage.TRAIN)
        if src.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(src), target)
        elif src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(src, target)
    return dest


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='tehm-lineage-i2c-checks-', dir=SCRATCH))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def assertRejects(self, reason, **kwargs):
        with self.assertRaises(ValueError) as ctx:
            lineage.audit(**kwargs)
        self.assertIn(reason, str(ctx.exception))

    def test_clean_passes(self):
        self.assertTrue(lineage.audit()['valid'])

    def test_tampered_input_rejected(self):
        inputs = _copy_inputs(self.tmp)
        path = inputs / 'alex/rtl__i2c_master.v.commits.p1.json'
        path.write_bytes(path.read_bytes().replace(b'Alex Forencich', b'Alex Forencicx', 1))
        self.assertRejects('fetched byte drift', inputs=inputs)

    def test_fork_flag_rejected(self):
        inputs = _copy_inputs(self.tmp)
        path = inputs / 'zip/repository.json'
        data = json.loads(path.read_text())
        data['fork'] = True
        path.write_text(json.dumps(data))
        _relog(inputs, 'zip/repository.json')
        self.assertRejects('repository identity/fork', inputs=inputs)

    def test_mutated_train_source_rejected(self):
        train = _linked_train(self.tmp)
        path = train / 'original/worker/zip.v'
        text = path.read_text()
        path.unlink()
        path.write_text(text + '\n// mutated\n')
        self.assertRejects('sealed TRAIN file drift', train=train)

    def test_injected_shared_window_flagged(self):
        alex = (lineage.TRAIN / 'original/worker/alex.v').read_text()
        top = (lineage.TRAIN / 'original/worker/freecores/top.v').read_text()
        body = [l for l in top.splitlines() if l.strip() and not l.strip().startswith('//')][40:80]
        row = lineage.compare(alex + '\n' + '\n'.join(body) + '\n', top)
        self.assertGreater(row['shared_substantive_8_line_windows'], 0)
        self.assertEqual(len(lineage.classify([row])), 1)
        clean = lineage.compare(alex, top)
        self.assertEqual(lineage.classify([clean]), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
