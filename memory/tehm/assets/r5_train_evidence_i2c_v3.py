"""Strict I2C NACK v3 Asset evidence from four raw TRAIN components and source origins (contract f886e8f, 2c).

Caller booleans, design IDs, old-generation receipts and target/heldout rows do
not grant authority. This is a scoped research admission path, not Knowledge
admission, a constructed Memory snapshot or a production authorization.
"""
from collections.abc import Mapping
import hashlib

from tehm.rtl.i2c_nack_action_v2 import decode_closure, encode_closure
from tehm.rtl.i2c_nack_action_v3 import PROFILE, payload_from_source_i2c_v3
from tehm.rtl.rtl_actions import apply_rtl_action
from . import r5_train_lineage_i2c_v3 as lineage
from . import r5_train_raw_i2c_v3 as raw
from .i2c_binding_v3 import is_i2c_v3_asset
from .receipts import AssetPromotionReceipt
from .structural_binding import bind_rtl_asset_to_source
from .validation import validate_asset_schema, validate_rtl_rewrite_asset

CASE_ORDER = raw.CASE_ORDER
REPOSITORIES = {case: row['repository'] for case, row in raw.SOURCES.items()}
ROLLBACK_VERSION = 'tehm-r5-i2c-v3-train-source-rollback-binding-v1'
EVIDENCE_VERSION = 'tehm-r5-i2c-v3-strict-train-asset-evidence-v1'


def _text_sha(text):
    return 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def _source_hashes(case, source):
    if case == 'freecores':
        return {role: _text_sha(text) for role, text in decode_closure(source).items()}
    return _text_sha(source)


def train_source(case):
    """The registered TRAIN fault source as core transport text."""
    if case not in CASE_ORDER:
        raise ValueError('unregistered I2C v3 TRAIN source')
    inputs = raw.TRAIN / 'bundle/inputs'
    if case == 'freecores':
        source = encode_closure({role: (inputs / 'freecores' / (role + '.v')).read_text()
                                 for role in raw.ROLES})
    else:
        source = (inputs / (case + '.v')).read_text()
    raw._require(raw._equal(_source_hashes(case, source), raw.SOURCES[case]['fault_sha256']),
                 'TRAIN fault source drift')
    return source


def _oracle(case, checked, lineage_checked):
    # Created only after fresh raw and lineage replays. It awards PASS only to the
    # exact candidate that was actually executed in TRAIN.
    expected = raw.SOURCES[case]['candidate_sha256']
    raw._require(checked['raw_chain_verified'] is True and lineage_checked['valid'] is True,
                 'raw/lineage TRAIN unavailable')

    def check(candidate, _asset):
        return {'verdict': 'PASS' if raw._equal(_source_hashes(case, candidate), expected) else 'FAIL'}
    return check


