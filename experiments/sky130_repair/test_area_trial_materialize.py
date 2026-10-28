"""Isolated parser/ingestion regression fixtures, never physical win evidence."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from area_trial_evidence import classify
from area_trial_materialize import materialize, REPO

sys.path.insert(0, str(REPO/'r2g-skills/signoff-loop/knowledge'))
import knowledge_db
import ingest_run


class MaterializeTests(unittest.TestCase):
    def prepare(self, root, signed):
        project = root/'subject'
        (project/'reports').mkdir(parents=True)
        (project/'constraints').mkdir()
        (project/'constraints/config.mk').write_text('export DESIGN_NAME = subject\nexport PLATFORM = sky130hd\n')
        old = project/'backend/RUN_FROZEN_ROUTE_FAILURE'
        old.mkdir(parents=True)
        (old/'stage_log.jsonl').write_text('{"stage":"route","status":"fail"}\n')
        run = project/'physical_run'
        (run/'logs/sky130hd/subject/base').mkdir(parents=True)
        result = dict(status='pass' if signed else 'fail', stage='6_finish' if signed else '5_1_grt',
                      design='subject', drc_status='clean' if signed else None)
        (run/'result.json').write_text(json.dumps(result))
        (run/'flow.log').write_text('finish' if signed else '[ERROR GRT-0118] Routing congestion too high.')
        if signed:
            (run/'6_final.gds').write_bytes(b'parser fixture, not a physical layout')
            (run/'6_drc.lyrdb').write_text('<report-database><items/></report-database>')
            (run/'netgen_lvs.rpt').write_text('Final result: Circuits match uniquely.\n')
        rc = 0 if signed else 2
        return project, dict(run=str(run), flow_returncode=rc, acceptance=classify(run, rc))

    def test_ingestion_preserves_failure_and_avoids_stale_ledger(self):
        for signed in (False, True):
            with self.subTest(signed=signed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, execution = self.prepare(root, signed)
                materialize(project, execution)
                materialize(project, execution)
                conn = knowledge_db.connect(root/'isolated.sqlite')
                knowledge_db.ensure_schema(conn)
                rid = ingest_run.ingest(project, conn)
                cur = conn.execute('SELECT * FROM runs WHERE run_id=?', (rid,))
                row = dict(zip([c[0] for c in cur.description], cur.fetchone()))
                self.assertEqual(knowledge_db.is_success(row), signed)
                if not signed:
                    self.assertEqual(row['orfs_status'], 'fail')
                    self.assertFalse((project/'reports/drc.json').exists())
                    self.assertFalse((project/'reports/lvs.json').exists())
                else:
                    self.assertNotEqual(row['orfs_status'], 'fail')
                    self.assertEqual((project/'backend/RUN_Z_FRESH_AB/stage_log.jsonl').read_text(), '')
                self.assertIn('"fail"', (project/'backend/RUN_FROZEN_ROUTE_FAILURE/stage_log.jsonl').read_text())
                conn.close()

    def test_changed_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, execution = self.prepare(Path(tmp), True)
            materialize(project, execution)
            (project/'reports/lvs.json').write_text('{"status":"fail"}')
            with self.assertRaisesRegex(ValueError, 'ingest reports'):
                materialize(project, execution)

    def test_inconclusive_does_not_create_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, execution = self.prepare(Path(tmp), False)
            execution['flow_returncode'] = 137
            execution['acceptance'] = classify(execution['run'], 137)
            with self.assertRaises(ValueError):
                materialize(project, execution)
            self.assertFalse((project/'backend/RUN_Z_FRESH_AB').exists())


if __name__ == '__main__':
    unittest.main()
