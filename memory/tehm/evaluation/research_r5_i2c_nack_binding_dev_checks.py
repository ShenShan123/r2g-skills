"""Registered I2C NACK DEV binding/action checks with explicit source inputs."""
from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from . import research_r5_i2c_nack_binding_dev as binder


EXPECTED = {
    'alex_clean': '9c4e05759f356e9ddd91af6981a1fcc10466cad0af6e5afb6553787fe9ea0328',
    'alex_fault': 'b4951f32e3387cf2a9984999defb66e49c7f2fa3832ea9a39bd8555c25d7accf',
    'zip_clean': '3d8c2f9bc704a4f71bd028e951246c00b6361d68154124261245c53734e12b29',
    'zip_fault': 'e9add7fdc8d33939b8abc693a096a589be0417f0baab7e8be2f0fa32c379afb2',
}
SOURCES: dict[str, str] = {}
ASSET = {'binding_template': binder.TEMPLATE}
CONTEXTS = {
    'alex': {'interface': 'dedicated_missed_ack_output', 'defined_macros': []},
    'zip': {'interface': 'wishbone_status_err_bit_30', 'defined_macros': []},
}


class Checks(unittest.TestCase):
    def bind(self, family: str, source: str | None = None, context: dict | None = None) -> dict:
        return binder.bind(ASSET, SOURCES[f'{family}_fault'] if source is None else source,
                           CONTEXTS[family] if context is None else context)

    def test_two_registered_faults_unique_and_exact_candidate(self):
        for family in ('alex', 'zip'):
            with self.subTest(family=family):
                source = SOURCES[f'{family}_fault']
                binding = self.bind(family)
                self.assertEqual(binding['status'], 'BOUND')
                candidate, receipt = binder.apply(ASSET, source, CONTEXTS[family], binding)
                self.assertEqual(candidate, SOURCES[f'{family}_clean'])
                self.assertEqual(source, SOURCES[f'{family}_fault'])
                self.assertEqual(receipt['rewritten_spans'], 1)
                self.assertEqual(receipt['functional_verdict'], 'NOT_EVALUATED')
                self.assertFalse(receipt['memory_authority_granted'])

    def test_clean_idempotence_and_action_rejection(self):
        for family in ('alex', 'zip'):
            with self.subTest(family=family):
                clean = SOURCES[f'{family}_clean']
                self.assertEqual(self.bind(family, clean)['status'], 'NO_MATCH')
                with self.assertRaises(ValueError):
                    binder.apply(ASSET, clean, CONTEXTS[family], self.bind(family, clean))

    def test_absent_and_multiple_modules(self):
        self.assertEqual(self.bind('alex', '// no RTL module')['status'], 'NO_MATCH')
        for family in ('alex', 'zip'):
            doubled = SOURCES[f'{family}_fault'].replace('endmodule', 'endmodule\nmodule decoy; endmodule', 1)
            self.assertEqual(self.bind(family, doubled)['status'],
                             'AMBIGUOUS')

    def test_extra_status_writers_are_ambiguous(self):
        for family, writer in (('alex', "missed_ack_reg = 1'b0;"),
                               ('zip', "last_err = 1'b0;")):
            source = SOURCES[f'{family}_fault'].replace('endmodule', writer + '\nendmodule')
            self.assertEqual(self.bind(family, source)['status'], 'AMBIGUOUS')

    def test_non_target_and_wrong_ack_relations_rejected(self):
        wrong = SOURCES['alex_fault'].replace('missed_ack_next = phy_rx_data_reg;',
                                              "missed_ack_next = 1'b0;", 1)
        self.assertEqual(self.bind('alex', wrong)['status'], 'UNSUPPORTED')
        wrong = SOURCES['zip_fault'].replace('else if ((r_busy)&&(ll_i2c_err))',
                                             'else if ((r_busy)&&(ll_i2c_ack))', 1)
        self.assertEqual(self.bind('zip', wrong)['status'], 'UNSUPPORTED')
        self.assertEqual(self.bind('alex', SOURCES['zip_fault'])['status'], 'NO_MATCH')

    def test_public_context_and_template_fail_closed(self):
        for family in ('alex', 'zip'):
            context = CONTEXTS[family]
            for bad in ({**context, 'defined_macros': ['FORMAL']},
                        {**context, 'answer': 'private'},
                        {**context, 'interface': []},
                        {**context, 'defined_macros': ()}, {}):
                with self.subTest(family=family, context=bad):
                    self.assertEqual(self.bind(family, context=bad)['status'], 'UNSUPPORTED')
            self.assertEqual(binder.bind({'binding_template': {}},
                                         SOURCES[f'{family}_fault'], context)['status'],
                             'UNSUPPORTED')

    def test_comments_are_not_binding_or_answer_slots(self):
        for family in ('alex', 'zip'):
            source = '// gold answer in comment: do not read\n' + SOURCES[f'{family}_fault']
            self.assertEqual(self.bind(family, source)['status'], 'BOUND')
            candidate, _ = binder.apply(ASSET, source, CONTEXTS[family], self.bind(family, source))
            self.assertTrue(candidate.startswith('// gold answer in comment: do not read'))

    def test_unsupported_directive_and_lexical_constructs(self):
        for family in ('alex', 'zip'):
            source = SOURCES[f'{family}_fault']
            for changed in ('`include "answer.v"\n' + source, source + '\x00',
                            source + '\n/* unterminated'):
                self.assertEqual(self.bind(family, changed)['status'], 'UNSUPPORTED')

    def test_stale_and_tampered_witness_rejected(self):
        for family in ('alex', 'zip'):
            source = SOURCES[f'{family}_fault']
            binding = self.bind(family)
            for field, value in (('rhs_span', [0, 1]), ('replacement_rhs', 'gold'),
                                 ('source_sha256', 'sha256:wrong'), ('action_digest', 'wrong')):
                tampered = copy.deepcopy(binding)
                tampered['witness'][field] = value
                with self.subTest(family=family, field=field), self.assertRaises(ValueError):
                    binder.apply(ASSET, source, CONTEXTS[family], tampered)
            with self.assertRaises(ValueError):
                binder.apply(ASSET, source + '\n', CONTEXTS[family], binding)

    def test_binding_has_no_file_or_network_side_channel(self):
        def denied(*_args, **_kwargs):
            raise AssertionError('forbidden I/O from binder')
        with patch.object(builtins, 'open', denied), patch.object(Path, 'open', denied), \
                patch.object(Path, 'read_text', denied), patch.object(Path, 'read_bytes', denied), \
                patch.object(socket, 'socket', denied):
            for family in ('alex', 'zip'):
                source = SOURCES[f'{family}_fault']
                binding = self.bind(family)
                self.assertEqual(binding['status'], 'BOUND')
                candidate, _ = binder.apply(ASSET, source, CONTEXTS[family], binding)
                self.assertEqual(self.bind(family, candidate)['status'], 'NO_MATCH')


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
