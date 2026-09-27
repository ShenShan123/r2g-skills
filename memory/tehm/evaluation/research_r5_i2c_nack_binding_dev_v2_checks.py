"""Adversarial source-only DEV checks for I2C NACK binding v2."""
from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from . import research_r5_i2c_nack_binding_dev_v2 as binder


EXPECTED = {
    'alex_clean': '9c4e05759f356e9ddd91af6981a1fcc10466cad0af6e5afb6553787fe9ea0328',
    'alex_fault': 'b4951f32e3387cf2a9984999defb66e49c7f2fa3832ea9a39bd8555c25d7accf',
    'zip_clean': '3d8c2f9bc704a4f71bd028e951246c00b6361d68154124261245c53734e12b29',
    'zip_fault': 'e9add7fdc8d33939b8abc693a096a589be0417f0baab7e8be2f0fa32c379afb2',
    'top_clean': '0382530c50abfede70ad5788d68b8a65272b189fe093e61c5635f63ccaa58577',
    'top_fault': '29562e912ae644ec61e5af9e1341d1c42b73868f8c0911622ecd0d71f3aa00ff',
    'byte_ctrl': '5e084e3cfcdd30ab7efe9e83dc2202d87a093a2cbe157c2efef0a6de25a45889',
    'bit_ctrl': '09e103a0abaef32a2666145166a51627682fbd050eb3bc64d4b0d8f94ab2fcfe',
    'defines': 'a20cc941c1fbc35f0d24a8283d4683046c0d896860c5ece0ab0715f76a0458ba',
}
SOURCES: dict[str, str] = {}
ASSET = {'binding_template': binder.TEMPLATE}
CTX = {'interface': binder.INTERFACE, 'parameters': {'ARST_LVL': 0}, 'defined_macros': []}
OLD = {
    'alex': {'interface': 'dedicated_missed_ack_output', 'defined_macros': []},
    'zip': {'interface': 'wishbone_status_err_bit_30', 'defined_macros': []},
}


def closure(top: str = 'top_fault') -> dict[str, str]:
    return {'top': SOURCES[top], **{r: SOURCES[r] for r in binder.ROLES if r != 'top'}}


