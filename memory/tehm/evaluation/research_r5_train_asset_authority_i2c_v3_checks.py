"""RAM-only strict I2C NACK v3 TRAIN authority, cold ledger and rejection checks (contract f886e8f, 2c).

No provider, EDA, persistent Memory or production promotion. A research-profile
promotion is exercised only in the disposable RAM database and then discarded.
"""
from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch

from tehm import db
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority, evaluate_asset_promotion_gates, ASSET_PROMOTION_GATES
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.i2c_binding_v3 import with_i2c_nack_binding_v3
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets import r5_train_evidence_i2c_v3 as evidence
from tehm.assets import r5_train_lineage_i2c_v3 as lineage
from tehm.assets import r5_train_raw_i2c_v3 as raw
from tehm.rtl import i2c_nack_action_v2 as action
from tehm.rtl import i2c_nack_action_v3 as action3
from tehm.rtl.i2c_nack_action_v3 import PROFILE, payload_from_source_i2c_v3

CLOCK = '2026-09-29T00:00:00+00:00'


def check():
    with patch('tehm.db.now_local', return_value=CLOCK):
        return _check()


def _rejects(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def _closure_cases():
    roles = {role: (raw.TRAIN / 'bundle/inputs/freecores' / (role + '.v')).read_text() for role in raw.ROLES}
    text = action.encode_closure(roles)
    swapped = action.encode_closure(roles).replace('role=top', 'role=tmp', 1).replace(
        'role=byte_ctrl', 'role=top', 1).replace('role=tmp', 'role=byte_ctrl', 1)
    header = '// @tehm-closure-v1 role=defines bytes=%d\n' % len(roles['defines'].encode())
    wrong_len = text.replace(header, '// @tehm-closure-v1 role=defines bytes=%d\n' % (len(roles['defines'].encode()) - 1))
    missing = text[:text.index('// @tehm-closure-v1 role=defines')]
    context = raw.SOURCES['freecores']['public_context']
    return {
        'closure_roundtrip_exact': action.decode_closure(text) == roles,
        'closure_wrong_role_order_rejected': _rejects(action.decode_closure, swapped),
        'closure_wrong_byte_length_rejected': _rejects(action.decode_closure, wrong_len),
        'closure_trailing_data_rejected': _rejects(action.decode_closure, text + '\n'),
        'closure_missing_role_rejected': _rejects(action.decode_closure, missing),
        'closure_for_single_file_interface_rejected': _rejects(
            payload_from_source_i2c_v3, text, raw.SOURCES['alex']['public_context']),
        'stale_payload_rejected': _rejects(
            action3.apply_i2c_nack_action_v3, text,
            {**payload_from_source_i2c_v3(text, context), 'source_sha256': 'sha256:' + '0' * 64}),
    }


def _check():
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    recovered = sqlite3.connect(':memory:'); recovered.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        source = evidence.train_source('alex')
        context = raw.SOURCES['alex']['public_context']
        proposal = build_rtl_asset_proposal({}, name='r5-i2c-v3-four-component-train-asset',
            transformation_family='i2c_nack_status_latch_v3',
            action_payload_template=payload_from_source_i2c_v3(source, context),
            compatibility_profile=PROFILE, verifier_obligations=('TRAIN target', 'TRAIN preservation'),
            creator='researcher_assisted_reused_dev_train_i2c_v3')
        proposal = with_i2c_nack_binding_v3(proposal, source, context)
        registered = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered.asset_id)
        expected = evidence.materialize(asset)

        def gate(**overrides):
            args = dict(validation_receipts=expected['validations'], bindings=expected['bindings'],
                        rollback_receipt=expected['rollback'], target_scope=PROFILE, min_lineages=4)
            args.update(overrides)
            return evaluate_asset_authority(asset, **args)

        def rows(values, wrapper):
            return [{wrapper: value, 'split': 'training', 'source_id': case,
                     'lineage_id': evidence.REPOSITORIES[case]}
                    for case, value in zip(evidence.CASE_ORDER, values)]
        validations, bindings = rows(expected['validations'], 'receipt'), rows(expected['bindings'], 'asset')
        rollback = {'receipt': expected['rollback'], 'split': 'ab', 'source_id': 'i2c-v3-train-source-rollback'}
        pure = gate()
        strict = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=validations, bindings=bindings, rollback_receipt=rollback, min_lineages=4)
        cold = verify_asset_authority(conn, strict)
        conn.commit(); conn.backup(recovered)
        cold_reloaded = verify_asset_authority(recovered, strict)
        cases = {
            'exact_four_raw_components': len(expected['bindings']) == len(expected['validations']) == 4,
            'bounded_source_groups_not_statistics': expected['lineage']['lineage_ids'] ==
                sorted(evidence.REPOSITORIES.values()) and
                expected['lineage']['statistical_independence_established'] is False and
                expected['lineage']['independent_authorship_proven'] is False,
            'raw_gate_eligible': pure.eligible and not pure.missing,
            'strict_ledger_eligible': strict.eligible and not strict.missing,
            'cold_ledger_eligible': cold['eligible'] is True,
            'second_ram_connection_cold_eligible': cold_reloaded['eligible'] is True,
            'exact_training_metadata': evidence.verify_train_row_metadata(validations, bindings),
            'no_boolean_authority': not evaluate_asset_promotion_gates(asset,
                {k: True for k in ASSET_PROMOTION_GATES}, target_scope=PROFILE).eligible,
            'wrong_scope_rejected': not gate(target_scope=PROFILE + '.other').eligible,
            'insufficient_components_rejected_even_with_min_one': not gate(
                validation_receipts=expected['validations'][:3], bindings=expected['bindings'][:3],
                min_lineages=1).eligible,
            'unproved_fifth_lineage_rejected': not gate(min_lineages=5).eligible,
        }
        altered = deepcopy(expected['validations'])
        altered[3]['r5_i2c_v3_oracle_witness']['candidate_sha256'] = 'sha256:' + '0' * 64
        cases['forged_oracle_witness_rejected'] = not gate(validation_receipts=altered).eligible
        altered = deepcopy(expected['bindings']); altered[3]['provenance']['bound_design'] = 'invented-lineage'
        cases['forged_bound_source_rejected'] = not gate(bindings=altered).eligible
        altered = {**expected['rollback'], 'memory_mremove': True}
        cases['source_rollback_not_mremove'] = not gate(rollback_receipt=altered).eligible
        altered = {**expected['rollback'], 'version': 'v8-or-old-generation'}
        cases['old_generation_rejected'] = not gate(rollback_receipt=altered).eligible
        with patch.object(raw, '_sealed', side_effect=ValueError('synthetic evidence missing')):
            cases['missing_raw_evidence_rejected'] = not gate().eligible
        audit = raw._read(raw.TRAIN / 'original/audit.json')
        changed = deepcopy(audit); changed['matrix']['chance189']['candidate']['target'] = 'UNKNOWN'
        with patch.object(raw, '_replay', return_value={'original': changed, 'recovery': changed}):
            cases['raw_unknown_not_pass'] = not gate().eligible
        original_origin = lineage._origin

        def duplicate_origin(case, inputs, train):
            result = original_origin(case, inputs, train)
            result['recorded_logins'] = ['same-origin']
            return result
        with patch.object(lineage, '_origin', side_effect=duplicate_origin):
            cases['shared_recorded_origin_rejected'] = not gate().eligible
        bad_rows = deepcopy(validations); bad_rows[3]['lineage_id'] = 'invented/fifth'
        bad_strict = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=bad_rows, bindings=bindings, rollback_receipt=rollback, min_lineages=4)
        cases['forged_metadata_record_rejected'] = not bad_strict.eligible
        cases['forged_metadata_cold_rejected'] = not verify_asset_authority(conn, bad_strict)['eligible']
        for split in ('heldout', 'calibration', 'ab'):
            altered = deepcopy(validations); altered[0]['split'] = split
            cases[split + '_not_training'] = not evidence.verify_train_row_metadata(altered, bindings)
        changed_rows = recovered.execute("UPDATE tehm_asset_authority_evidence SET lineage_id='forged' "
                                         "WHERE evidence_type='asset_binding'").rowcount
        cases['db_evidence_tamper_cold_rejected'] = (changed_rows == 4 and
            not verify_asset_authority(recovered, strict)['eligible'])
        heldout = deepcopy(validations); heldout[0]['split'] = 'heldout'
        heldout_receipt = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=heldout, bindings=bindings, rollback_receipt=rollback, min_lineages=4)
        cases['heldout_actual_ledger_rejected'] = not heldout_receipt.eligible
        cases['heldout_actual_cold_rejected'] = not verify_asset_authority(conn, heldout_receipt)['eligible']
        generated = deepcopy(asset); generated['provenance']['generator_is_verifier'] = True
        cases['generator_is_verifier_rejected'] = not evaluate_asset_authority(generated,
            validation_receipts=expected['validations'], bindings=expected['bindings'],
            rollback_receipt=expected['rollback'], target_scope=PROFILE, min_lineages=4).eligible
        cases.update(_closure_cases())
        for status in ('shadow', 'candidate'):
            set_asset_status(conn, asset_id=registered.asset_id, target_scope=PROFILE, status=status)
        try:
            set_asset_status(conn, asset_id=registered.asset_id, target_scope=PROFILE,
                             status='promoted', gates={k: True for k in ASSET_PROMOTION_GATES})
            cases['nonstrict_promotion_rejected'] = False
        except ValueError:
            cases['nonstrict_promotion_rejected'] = True
        set_asset_status(conn, asset_id=registered.asset_id, target_scope=PROFILE, status='promoted',
                         strict_asset_authority=True, authority_receipt=strict)
        cases['strict_research_promotion_in_ram'] = get_asset_status(conn,
            asset_id=registered.asset_id, target_scope=PROFILE)['status'] == 'promoted'
        cases['no_knowledge_objects'] = db.count_rows(conn, 'tehm_mechanism_knowledge') == 0
        return {'schema': 'r5-i2c-v3-strict-train-authority-ram-checks-v1',
            'valid': all(cases.values()), 'case_count': len(cases), 'cases': cases,
            'failed': sorted(k for k, v in cases.items() if not v),
            'raw_train_digest': expected['raw']['digest'],
            'lineage_audit_digest': expected['lineage']['digest'],
            'asset_authority_receipt_digest': strict.receipt_digest,
            'asset_id': registered.asset_id, 'ram_only_research_promotion': True,
            'persistent_memory_changed': False, 'production_authority': False,
            'independent_lineages_established': False,
            'memory_m_plus_constructed': False, 'memory_mremove': False,
            'heldout_transfer': False, 'model_calls': 0, 'new_simulator_executions': 0,
            'fixture_clock': CLOCK, 'pure_missing': list(pure.missing), 'cold_reasons': cold['reasons']}
    finally:
        recovered.close(); conn.close()


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result['valid'] else 1)