def materialize(asset):
    """Recompute the exact evidence expected by the strict gate; no DB writes."""
    schema_valid, errors = validate_asset_schema(asset)
    if (not schema_valid or errors or not is_i2c_v3_asset(asset) or
            not isinstance(asset.get('compatibility'), Mapping) or
            not isinstance(asset.get('definition'), Mapping) or
            not isinstance(asset.get('provenance'), Mapping) or
            asset['compatibility'].get('compatibility_profile') != PROFILE):
        raise ValueError('I2C v3 Asset schema/profile mismatch')
    checked = raw.verify()
    origins = lineage.audit()
    raw._require(origins['valid'] is True and
                 origins['verdict'] == 'BOUNDED_RECORDED_ORIGIN_GROUPS_VERIFIED', 'lineage not verified')
    bounds, validations, payloads = [], [], []
    for case in CASE_ORDER:
        source, context = train_source(case), raw.SOURCES[case]['public_context']
        payload = payload_from_source_i2c_v3(source, context)
        candidate, edit = apply_rtl_action(source, payload)
        raw._require(raw._equal(_source_hashes(case, candidate), raw.SOURCES[case]['candidate_sha256'])
                     and edit['rewritten'] == 1 and edit['rewritten_spans'] == 1
                     and edit['source_binding_rederived'] is True,
                     'current I2C v3 action differs from executed TRAIN candidate')
        bound = bind_rtl_asset_to_source(asset, source, design_id=case, public_context=context)
        callback = _oracle(case, checked, origins)
        receipt = validate_rtl_rewrite_asset(bound, source, verifier=callback,
                                             regression_verifier=callback).to_dict()
        raw._require(receipt['static_valid'] is True and receipt['independent_verifier'] is True
                     and receipt['oracle_verdict'] == receipt['regression_verdict'] == 'PASS'
                     and not receipt['errors'], 'TRAIN validation incomplete')
        receipt['r5_i2c_v3_oracle_witness'] = {
            'case': case, 'repository': REPOSITORIES[case], 'profile': PROFILE,
            'raw_train_digest': checked['digest'],
            'candidate_sha256': raw.SOURCES[case]['candidate_sha256'],
            'oracle_kind': raw.SOURCES[case]['oracle_kind'],
            'public_context': dict(context), 'obligations': ['target', 'preservation']}
        payloads.append(payload)
        bounds.append(bound)
        validations.append(receipt)
    raw._require(any(raw._equal(asset['definition']['action']['payload'], p) for p in payloads),
                 'registered payload not from these TRAIN sources')
    rollback = {'version': ROLLBACK_VERSION, 'verified': True, 'cases': list(CASE_ORDER),
                'profile': PROFILE, 'raw_train_digest': checked['digest'],
                'package_receipt': raw.RECEIPT_SHA,
                'restored_sources': {case: raw.SOURCES[case]['fault_sha256'] for case in CASE_ORDER},
                'source_restored_before_oracle_execution': True, 'memory_mremove': False,
                'role': 'RESEARCHER_ASSISTED_REUSED_DEV_TRAIN_SOURCE_ROLLBACK'}
    return {'bindings': bounds, 'validations': validations, 'rollback': rollback,
            'raw': checked, 'lineage': origins}


def verify_train_row_metadata(validation_entries, binding_entries):
    if len(validation_entries) != 4 or len(binding_entries) != 4:
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
                'lineage_scope': 'bounded_recorded_origin_groups',
                'statistical_independence_established': False}

    def result(reason):
        missing = tuple(name for name in ASSET_PROMOTION_GATES if not checks[name])
        return AssetPromotionReceipt(asset_id=str(asset.get('asset_id') or ''),
            target_scope=target_scope, eligible=not missing, checks=checks,
            missing=missing, evidence={**evidence, 'reason': reason})

    if target_scope != PROFILE:
        return result('i2c_v3_target_scope_mismatch')
    try:
        validations, bound = list(validation_receipts), list(bindings)
    except TypeError:
        return result('i2c_v3_train_evidence_malformed')
    if len(validations) != 4 or len(bound) != 4:
        return result('i2c_v3_train_evidence_cardinality')
    if (not all(isinstance(item, Mapping) for item in validations + bound) or
            not isinstance(rollback_receipt, Mapping) or
            rollback_receipt.get('version') != ROLLBACK_VERSION):
        return result('i2c_v3_train_evidence_generation_or_shape')
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
                len(set(source_groups)) >= max(4, min_lineages),
            'regression_zero': same_validation,
            'rollback_verified': same_rollback,
        })
        evidence.update({'lineages': source_groups if same_bindings and same_validation else [],
            'raw_train_digest': expected['raw']['digest'],
            'lineage_audit_digest': expected['lineage']['digest'],
            'rollback_digest': lineage._digest(expected['rollback']),
            'exact_four_component_evidence_required': True})
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        return result('i2c_v3_train_raw_replay_failed:' + type(exc).__name__)
    return result('i2c_v3_raw_train_bundle_verified' if all(checks.values())
                  else 'i2c_v3_raw_train_bundle_mismatch')
