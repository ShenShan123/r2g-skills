"""Counterexamples for the isolated checker repair, without EDA or model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

SPEC = importlib.util.spec_from_file_location('checker_probe', Path(__file__).with_name('checker_probe.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class VerdictTests(unittest.TestCase):
    def test_valid_zero_drc(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'result.lyrdb'
            p.write_text('<report-database><categories/><items/></report-database>')
            self.assertEqual(M.drc_verdict(0, p, ''), {'status': 'clean', 'violations': 0})

    def test_count_items_not_value_coordinates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'result.lyrdb'
            p.write_text('<report-database><items><item><values><value>a</value><value>b</value></values></item></items></report-database>')
            self.assertEqual(M.drc_verdict(0, p, '')['violations'], 1)

    def test_drc_timeout_never_consumes_partial_xml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'result.lyrdb'; p.write_text('<report-database><items/></report-database>')
            self.assertEqual(M.drc_verdict(124, p, '')['status'], 'UNKNOWN')

    def test_rule_location_is_not_deadlock_proof(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(M.drc_verdict(1, Path(d)/'missing', 'ERROR: rule.lydrc:131')['reason'], 'checker_nonzero_exit')

    def test_malformed_or_wrong_drc_database_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'
            for text in ('<report-database><items>', '<unrelated/>', '<report-database/>'):
                p.write_text(text)
                self.assertEqual(M.drc_verdict(0, p, '')['status'], 'UNKNOWN')

    def test_crash_never_becomes_clean(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'; p.write_text('<report-database><items/></report-database>')
            self.assertEqual(M.drc_verdict(0, p, 'Signal number: 11')['status'], 'UNKNOWN')

    def test_lvs_requires_database_and_consistent_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'; p.write_text('lvsdb (\n status (match)\n)')
            self.assertEqual(M.lvs_verdict(0, p, 'CONGRATULATIONS! Netlists match.', {'status': 'clean', 'circuit_pairs': 1})['status'], 'clean')
            self.assertEqual(M.lvs_verdict(0, p, 'CONGRATULATIONS! Netlists match.')['status'], 'UNKNOWN')
            self.assertEqual(M.lvs_verdict(0, p, "ERROR : Netlists don't match")['status'], 'UNKNOWN')
            p.unlink()
            self.assertEqual(M.lvs_verdict(0, p, 'CONGRATULATIONS! Netlists match.')['status'], 'UNKNOWN')

    def test_lvs_true_mismatch_is_measured_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'; p.write_text('lvsdb (\n status (nomatch)\n)')
            self.assertEqual(M.lvs_verdict(0, p, "ERROR : Netlists don't match", {'status': 'fail', 'circuit_pairs': 1})['status'], 'fail')

    def test_lvs_writer_or_compare_fault_overrides_match(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'; p.write_text('lvsdb (\n status (match)\n)')
            for fault in ('Signal number: 11', 'dbLayoutVsSchematicWriter', 'compare raised exception:', 'report_lvs failed:', 'Internal error: dbNetlistCompareCore.cc'):
                self.assertEqual(M.lvs_verdict(0, p, 'CONGRATULATIONS! Netlists match.\n'+fault)['status'], 'UNKNOWN')

    def test_input_digest_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'x'; p.write_text('before'); pins={str(p): M.sha(p)}
            M.check_hashes(pins); p.write_text('after')
            with self.assertRaises(ValueError): M.check_hashes(pins)

    def test_output_cannot_be_inside_historical_episode(self):
        with self.assertRaises(ValueError): M.check_output(Path('/x/episodes/cell/probe'), [Path('/x/episodes/cell/input.gds')])

    def test_existing_output_is_never_reused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError): M.new_directory(Path(d))

    def test_atomic_receipt_is_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json'; M.atomic_json(p, {'actual_tokens': None, 'paper_data': False})
            self.assertIsNone(json.loads(p.read_text())['actual_tokens'])

    def test_supervisor_reports_timeout_and_reaps(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            result=M.bound_run([sys.executable,'-c','import time; time.sleep(30)'],p,p/'timeout.log',0.05)
            self.assertEqual(result['exit_code'],124)
            self.assertTrue(result['timed_out'])
            self.assertLess(result['wall_s'],5)

    def test_supervisor_preserves_nonzero_return(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            result=M.bound_run([sys.executable,'-c','raise SystemExit(9)'],p,p/'exit.log',5)
            self.assertEqual(result['exit_code'],9)
            self.assertFalse(result['timed_out'])


if __name__ == '__main__': unittest.main()
