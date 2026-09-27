"""RAM-only strict v8 TRAIN authority, cold ledger and rejection checks.

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
from tehm.assets.skid_binding_v8 import with_skid_payload_binding_v8
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets import r5_train_evidence_v8 as evidence
from tehm.assets import r5_train_lineage_v8 as lineage
from tehm.assets import r5_train_raw_v8 as raw
from tehm.rtl.skid_payload_action_v8 import PROFILE, payload_from_source_v8

CLOCK = '2026-09-27T00:00:00+00:00'


def check():
    with patch('tehm.db.now_local', return_value=CLOCK):
        return _check()


def _check():
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    recovered = sqlite3.connect(':memory:'); recovered.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        source = evidence.train_source('axis_register')
        context = raw.SOURCES['axis_register']['public_context']
        proposal = build_rtl_asset_proposal({}, name='r5-v8-three-component-train-asset',
            transformation_family='skid_payload_restore_shadow_v8',
            action_payload_template=payload_from_source_v8(source, context),
            compatibility_profile=PROFILE, verifier_obligations=('TRAIN target', 'TRAIN preservation'),
            creator='researcher_assisted_reused_dev_train_v8')
        proposal = with_skid_payload_binding_v8(proposal, source, context)
        registered = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered.asset_id)
        expected = evidence.materialize(asset)
        def gate(**overrides):
            args = dict(validation_receipts=expected['validations'], bindings=expected['bindings'],
                        rollback_receipt=expected['rollback'], target_scope=PROFILE, min_lineages=3)
            args.update(overrides)
            return evaluate_asset_authority(asset, **args)
        def rows(values, wrapper):
            return [{wrapper: value, 'split': 'training', 'source_id': case,
                     'lineage_id': evidence.REPOSITORIES[case]}
                    for case, value in zip(evidence.CASE_ORDER, values)]
        validations, bindings = rows(expected['validations'], 'receipt'), rows(expected['bindings'], 'asset')
        rollback = {'receipt': expected['rollback'], 'split': 'ab', 'source_id': 'v8-train-source-rollback'}
        pure = gate()
        strict = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=validations, bindings=bindings, rollback_receipt=rollback, min_lineages=3)
        cold = verify_asset_authority(conn, strict)
        conn.commit(); conn.backup(recovered)
        cold_reloaded = verify_asset_authority(recovered, strict)
        cases = {
            'exact_three_raw_components': len(expected['bindings']) == len(expected['validations']) == 3,
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
                validation_receipts=expected['validations'][:2], bindings=expected['bindings'][:2], min_lineages=1).eligible,
            'unproved_fourth_lineage_rejected': not gate(min_lineages=4).eligible,
        }
        altered = deepcopy(expected['validations']); altered[2]['r5_v8_oracle_witness']['candidate_sha256'] = '0' * 64
        cases['forged_oracle_witness_rejected'] = not gate(validation_receipts=altered).eligible
        altered = deepcopy(expected['bindings']); altered[2]['provenance']['bound_design'] = 'invented-lineage'
        cases['forged_bound_source_rejected'] = not gate(bindings=altered).eligible
        altered = {**expected['rollback'], 'memory_mremove': True}
        cases['source_rollback_not_mremove'] = not gate(rollback_receipt=altered).eligible
        altered = {**expected['rollback'], 'version': 'v7-or-old-generation'}
        cases['old_generation_rejected'] = not gate(rollback_receipt=altered).eligible
        with patch.object(raw, '_sealed', side_effect=ValueError('synthetic evidence missing')):
            cases['missing_raw_evidence_rejected'] = not gate().eligible
        changed = raw._expected('axis_zip'); changed['matrix']['axis_register']['candidate']['target'] = 'UNKNOWN'
        with patch.object(raw, '_replay', return_value={'original': changed, 'recovery': changed}):
            cases['raw_unknown_not_pass'] = not gate().eligible
        original_origin = lineage._pair_origin
        def duplicate_origin(root, case):
            result = original_origin(root, case)
            result['recorded_login'] = 'same-origin'
            return result
        with patch.object(lineage, '_pair_origin', side_effect=duplicate_origin):
            cases['shared_recorded_origin_rejected'] = not gate().eligible
        bad_rows = deepcopy(validations); bad_rows[2]['lineage_id'] = 'invented/fourth'
        bad_strict = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=bad_rows, bindings=bindings, rollback_receipt=rollback, min_lineages=3)
        cases['forged_metadata_record_rejected'] = not bad_strict.eligible
        cases['forged_metadata_cold_rejected'] = not verify_asset_authority(conn, bad_strict)['eligible']
        for split in ('heldout', 'calibration', 'ab'):
            altered = deepcopy(validations); altered[0]['split'] = split
            cases[split + '_not_training'] = not evidence.verify_train_row_metadata(altered, bindings)
        changed_rows = recovered.execute("UPDATE tehm_asset_authority_evidence SET lineage_id='forged' WHERE evidence_type='asset_binding'").rowcount
        cases['db_evidence_tamper_cold_rejected'] = changed_rows == 3 and not verify_asset_authority(recovered, strict)['eligible']
        heldout = deepcopy(validations); heldout[0]['split'] = 'heldout'
        heldout_receipt = record_asset_authority(conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=heldout, bindings=bindings, rollback_receipt=rollback, min_lineages=3)
        cases['heldout_actual_ledger_rejected'] = not heldout_receipt.eligible
        cases['heldout_actual_cold_rejected'] = not verify_asset_authority(conn, heldout_receipt)['eligible']
        generated = deepcopy(asset); generated['provenance']['generator_is_verifier'] = True
        cases['generator_is_verifier_rejected'] = not evaluate_asset_authority(generated,
            validation_receipts=expected['validations'], bindings=expected['bindings'],
            rollback_receipt=expected['rollback'], target_scope=PROFILE, min_lineages=3).eligible
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
        return {'schema': 'r5-v8-strict-train-authority-ram-checks-v1',
            'valid': all(cases.values()), 'case_count': len(cases), 'cases': cases,
            'failed': sorted(k for k, v in cases.items() if not v),
            'raw_train_digest': expected['lineage']['raw_train_digest'],
            'lineage_audit_digest': expected['lineage']['digest'],
            'asset_authority_receipt_digest': strict.receipt_digest,
            'asset_id': registered.asset_id, 'ram_only_research_promotion': True,
            'persistent_memory_changed': False, 'production_authority': False,
            'memory_m_plus_constructed': False, 'memory_mremove': False,
            'heldout_transfer': False, 'model_calls': 0, 'new_simulator_executions': 0,
            'fixture_clock': CLOCK, 'pure_missing': list(pure.missing), 'cold_reasons': cold['reasons']}
    finally:
        recovered.close(); conn.close()


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result['valid'] else 1)
