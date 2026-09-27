"""Offline rejection tests for the v8 raw TRAIN consumer (not new RTL trials)."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tehm.assets import r5_train_raw_v8 as raw


class SealChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tehm-v8-raw-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'package'
        self.root.mkdir()
        (self.root / 'receipt.json').write_text('{}')
        (self.root / 'audit.py').write_text('# fixture, never executed\n')
        self.pin = {'receipt': raw._sha(self.root / 'receipt.json'),
                    'auditor': 'audit.py', 'auditor_sha': raw._sha(self.root / 'audit.py')}
        self.seal = {'files': {p.name: raw._sha(p) for p in self.root.iterdir()},
                     'recovery_receipt_sha256': self.pin['receipt']}
        self.reseal()

    def reseal(self):
        (self.root / 'seal.json').write_text(json.dumps(self.seal, sort_keys=True))
        self.pin['seal'] = raw._sha(self.root / 'seal.json')

    def test_intact_seal(self):
        self.assertEqual(raw._sealed(self.root, self.pin), self.seal)

    def test_file_tamper(self):
        (self.root / 'audit.py').write_text('# different\n')
        with self.assertRaisesRegex(ValueError, 'inventory drift'):
            raw._sealed(self.root, self.pin)

    def test_missing_file(self):
        (self.root / 'audit.py').unlink()
        with self.assertRaisesRegex(ValueError, 'inventory drift'):
            raw._sealed(self.root, self.pin)

    def test_extra_file(self):
        (self.root / 'unregistered').write_text('x')
        with self.assertRaisesRegex(ValueError, 'inventory drift'):
            raw._sealed(self.root, self.pin)

    def test_seal_tamper_without_external_pin(self):
        (self.root / 'seal.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'seal identity'):
            raw._sealed(self.root, self.pin)

    def test_linked_file_or_directory(self):
        for target in (self.root / 'audit.py', self.root):
            alias = self.root / 'alias'
            alias.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'linked evidence'):
                raw._sealed(self.root, self.pin)
            alias.unlink()

    def test_linked_root_and_ancestor(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'noncanonical package root'):
            raw._sealed(alias, self.pin)
        ancestor = Path(self.temp.name) / 'parent_alias'
        ancestor.symlink_to(Path(self.temp.name))
        with self.assertRaisesRegex(ValueError, 'noncanonical package root'):
            raw._sealed(ancestor / 'package', self.pin)

    def test_unsafe_inventory_paths(self):
        original = deepcopy(self.seal)
        for name in ('../escape', '/absolute', './audit.py', 'seal.json', 'a/../audit.py'):
            self.seal = deepcopy(original)
            self.seal['files'][name] = '0' * 64
            self.reseal()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unsafe inventory path'):
                raw._sealed(self.root, self.pin)

    def test_receipt_pin_is_external(self):
        self.pin['receipt'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'receipt identity'):
            raw._sealed(self.root, self.pin)

    def test_auditor_pin_is_external(self):
        self.pin['auditor_sha'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'auditor identity'):
            raw._sealed(self.root, self.pin)


class ContractChecks(unittest.TestCase):
    def verify_fixture(self, kind, *, replay=None, saved=None):
        expected = raw._expected(kind)
        replay = replay if replay is not None else {'original': expected, 'recovery': expected}
        saved = saved if saved is not None else expected
        with patch.object(raw, '_sealed') as seal, patch.object(raw, '_replay', return_value=replay), \
                patch.object(raw, '_read', side_effect=lambda p: {'audit': saved} if p.name == 'receipt.json' else saved):
            result = raw._verify_package(Path('/unit-fixture'), kind, raw.PACKAGES[kind])
            self.assertEqual(seal.call_count, 2)
            return result

    def test_registered_shapes(self):
        for kind in raw.PACKAGES:
            self.assertEqual(self.verify_fixture(kind), raw._expected(kind))

    def test_raw_failure_cannot_be_hidden_by_saved_pass(self):
        for kind in raw.PACKAGES:
            expected = raw._expected(kind)
            bad = deepcopy(expected)
            bad['component_training_obligations_pass'] = False
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'disagreement'):
                self.verify_fixture(kind, replay={'original': bad, 'recovery': expected})

    def test_recovery_must_replay_too(self):
        expected = raw._expected('mux')
        bad = deepcopy(expected)
        bad['matrix']['rollback']['target'] = 'UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'disagreement'):
            self.verify_fixture('mux', replay={'original': expected, 'recovery': bad})

    def test_saved_scope_changes_rejected(self):
        changes = {'role': 'FINAL_TEST', 'memory_mremove': True, 'model_calls': 1,
                   'oracle_executions': True, 'source_components': 3,
                   'zipcpu_native_payload_oracle': 'PASS', 'memory_authority_granted': True}
        for key, value in changes.items():
            saved = raw._expected('axis_zip')
            saved[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'disagreement'):
                self.verify_fixture('axis_zip', saved=saved)

    def test_counter_and_boolean_are_not_equal(self):
        self.assertFalse(raw._equal({'count': 1}, {'count': True}))
        self.assertFalse(raw._equal({'grant': False}, {'grant': 0}))

    def test_evidence_drift_after_replay_rejected(self):
        expected = raw._expected('mux')
        with patch.object(raw, '_sealed', side_effect=[{}, ValueError('drift after replay')]), \
                patch.object(raw, '_replay', return_value={'original': expected, 'recovery': expected}), \
                patch.object(raw, '_read', side_effect=lambda p: {'audit': expected} if p.name == 'receipt.json' else expected):
            with self.assertRaisesRegex(ValueError, 'drift after replay'):
                raw._verify_package(Path('/unit-fixture'), 'mux', raw.PACKAGES['mux'])

    def test_no_replay_before_seal_validation(self):
        with patch.object(raw, '_sealed', side_effect=ValueError('bad pin')), patch.object(raw, '_replay') as replay:
            with self.assertRaises(ValueError):
                raw._verify_package(Path('/unit-fixture'), 'mux', raw.PACKAGES['mux'])
            replay.assert_not_called()

    def test_replay_failure_timeout_malformed_result(self):
        failures = [subprocess.CompletedProcess([], 1, b'', b'failed'),
                    subprocess.CompletedProcess([], 0, b'{}', b''),
                    subprocess.CompletedProcess([], 0, b'not JSON', b''),
                    subprocess.CompletedProcess([], 0, b'{}', b'unexpected warning')]
        for result in failures:
            with patch.object(raw.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                raw._replay(Path('/unit-fixture'), 'mux', raw.PACKAGES['mux'])
        with patch.object(raw.subprocess, 'run', side_effect=subprocess.TimeoutExpired('audit', 90)):
            with self.assertRaisesRegex(ValueError, 'timeout'):
                raw._replay(Path('/unit-fixture'), 'mux', raw.PACKAGES['mux'])

    def test_replay_command_is_isolated_readonly_and_unoptimized(self):
        result = subprocess.CompletedProcess([], 0, b'{"original":{},"recovery":{}}', b'')
        with patch.object(raw.subprocess, 'run', return_value=result) as run:
            raw._replay(Path('/unit-fixture'), 'mux', raw.PACKAGES['mux'])
        argv = run.call_args.args[0]
        self.assertIn('--unshare-all', argv)
        self.assertIn('--clearenv', argv)
        self.assertNotIn('--bind', argv)
        self.assertEqual(argv[-6:-3], ['-I', '-B', '-c'])
        self.assertEqual(argv[-2:], ['mux', 'recovery-bundle/audit.py'])
        self.assertEqual(run.call_args.kwargs['timeout'], 90)

    def test_no_independence_or_authority_from_task_counts(self):
        self.assertEqual(len(raw.CASE_ORDER), 3)
        for kind in raw.PACKAGES:
            self.assertIs(raw._expected(kind)['memory_authority_granted'], False)
        self.assertEqual(raw._expected('axis_zip')['zipcpu_native_payload_oracle'], 'MISSED')


if __name__ == '__main__':
    unittest.main()
