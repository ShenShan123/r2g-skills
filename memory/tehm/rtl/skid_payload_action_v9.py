"""DEV-only source-bound action envelope for the one-process v9 drain grammar.

This module has no retrieval, Asset, TRAIN, or production authority.  The
selected template and all source-specific coordinates are rederived from the
provided RTL and public context immediately before the edit.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy

from tehm.evaluation.research_r5_skid_binding_v9 import (
    CONTRACT, PROFILE, TEMPLATE, apply_bound_one_process_drain_v9,
    bind_one_process_drain_v9,
)


DOMAIN = 'rtl.SKID_ONE_PROCESS_DRAIN_SHADOW_V9'
PAYLOAD_KEYS = frozenset({
    'domain', 'compatibility_profile', 'module', 'public_context',
    'source_sha256', 'witness_digest', 'binding_contract',
})


def payload_from_source_v9(source: str, public_context: Mapping) -> dict:
    binding = bind_one_process_drain_v9(
        {'binding_template': TEMPLATE}, source, public_context)
    if binding.get('status') != 'BOUND':
        raise ValueError('v9 source binding rejected: ' + str(binding.get('reason')))
    witness = binding['witness']
    return {'domain': DOMAIN, 'compatibility_profile': PROFILE,
            'module': witness['module'],
            'public_context': copy.deepcopy(dict(public_context)),
            'source_sha256': witness['source_sha256'],
            'witness_digest': binding['witness_digest'],
            'binding_contract': CONTRACT}


def apply_skid_payload_action_v9(source: str, payload: Mapping) -> tuple[str, dict]:
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError('v9 action requires exact payload fields')
    context = payload['public_context']
    if not isinstance(context, Mapping):
        raise ValueError('v9 action requires public context')
    expected = payload_from_source_v9(source, context)
    if dict(payload) != expected:
        raise ValueError('v9 action payload is stale or tampered')
    binding = bind_one_process_drain_v9(
        {'binding_template': TEMPLATE}, source, context)
    edited, receipt = apply_bound_one_process_drain_v9(
        {'binding_template': TEMPLATE}, source, context, binding)
    return edited, {**receipt, 'domain': DOMAIN,
                    'compatibility_profile': PROFILE,
                    'source_binding_rederived': True,
                    'memory_authority_granted': False}


__all__ = ['DOMAIN', 'PROFILE', 'PAYLOAD_KEYS', 'payload_from_source_v9',
           'apply_skid_payload_action_v9']
