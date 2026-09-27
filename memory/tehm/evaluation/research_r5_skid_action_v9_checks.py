"""Offline checks for the v9 DEV action envelope on registered local inputs."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, TextTestRunner, defaultTestLoader, mock
import socket

from tehm.evaluation.research_r5_skid_binding_v9 import CONTEXT
from tehm.assets.source_selection import verify_candidate_source_replay
from tehm.assets import lifecycle, registry
from tehm.assets.lifecycle import ASSET_PROMOTION_GATES
from tehm.rtl.rtl_actions import RTL_ACTION_DOMAINS, RTL_ACTION_VERSION, apply_rtl_action
from tehm.rtl.skid_payload_action_v9 import (
    DOMAIN, PROFILE, PAYLOAD_KEYS, apply_skid_payload_action_v9, payload_from_source_v9,
)


FAULT = CLEAN = NEGATIVE = ''


class Checks(TestCase):
    def payload(self):
        return payload_from_source_v9(FAULT, CONTEXT)

    def test_exact_source_derived_action(self):
        payload = self.payload()
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertEqual(payload['domain'], DOMAIN)
        candidate, receipt = apply_skid_payload_action_v9(FAULT, payload)
        self.assertNotEqual(candidate, FAULT)
        self.assertEqual(candidate, CLEAN)  # Evaluator-only post-hoc equality.
        self.assertEqual(receipt['rewritten'], 1)
        self.assertTrue(receipt['source_binding_rederived'])
        self.assertFalse(receipt['memory_authority_granted'])
        self.assertEqual(receipt['functional_verdict'], 'NOT_EVALUATED')

    def test_core_dispatch_and_proofless_rejection(self):
        payload = self.payload()
        self.assertEqual(RTL_ACTION_VERSION, 'rtl-actions-v1.0')
        self.assertIn(DOMAIN, RTL_ACTION_DOMAINS)
        candidate, receipt = apply_rtl_action(FAULT, payload)
        self.assertEqual(candidate, CLEAN)
        self.assertFalse(receipt['memory_authority_granted'])
        proofless = SimpleNamespace(provenance={}, concrete_action=payload)
        self.assertFalse(verify_candidate_source_replay(proofless, FAULT))

    def test_v9_asset_authority_is_closed(self):
        asset = {'asset_id': 'asset_v9_dev_only',
                 'definition': {'action': {'domain': DOMAIN, 'payload': self.payload()}},
                 'compatibility': {'compatibility_profile': PROFILE}}
        gates = {name: True for name in ASSET_PROMOTION_GATES}
        delegated = lifecycle.evaluate_asset_promotion_gates(
            asset, gates, target_scope=PROFILE)
        derived = lifecycle.evaluate_asset_authority(
            asset, validation_receipts=[], bindings=[], rollback_receipt=None,
            target_scope=PROFILE)
        for receipt in (delegated, derived):
            self.assertFalse(receipt.eligible)
            self.assertEqual(receipt.evidence['reason'],
                             'v9_raw_train_authority_not_implemented')
        spoofed = copy.deepcopy(asset)
        spoofed['definition']['action']['domain'] = 'rtl.AST_REWRITE'
        spoofed['compatibility']['compatibility_profile'] = 'other'
        nested = lifecycle.evaluate_asset_promotion_gates(
            spoofed, gates, target_scope=PROFILE)
        self.assertFalse(nested.eligible)
        self.assertEqual(nested.evidence['reason'], 'v9_raw_train_authority_not_implemented')
        with (mock.patch.object(registry, 'get_asset', return_value=asset),
              mock.patch.object(registry, 'get_asset_status',
                                return_value={'status': 'candidate'})):
            with self.assertRaisesRegex(ValueError,
                                        'v9_raw_train_authority_not_implemented'):
                registry.set_asset_status(None, asset_id=asset['asset_id'],
                                          target_scope=PROFILE, status='promoted',
                                          strict_asset_authority=True,
                                          authority_receipt={'eligible': True})

    def test_healthy_source_does_not_bind(self):

        with self.assertRaises(ValueError):
            payload_from_source_v9(CLEAN, CONTEXT)
        with self.assertRaises(ValueError):
            apply_skid_payload_action_v9(CLEAN, self.payload())

    def test_non_target_structure_does_not_bind(self):
        with self.assertRaises(ValueError):
            payload_from_source_v9(NEGATIVE, CONTEXT)

    def test_source_drift_rejected(self):
        with self.assertRaises(ValueError):
            apply_skid_payload_action_v9(FAULT + '\n// drift\n', self.payload())

    def test_exact_fields_and_wrong_domain_rejected(self):
        for key, value in [('answer_path', '/tmp/private'), ('domain', 'rtl.AST_REWRITE')]:
            payload = self.payload()
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                apply_skid_payload_action_v9(FAULT, payload)
        payload = self.payload()
        del payload['witness_digest']
        with self.assertRaises(ValueError):
            apply_skid_payload_action_v9(FAULT, payload)

    def test_tampered_source_witness_and_context_rejected(self):
        for key, value in [('module', 'another_module'),
                           ('source_sha256', 'sha256:' + '0' * 64),
                           ('witness_digest', 'sha256:' + '0' * 64),
                           ('binding_contract', 'wrong')]:
            payload = self.payload()
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                apply_skid_payload_action_v9(FAULT, payload)
        context = copy.deepcopy(CONTEXT)
        context['defined_macros'] = ['FORMAL']
        with self.assertRaises(ValueError):
            payload_from_source_v9(FAULT, context)

    def test_wrong_payload_type_rejected(self):
        for payload in (None, {}, [], 'payload'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                apply_skid_payload_action_v9(FAULT, payload)

    def test_action_has_no_file_or_network_side_channel(self):
        payload = self.payload()
        with (mock.patch('builtins.open', side_effect=AssertionError('file read')),
              mock.patch.object(Path, 'open', side_effect=AssertionError('file read')),
              mock.patch.object(Path, 'read_text', side_effect=AssertionError('file read')),
              mock.patch.object(socket, 'socket', side_effect=AssertionError('network'))):
            candidate, _ = apply_skid_payload_action_v9(FAULT, payload)
        self.assertEqual(candidate, CLEAN)


def run_checks(clean: Path, fault: Path, negative: Path) -> dict:
    global CLEAN, FAULT, NEGATIVE
    CLEAN, FAULT, NEGATIVE = (path.read_text(encoding='utf-8')
                              for path in (clean, fault, negative))
    result = TextTestRunner(verbosity=2).run(defaultTestLoader.loadTestsFromTestCase(Checks))
    return {'valid': result.wasSuccessful(), 'case_count': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'role': 'DEV_ONLY', 'memory_constructed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--clean', type=Path, required=True)
    parser.add_argument('--fault', type=Path, required=True)
    parser.add_argument('--negative', type=Path, required=True)
    args = parser.parse_args()
    receipt = run_checks(args.clean, args.fault, args.negative)
    print(receipt)
    raise SystemExit(0 if receipt['valid'] else 1)
