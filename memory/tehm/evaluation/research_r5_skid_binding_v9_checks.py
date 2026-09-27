"""Bounded DEV-only v9 source-binding checks on explicit input files."""
from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
from pathlib import Path
import re
import socket
import unittest
from unittest.mock import patch

from . import research_r5_skid_binding_v9 as binder


CLEAN = ''
FAULT = ''
NEGATIVE = ''
ASSET = {'binding_template': binder.TEMPLATE}
CLEAN_SHA = '3addbc1532e66facaa38d3ecb6303e6dd919dc765cd3e8bc9bb3854d04e0031c'
FAULT_SHA = 'da50a68d67a4fb1a2197a749ef1c3649bce34875edd45bca22a58f18037bf66b'


class Checks(unittest.TestCase):
    def bind(self, source: str | None = None, context: dict | None = None) -> dict:
        return binder.bind_one_process_drain_v9(
            ASSET, FAULT if source is None else source,
            binder.CONTEXT if context is None else context)

    def test_unique_bind_and_exact_replay(self):
        binding = self.bind()
        self.assertEqual((binding['status'], binding['reason']),
                         ('BOUND', 'unique_captured_payload_drain_mismatch'))
        span = binding['witness']['rhs_span']
        self.assertEqual(FAULT[span[0]:span[1]], 's_data')
        candidate, receipt = binder.apply_bound_one_process_drain_v9(
            ASSET, FAULT, binder.CONTEXT, binding)
        self.assertEqual(candidate, CLEAN)
        self.assertEqual(receipt['rewritten'], 1)
        self.assertTrue(receipt['source_binding_rederived'])
        self.assertFalse(receipt['memory_authority_granted'])
        self.assertEqual(receipt['functional_verdict'], 'NOT_EVALUATED')

    def test_clean_idempotence(self):
        result = self.bind(CLEAN)
        self.assertEqual((result['status'], result['reason']),
                         ('NO_MATCH', 'drain_already_uses_captured_payload'))
        with self.assertRaises(ValueError):
            binder.apply_bound_one_process_drain_v9(ASSET, CLEAN, binder.CONTEXT, result)

    def test_absence_multiple_modules_and_extra_writer(self):
        self.assertEqual(self.bind('// no module')['status'], 'NO_MATCH')
        self.assertEqual(self.bind(FAULT + '\n' + CLEAN)['status'], 'AMBIGUOUS')
        extra = FAULT.replace('endmodule', 'always_ff @(posedge clk) m_data <= s_data;\nendmodule')
        self.assertEqual(self.bind(extra)['status'], 'UNSUPPORTED')

    def test_public_context_exactness(self):
        bad = ({'WIDTH': 16, 'defined_macros': []},
               {'WIDTH': True, 'defined_macros': []},
               {'WIDTH': 8.0, 'defined_macros': []},
               {'WIDTH': 8, 'defined_macros': ['FORMAL']},
               {'WIDTH': 8, 'defined_macros': ()},
               {'WIDTH': 8, 'defined_macros': [], 'gold': 'private'},
               {})
        for context in bad:
            with self.subTest(context=context):
                self.assertEqual(self.bind(context=context)['status'], 'UNSUPPORTED')
        self.assertEqual(binder.bind_one_process_drain_v9(
            {'binding_template': {}}, FAULT, binder.CONTEXT)['status'], 'UNSUPPORTED')

    def test_non_target_shape_and_unsupported_rhs(self):
        self.assertEqual(self.bind(NEGATIVE)['status'], 'UNSUPPORTED')
        changed = FAULT.replace('m_data     <= s_data;',
                                'm_data     <= m_data;', 1)
        self.assertEqual(self.bind(changed)['status'], 'UNSUPPORTED')
        aliased = re.sub(r'\bskid_data\b', 's_data', FAULT)
        self.assertEqual(self.bind(aliased)['status'], 'AMBIGUOUS')

    def test_comment_invariance_and_lexical_rejection(self):
        annotated = '// misleading answer: m_data <= skid_data\n' + FAULT
        self.assertEqual(self.bind(annotated)['status'], 'BOUND')
        for changed in (FAULT.replace('module stream_skid', '`include "gold.sv"\nmodule stream_skid'),
                        FAULT.replace('endmodule', 'initial $display("gold");\nendmodule'),
                        FAULT + '\x00'):
            self.assertEqual(self.bind(changed)['status'], 'UNSUPPORTED')

    def test_alpha_renaming_is_not_transfer(self):
        binding = self.bind()
        original = binding['witness']['roles']
        replacements = {value: f'role_{index}' for index, (role, value) in
                        enumerate(original.items()) if role != 'RHS'}
        renamed = re.sub(r'\b[A-Za-z_]\w*\b',
                         lambda match: replacements.get(match[0], match[0]), FAULT)
        result = self.bind(renamed)
        self.assertEqual(result['status'], 'BOUND')
        candidate, _ = binder.apply_bound_one_process_drain_v9(
            ASSET, renamed, binder.CONTEXT, result)
        self.assertEqual(self.bind(candidate)['status'], 'NO_MATCH')

    def test_stale_and_tampered_binding(self):
        original = self.bind()
        for key, value in (('rhs_span', [0, 1]), ('replacement_rhs', 'gold'),
                           ('source_sha256', 'wrong')):
            changed = copy.deepcopy(original)
            changed['witness'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                binder.apply_bound_one_process_drain_v9(
                    ASSET, FAULT, binder.CONTEXT, changed)
        with self.assertRaises(ValueError):
            binder.apply_bound_one_process_drain_v9(
                ASSET, FAULT + '\n', binder.CONTEXT, original)

    def test_no_filesystem_or_network_side_channel(self):
        def denied(*_args, **_kwargs):
            raise AssertionError('binder attempted prohibited I/O')
        with patch.object(builtins, 'open', denied), patch.object(Path, 'open', denied), \
                patch.object(Path, 'read_text', denied), patch.object(Path, 'read_bytes', denied), \
                patch.object(socket, 'socket', denied):
            self.assertEqual(self.bind()['status'], 'BOUND')


def run_checks(clean: Path, fault: Path, negative: Path) -> dict:
    global CLEAN, FAULT, NEGATIVE
    CLEAN, FAULT, NEGATIVE = clean.read_text(), fault.read_text(), negative.read_text()
    if hashlib.sha256(CLEAN.encode()).hexdigest() != CLEAN_SHA:
        raise ValueError('APEX DEV clean source drift')
    if hashlib.sha256(FAULT.encode()).hexdigest() != FAULT_SHA:
        raise ValueError('APEX DEV fault source drift')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Checks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return {'valid': result.wasSuccessful(), 'case_count': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'role': 'DEV_ONLY', 'heldout_transfer': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean', type=Path, required=True)
    parser.add_argument('--fault', type=Path, required=True)
    parser.add_argument('--negative', type=Path, required=True)
    args = parser.parse_args()
    result = run_checks(args.clean, args.fault, args.negative)
    print(result)
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
