"""Strict v8 Asset evidence from three raw TRAIN components and source origins.

Caller booleans, design IDs, old-generation receipts and target/heldout rows do
not grant authority. This is a scoped research admission path, not Knowledge
admission, a constructed Memory snapshot or a production authorization.
"""
from collections.abc import Mapping
import hashlib

from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v8 import PROFILE, payload_from_source_v8
from . import r5_train_raw_v8 as raw
from . import r5_train_lineage_v8 as lineage
from .receipts import AssetPromotionReceipt
from .skid_binding_v8 import is_skid_v8_asset
from .structural_binding import bind_rtl_asset_to_source
from .validation import validate_asset_schema, validate_rtl_rewrite_asset

CASE_ORDER = raw.CASE_ORDER
REPOSITORIES = {case: row['repository'] for case, row in raw.SOURCES.items()}
ROLLBACK_VERSION = 'tehm-r5-v8-train-source-rollback-binding-v1'
EVIDENCE_VERSION = 'tehm-r5-v8-strict-train-asset-evidence-v1'


def train_source(case):
    if case not in CASE_ORDER:
        raise ValueError('unregistered v8 TRAIN source')
    if case == 'mux_skid':
        path = raw.TRAIN / raw.PACKAGES['mux']['directory'] / 'bundle/source.sv'
    else:
        path = raw.TRAIN / raw.PACKAGES['axis_zip']['directory'] / 'bundle/inputs' / (case + '.v')
    raw._require(raw._sha(path) == raw.SOURCES[case]['fault_sha256'], 'TRAIN fault source drift')
    return path.read_text()


def _oracle(case, checked):
    # This callback is created only after fresh raw/log and lineage replays.
    # It awards no verdict to a source other than the actually executed candidate.
    expected = raw.SOURCES[case]['candidate_sha256']
    raw._require(checked['valid'] is True and checked['raw_train_digest'], 'raw TRAIN unavailable')

    def check(candidate, _asset):
        return {'verdict': 'PASS' if hashlib.sha256(candidate.encode()).hexdigest() == expected else 'FAIL'}
    return check


def materialize(asset):
    """Recompute the exact evidence expected by the strict gate; no DB writes."""
    schema_valid, errors = validate_asset_schema(asset)
    if (not schema_valid or errors or not is_skid_v8_asset(asset) or
            not isinstance(asset.get('compatibility'), Mapping) or
            not isinstance(asset.get('definition'), Mapping) or
            not isinstance(asset.get('provenance'), Mapping) or
            asset['compatibility'].get('compatibility_profile') != PROFILE):
        raise ValueError('v8 Asset schema/profile mismatch')
    checked = lineage.audit()  # Internally replays both complete raw TRAIN packages.
    bounds, validations, payloads = [], [], []
    for case in CASE_ORDER:
        source, context = train_source(case), raw.SOURCES[case]['public_context']
        payload = payload_from_source_v8(source, context)
        candidate, edit = apply_rtl_action(source, payload)
        raw._require(hashlib.sha256(candidate.encode()).hexdigest() == raw.SOURCES[case]['candidate_sha256']
                     and edit['rewritten'] == 1 and edit['source_binding_rederived'] is True,
                     'current v8 action differs from executed TRAIN candidate')
        bound = bind_rtl_asset_to_source(asset, source, design_id=case, public_context=context)
        callback = _oracle(case, checked)
        receipt = validate_rtl_rewrite_asset(bound, source, verifier=callback,
                                             regression_verifier=callback).to_dict()
        raw._require(receipt['static_valid'] is True and receipt['independent_verifier'] is True
                     and receipt['oracle_verdict'] == receipt['regression_verdict'] == 'PASS'
                     and not receipt['errors'], 'TRAIN validation incomplete')
        receipt['r5_v8_oracle_witness'] = {
            'case': case, 'repository': REPOSITORIES[case], 'profile': PROFILE,
            'raw_train_digest': checked['raw_train_digest'],
            'candidate_sha256': raw.SOURCES[case]['candidate_sha256'],
            'oracle_kind': raw.SOURCES[case]['oracle_kind'],
            'public_context': dict(context), 'obligations': ['target', 'preservation']}
        payloads.append(payload)
        bounds.append(bound)
        validations.append(receipt)
    raw._require(any(raw._equal(asset['definition']['action']['payload'], p) for p in payloads),
                 'registered payload not from these TRAIN sources')
    rollback = {'version': ROLLBACK_VERSION, 'verified': True, 'cases': list(CASE_ORDER),
        'profile': PROFILE, 'raw_train_digest': checked['raw_train_digest'],
        'package_receipts': {kind: pin['receipt'] for kind, pin in raw.PACKAGES.items()},
        'restored_sources': {case: raw.SOURCES[case]['fault_sha256'] for case in CASE_ORDER},
        'source_restored_before_oracle_execution': True, 'memory_mremove': False,
        'role': 'RESEARCHER_ASSISTED_REUSED_DEV_TRAIN_SOURCE_ROLLBACK'}
    return {'bindings': bounds, 'validations': validations, 'rollback': rollback,
            'lineage': checked}


