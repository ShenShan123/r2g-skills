"""Canonical v8 TRAIN records derived from the sealed three-source executions.

Both control and treatment reuse explicitly registered DEV tasks. They are
training witnesses, not new oracle runs, unseen transfer, or autonomous repair.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import sqlite3

from tehm.assets import r5_train_lineage_v8 as lineage
from tehm.assets import r5_train_raw_v8 as raw_train
from tehm.canonical.capture import ExecutionRecord
from tehm.evaluation import research_r5_train_axis as axis
from tehm.ids import stable_dumps
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v8 import DOMAIN, PROFILE, payload_from_source_v8

ACQUISITION_VERSION = 'tehm-r5-rtl-train-acquisition-v8'
SCOPED_VERSION = 'tehm-r5-rtl-train-scoped-v8'
CAMPAIGN = 'r5-rtl-skid-train-generation-6-v8'
FAMILY = 'SKID_TEMP_PAYLOAD_RESTORE'
CASES = raw_train.CASE_ORDER
LINEAGE_DIGEST = 'sha256:3d50d97014f9fee8e6224f80429a32da8f196c37eb87be839601e54155944876'
RAW_DIGEST = 'sha256:c7542a4fb46873d0f4b2ce5b8a89854f9995f6113e130e24914fff33e7f5d7d7'
MEASUREMENT_CONTRACT = {
    'version': 'tehm-r5-skid-payload-measurement-v8',
    'scope': PROFILE,
    'mechanism_family': FAMILY,
    'common_obligation': 'one_buffered_payload_is_delivered_intact_after_backpressure',
    'preservation_required': True,
    'per_task_oracle_instance_witness_required': True,
    'native_oracle_equivalence': False,
    'heldout_target_claim': False,
    'production_authority': False,
}
OBLIGATIONS = {
    'axis_register': ('registered_buffered_payload_after_stall',
                      'seven_non_target_cocotb_test_cases'),
    'zipcpu_skidbuffer': ('backpressure_payload_delivery_augmented',
                         'direct_payload_delivery_augmented'),
    'mux_skid': ('second_buffered_beat_payload_delivery',
                 'direct_non_buffered_payload_delivery'),
}
TEST_IDS = {
    'axis_register': {'target': ('run_test_002', 'run_stress_test_002'),
                      'preservation': ('run_test_001', 'run_test_003',
                                       'run_test_004', 'run_test_tuser_assert_001',
                                       'run_stress_test_001', 'run_stress_test_003',
                                       'run_stress_test_004')},
    'zipcpu_skidbuffer': {'target': ('backpressure',), 'preservation': ('direct',)},
    'mux_skid': {'target': ('target',), 'preservation': ('preservation',)},
}


def _digest(value: object) -> str:
    return 'sha256:' + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def acquisition(case_id: str, role: str) -> dict:
    if case_id not in CASES or role not in {'control', 'treatment'}:
        raise ValueError('unsupported v8 TRAIN case or role')
    group = 'mux' if case_id == 'mux_skid' else 'axis_zip'
    return {'version': ACQUISITION_VERSION, 'campaign_id': CAMPAIGN,
            'case_id': case_id, 'role': role,
            'expected_raw_digest': RAW_DIGEST,
            'expected_lineage_digest': LINEAGE_DIGEST,
            'expected_seal_sha256': raw_train.PACKAGES[group]['seal'],
            'source_role': 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV'}


def verify_acquisition(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError('v8 TRAIN acquisition must be a mapping')
    case, role = value.get('case_id'), value.get('role')
    if case not in CASES or role not in {'control', 'treatment'} or dict(value) != acquisition(case, role):
        raise ValueError('v8 TRAIN acquisition identity/role drift')
    checked = lineage.audit()
    if (checked['digest'] != LINEAGE_DIGEST or checked['raw_train_digest'] != RAW_DIGEST or
            checked['lineage_ids'] != sorted(item['repository'] for item in raw_train.SOURCES.values())):
        raise ValueError('v8 bounded source/measurement audit drift')
    return {**raw_train.SOURCES[case], 'case_id': case, 'role': role,
            'raw_train_digest': checked['raw_train_digest'],
            'lineage_audit_digest': checked['digest'],
            'package_seal_sha256': value['expected_seal_sha256'],
            'source_role': value['source_role']}


def _source(checked: Mapping) -> str:
    case = checked['case_id']
    group = 'mux' if case == 'mux_skid' else 'axis_zip'
    path = raw_train.TRAIN / raw_train.PACKAGES[group]['directory'] / (
        'bundle/source.sv' if group == 'mux' else 'bundle/inputs/' + case + '.v')
    data = path.read_bytes()
    if path.is_symlink() or _sha(data) != 'sha256:' + checked['fault_sha256']:
        raise ValueError('v8 TRAIN fault source drift')
    return data.decode('utf-8')


def _matrix(case: str) -> dict:
    group = 'mux' if case == 'mux_skid' else 'axis_zip'
    audit = raw_train._expected(group)
    return audit['matrix'] if group == 'mux' else audit['matrix'][case]


def _witness(checked: Mapping, payload: Mapping) -> dict:
    case = checked['case_id']
    if case == 'axis_register' and (TEST_IDS[case]['target'] != axis.TARGET_IDS or
                                    TEST_IDS[case]['preservation'] != axis.PRESERVATION_IDS or
                                    set(TEST_IDS[case]['target'] + TEST_IDS[case]['preservation']) != set(axis.IDS)):
        raise ValueError('v8 AXIS oracle test ID witness drift')
    witness = {
        'version': 'tehm-r5-oracle-instance-witness-v8',
        'role': checked['source_role'], 'case_id': case,
        'repository': checked['repository'], 'source_git_sha': checked['commit'],
        'source_file': checked['source_file'],
        'source_before_sha256': 'sha256:' + checked['fault_sha256'],
        'source_after_sha256': 'sha256:' + checked['candidate_sha256'],
        'public_context': copy.deepcopy(checked['public_context']),
        'action_payload_digest': _digest(dict(payload)),
        'oracle_kind': checked['oracle_kind'],
        'target_ids': list(TEST_IDS[case]['target']),
        'preservation_ids': list(TEST_IDS[case]['preservation']),
        'preregistered_target_obligation': OBLIGATIONS[case][0],
        'preregistered_preservation_obligation': OBLIGATIONS[case][1],
        'semantic_mapping': 'buffered_payload_value_after_backpressure',
        'raw_train_digest': checked['raw_train_digest'],
        'lineage_audit_digest': checked['lineage_audit_digest'],
        'train_package_seal_sha256': checked['package_seal_sha256'],
        'shared_contract_digest': _digest(MEASUREMENT_CONTRACT),
        'native_oracle_equivalence': False,
        'unseen_transfer': False,
    }
    witness['witness_digest'] = _digest(witness)
    return witness


def build_record(value: Mapping) -> ExecutionRecord:
    checked = verify_acquisition(value)
    case, role = checked['case_id'], checked['role']
    source = _source(checked)
    payload = payload_from_source_v8(source, checked['public_context'])
    candidate, edit = apply_rtl_action(source, payload)
    if (_sha(candidate.encode()) != 'sha256:' + checked['candidate_sha256'] or
            edit.get('rewritten') != 1 or edit.get('source_binding_rederived') is not True):
        raise ValueError('v8 action does not replay TRAIN candidate')
    matrix = _matrix(case)
    target_fail = 'FAIL_SECOND_DELIVERY' if case == 'mux_skid' else 'FAIL'
    if (matrix['fault']['target'] != target_fail or matrix['rollback']['target'] != target_fail
            or matrix['candidate']['target'] != 'PASS' or
            any(matrix[arm]['preservation'] != 'PASS' for arm in ('fault', 'candidate', 'rollback'))):
        raise ValueError('v8 raw TRAIN target or preservation is incomplete')
    before_result = {'verdict': 'FAIL', 'target_verdict': 'FAIL',
                     'preservation_verdict': 'PASS', 'test_count': 2,
                     'failed_count': 1, 'count_unit': 'behavioral_obligations'}
    repaired_result = {'verdict': 'PASS', 'target_verdict': 'PASS',
                       'preservation_verdict': 'PASS', 'test_count': 2,
                       'failed_count': 0, 'count_unit': 'behavioral_obligations'}
    is_control = role == 'control'
    after_result = before_result if is_control else repaired_result
    contract = copy.deepcopy(MEASUREMENT_CONTRACT)
    contract_digest = _digest(contract)
    witness = _witness(checked, payload)
    pair = {'contract': contract, 'contract_digest': contract_digest,
            'controlled_measurement_valid': True, 'native_oracle_equivalence': False}
    for side, result in (('before', before_result), ('after', repaired_result)):
        pair[side] = {'contract': contract, 'oracle_instance': checked['oracle_kind'],
                      'verdict': result['verdict'],
                      'target_verdict': result['target_verdict'],
                      'preservation_verdict': result['preservation_verdict'],
                      'source_sha256': 'sha256:' + checked['fault_sha256' if side == 'before' else 'candidate_sha256'],
                      'train_receipt_digest': checked['raw_train_digest'],
                      'oracle_instance_witness': copy.deepcopy(witness)}
    pair['receipt_digest'] = _digest(pair)
    scoped = {'version': SCOPED_VERSION, 'role': 'before' if is_control else 'after',
              'case_id': case, 'acquisition_digest': _digest(dict(value)),
              'train_receipt_digest': checked['raw_train_digest'],
              'lineage_receipt_digest': checked['lineage_audit_digest'],
              'pair_receipt': pair,
              'oracle_instance_witness_digest': witness['witness_digest']}
    run_tag = 'r5-train-v8-' + case + '-' + checked['package_seal_sha256'][-12:]
    config = {'public_context': copy.deepcopy(checked['public_context']),
              'compatibility_profile': PROFILE, 'source_file': checked['source_file']}

    def state(side: str, result: Mapping) -> dict:
        return {'config': copy.deepcopy(config), 'reports': {'target_oracle': {
            'scope': PROFILE, 'oracle_kind': checked['oracle_kind'],
            'verdict': result['verdict'], 'target_verdict': result['target_verdict'],
            'preservation_verdict': result['preservation_verdict'],
            'provenance': {'run_tag': run_tag + ('-candidate' if side == 'after' else '')}}},
            'artifacts': {'rtl_source_sha256': 'sha256:' + checked[
                'fault_sha256' if side == 'before' else 'candidate_sha256'],
                'train_package_seal_sha256': checked['package_seal_sha256'],
                'raw_train_digest': checked['raw_train_digest']}}

    before = state('before', before_result)
    before['failure_signature'] = {'mechanism_family': FAMILY,
                                   'failure': 'buffered_payload_source_mismatch'}
    after = copy.deepcopy(before) if is_control else state('after', repaired_result)
    action = {'domain': 'rtl.BASELINE_CONTROL' if is_control else DOMAIN,
              'transformation_family': FAMILY,
              'payload': {'control': True, 'observation_only': True,
                          'compatibility_profile': PROFILE,
                          'measurement_contract_digest': contract_digest}
              if is_control else payload}
    record = ExecutionRecord(
        record_id='r5-rtl-train-v8:' + _digest(scoped).split(':', 1)[1],
        domain='rtl.functional', project_id=checked['repository'],
        design_id=case, lineage_id=checked['repository'],
        repository_ref=checked['repository'] + '@' + checked['commit'],
        before=before, after=after, action=action,
        observation_delta={'original_failure': 'PRESENT' if is_control else 'REMOVED',
                           'failing_tests': {'before': 1, 'after': after_result['failed_count'],
                                             'count_unit': 'behavioral_obligations'},
                           'created_regressions': [], 'newly_observed_failures': [],
                           'experiment_kind': 'OBSERVATION' if is_control else 'REPAIR',
                           'utility_verdict': 'UNKNOWN'},
        verification={'verdict': after_result['verdict'], 'oracle_type': 'TARGET_TEST',
                      'scope': PROFILE, 'confidence_tier': 'T', 'oracle_complete': True,
                      'obligation_coverage': 1.0, 'extractor_version': SCOPED_VERSION,
                      'evidence_refs': [checked['raw_train_digest'], checked['lineage_audit_digest'],
                                        checked['package_seal_sha256'], witness['witness_digest']],
                      'tool_versions': {'source_group': checked['repository'],
                                        'train_package_seal_sha256': checked['package_seal_sha256']},
                      'full_oracle': {'before': {'complete': True, **before_result},
                                      'after': {'complete': True, **after_result}},
                      'scoped_execution': scoped})
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get('scoped_execution')
    if not isinstance(scoped, Mapping) or scoped.get('version') != SCOPED_VERSION:
        raise ValueError('unsupported v8 scoped record')
    case, side = scoped.get('case_id'), scoped.get('role')
    expected = build_record(acquisition(case, 'control' if side == 'before' else 'treatment'))
    if asdict(record) != asdict(expected):
        raise ValueError('v8 canonical record differs from raw TRAIN evidence')
    return expected.verification['scoped_execution']['pair_receipt']


def replay_persisted_rtl_train_v8(conn: sqlite3.Connection, transition_id: str,
                                  *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts
    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get('scoped_execution')
    if not isinstance(scoped, dict) or scoped.get('version') != SCOPED_VERSION:
        raise ValueError('transition lacks v8 scoped execution')
    _compare_persisted_flow_record(conn, transition_id, build_record(acquisition_data))
    return {'version': SCOPED_VERSION, 'transition_id': transition_id,
            'acquisition_digest': _digest(dict(acquisition_data)),
            'persisted_binding_verified': True, 'learner_admission': False,
            'promotion_attempted': False}


def oracle_for(checked: Mapping, record: ExecutionRecord, arm: str):
    if arm not in {'target', 'preservation'}:
        raise ValueError('unsupported v8 oracle obligation')
    pair = record.verification['scoped_execution']['pair_receipt']
    witness = pair['after']['oracle_instance_witness']
    if (witness['source_after_sha256'] != 'sha256:' + checked['candidate_sha256'] or
            witness['raw_train_digest'] != checked['raw_train_digest'] or
            witness['shared_contract_digest'] != _digest(MEASUREMENT_CONTRACT)):
        raise ValueError('v8 oracle instance mismatch')
    expected = record.verification['full_oracle']['after']

    def check(candidate: str, _asset: Mapping) -> dict:
        return {'verdict': 'PASS' if _sha(candidate.encode()) == witness['source_after_sha256']
                and expected['complete'] is True and expected[arm + '_verdict'] == 'PASS'
                else 'FAIL'}
    return check