class Checks(unittest.TestCase):
    def test_three_dev_faults_unique_and_candidate_exact(self):
        for family in ('alex', 'zip'):
            with self.subTest(family=family):
                source, context = SOURCES[family + '_fault'], OLD[family]
                binding = binder.bind(ASSET, source, context)
                self.assertEqual(binding['status'], 'BOUND')
                candidate, receipt = binder.apply(ASSET, source, context, binding)
                self.assertEqual(candidate, SOURCES[family + '_clean'])
                self.assertEqual(receipt['rewritten_spans'], 1)
                self.assertEqual(binder.bind(ASSET, candidate, context)['status'], 'NO_MATCH')
        source = closure()
        binding = binder.bind(ASSET, source, CTX)
        self.assertEqual(binding['status'], 'BOUND')
        candidate, receipt = binder.apply(ASSET, source, CTX, binding)
        self.assertEqual(candidate['top'], SOURCES['top_clean'])
        self.assertEqual(source, closure())
        self.assertEqual([r for r in source if source[r] != candidate[r]], ['top'])
        self.assertEqual(receipt['rewritten_spans'], 1)
        self.assertEqual(receipt['functional_verdict'], 'NOT_EVALUATED')
        self.assertFalse(receipt['memory_authority_granted'])
        self.assertEqual(binder.bind(ASSET, candidate, CTX)['status'], 'NO_MATCH')

    def test_no_match_and_repeat_rejected(self):
        self.assertEqual(binder.bind(ASSET, closure('top_clean'), CTX)['status'], 'NO_MATCH')
        self.assertEqual(binder.bind(ASSET, {**closure(), 'top':
                         '`include "i2c_master_defines.v"\n// no module'}, CTX)['status'], 'NO_MATCH')
        with self.assertRaises(ValueError):
            binder.apply(ASSET, closure('top_clean'), CTX,
                         binder.bind(ASSET, closure('top_clean'), CTX))

    def test_extra_writer_and_multiple_modules_ambiguous(self):
        extra = closure()
        extra['top'] = extra['top'].replace('endmodule', "rxack <= 1'b0;\nendmodule", 1)
        self.assertEqual(binder.bind(ASSET, extra, CTX)['status'], 'AMBIGUOUS')
        doubled = closure()
        doubled['top'] += '\nmodule decoy; endmodule\n'
        self.assertEqual(binder.bind(ASSET, doubled, CTX)['status'], 'AMBIGUOUS')
        extra_ack = closure()
        extra_ack['byte_ctrl'] = extra_ack['byte_ctrl'].replace('endmodule', "ack_out <= 1'b0;\nendmodule", 1)
        self.assertEqual(binder.bind(ASSET, extra_ack, CTX)['status'], 'UNSUPPORTED')

    def test_wrong_route_and_similar_non_target_rejected(self):
        wrong = closure()
        wrong['top'] = wrong['top'].replace("3'b100: wb_dat_o <= sr;",
                                            "3'b100: wb_dat_o <= cr;", 1)
        self.assertEqual(binder.bind(ASSET, wrong, CTX)['status'], 'UNSUPPORTED')
        wrong = closure()
        wrong['top'] = wrong['top'].replace('.ack_out  ( irxack', '.ack_out  ( i2c_busy', 1)
        self.assertEqual(binder.bind(ASSET, wrong, CTX)['status'], 'AMBIGUOUS')
        wrong = closure()
        wrong['top'] = wrong['top'].replace("rxack    <= 1'b0;", "rxack    <= 1'b1;", 1)
        self.assertEqual(binder.bind(ASSET, wrong, CTX)['status'], 'UNSUPPORTED')

    def test_context_closure_and_lexical_fail_closed(self):
        for bad in ({**CTX, 'parameters': {'ARST_LVL': 1}},
                    {**CTX, 'parameters': {'ARST_LVL': False}},
                    {**CTX, 'defined_macros': ['FORMAL']},
                    {**CTX, 'answer': 'clean'},
                    {**CTX, 'interface': []}):
            self.assertEqual(binder.bind(ASSET, closure(), bad)['status'], 'UNSUPPORTED')
        self.assertEqual(binder.bind({'binding_template': {}}, closure(), CTX)['status'], 'UNSUPPORTED')
        for role in binder.ROLES:
            bad = closure()
            del bad[role]
            self.assertEqual(binder.bind(ASSET, bad, CTX)['status'], 'UNSUPPORTED')
        for replacement in ('`include "answer.v"', '`ifdef FORMAL', '"secret"',
                            '"i2c_master_defines.v"', '\\escaped'):
            bad = closure()
            bad['top'] = replacement + '\n' + bad['top']
            self.assertEqual(binder.bind(ASSET, bad, CTX)['status'], 'UNSUPPORTED')
        bad = closure()
        bad['byte_ctrl'] += '\n`UNKNOWN_MACRO\n'
        self.assertEqual(binder.bind(ASSET, bad, CTX)['status'], 'UNSUPPORTED')

    def test_comments_tamper_and_closure_hashes(self):
        source = closure()
        binding = binder.bind(ASSET, source, CTX)
        for field, value in (('rhs_span', [0, 1]), ('replacement_rhs', 'gold'),
                             ('source_hashes', {}), ('action_digest', 'wrong')):
            changed = copy.deepcopy(binding)
            changed['witness'][field] = value
            with self.assertRaises(ValueError):
                binder.apply(ASSET, source, CTX, changed)
        changed = closure()
        changed['defines'] += '\n// drift\n'
        with self.assertRaises(ValueError):
            binder.apply(ASSET, changed, CTX, binding)
        changed = closure()
        changed['top'] = '// rxack <= irxack; answer decoy\n' + changed['top']
        self.assertEqual(binder.bind(ASSET, changed, CTX)['status'], 'BOUND')

    def test_no_file_or_network_side_channel(self):
        def denied(*_args, **_kwargs):
            raise AssertionError('binder attempted external I/O')
        source = closure()
        with patch.object(builtins, 'open', denied), patch.object(Path, 'open', denied), \
                patch.object(Path, 'read_text', denied), patch.object(Path, 'read_bytes', denied), \
                patch.object(socket, 'socket', denied):
            binding = binder.bind(ASSET, source, CTX)
            self.assertEqual(binding['status'], 'BOUND')
            candidate, _ = binder.apply(ASSET, source, CTX, binding)
            self.assertEqual(binder.bind(ASSET, candidate, CTX)['status'], 'NO_MATCH')


def run_checks(paths: dict[str, Path]) -> dict:
    global SOURCES
    SOURCES = {name: path.read_text(encoding='utf-8') for name, path in paths.items()}
    for name, source in SOURCES.items():
        if hashlib.sha256(source.encode()).hexdigest() != EXPECTED[name]:
            raise ValueError(f'{name} registered DEV source drift')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Checks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return {'valid': result.wasSuccessful(), 'case_count': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'role': 'DEV_ONLY', 'memory_authority_granted': False,
            'heldout_transfer': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in EXPECTED:
        parser.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    args = parser.parse_args()
    verdict = run_checks({name: getattr(args, name) for name in EXPECTED})
    print(verdict)
    raise SystemExit(0 if verdict['valid'] else 1)