def verify_train_row_metadata(validation_entries, binding_entries):
    if len(validation_entries) != 3 or len(binding_entries) != 3:
        return False
    return all(isinstance(row, Mapping) and row.get('split') == 'training' and
               row.get('lineage_id') == REPOSITORIES[case] and row.get('source_id') == case
               for i, case in enumerate(CASE_ORDER)
               for row in (validation_entries[i], binding_entries[i]))


def evaluate(asset, *, validation_receipts, bindings, rollback_receipt,
             target_scope, min_lineages=2):
    from .lifecycle import ASSET_PROMOTION_GATES
    checks = {name: False for name in ASSET_PROMOTION_GATES}
    evidence = {'version': EVIDENCE_VERSION, 'lineages': [],
                'design_ids_are_not_lineages': True, 'memory_mremove': False,
                'lineage_scope': 'bounded_recorded_file_origin_groups',
                'statistical_independence_established': False}

    def result(reason):
        missing = tuple(name for name in ASSET_PROMOTION_GATES if not checks[name])
        return AssetPromotionReceipt(asset_id=str(asset.get('asset_id') or ''),
            target_scope=target_scope, eligible=not missing, checks=checks,
            missing=missing, evidence={**evidence, 'reason': reason})

    if target_scope != PROFILE:
        return result('v8_target_scope_mismatch')
    try:
        validations, bound = list(validation_receipts), list(bindings)
    except TypeError:
        return result('v8_train_evidence_malformed')
    if len(validations) != 3 or len(bound) != 3:
        return result('v8_train_evidence_cardinality')
    if (not all(isinstance(item, Mapping) for item in validations + bound) or
            not isinstance(rollback_receipt, Mapping) or
            rollback_receipt.get('version') != ROLLBACK_VERSION):
        return result('v8_train_evidence_generation_or_shape')
    try:
        expected = materialize(asset)
        same_bindings = raw._equal(bound, expected['bindings'])
        same_validation = raw._equal(validations, expected['validations'])
        same_rollback = raw._equal(rollback_receipt, expected['rollback'])
        source_groups = expected['lineage']['lineage_ids']
        checks.update({
            'schema_valid': True,
            'static_valid': same_validation,
            'independent_verifier': same_validation,
            'compatibility_verified': same_bindings,
            'cross_lineage_verified': same_bindings and same_validation and
                len(set(source_groups)) >= max(3, min_lineages),
            'regression_zero': same_validation,
            'rollback_verified': same_rollback,
        })
        evidence.update({'lineages': source_groups if same_bindings and same_validation else [],
            'raw_train_digest': expected['lineage']['raw_train_digest'],
            'lineage_audit_digest': expected['lineage']['digest'],
            'rollback_digest': lineage._digest(expected['rollback']),
            'exact_three_component_evidence_required': True})
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        return result('v8_train_raw_replay_failed:' + type(exc).__name__)
    return result('v8_raw_train_bundle_verified' if all(checks.values()) else 'v8_raw_train_bundle_mismatch')
