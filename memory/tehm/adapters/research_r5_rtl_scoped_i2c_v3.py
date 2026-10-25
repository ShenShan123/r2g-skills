"""Canonical I2C NACK v3 TRAIN records derived from the sealed four-source executions.

Contract: memory/evaluation/research_r5_i2c_nack_v3_train_generation_contract_20260929.md (2d),
reusing research_r5_i2c_nack_v2_knowledge_contract_20260928.md.
Control and treatment reuse researcher-assisted reused-DEV TRAIN tasks. They are
training witnesses, not new oracle runs, unseen transfer, or autonomous repair.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import sqlite3

from tehm.assets import r5_train_evidence_i2c_v3 as evidence
from tehm.assets import r5_train_lineage_i2c_v3 as lineage
from tehm.assets import r5_train_raw_i2c_v3 as raw_train
from tehm.canonical.capture import ExecutionRecord
from tehm.ids import stable_dumps
from tehm.rtl.i2c_nack_action_v3 import DOMAIN, PROFILE, payload_from_source_i2c_v3
from tehm.rtl.rtl_actions import apply_rtl_action

ACQUISITION_VERSION = 'tehm-r5-rtl-train-acquisition-i2c-v3'
SCOPED_VERSION = 'tehm-r5-rtl-train-scoped-i2c-v3'
CAMPAIGN = 'r5-rtl-i2c-nack-train-generation-v3'
FAMILY = 'I2C_NACK_STATUS_LATCH'
SOURCE_ROLE = 'RESEARCHER_ASSISTED_TRAIN_REUSED_DEV_V3'
CASES = raw_train.CASE_ORDER
RAW_DIGEST = 'sha256:30851e109592f30f9a7765c1e2a4789b1b27a8ab17dc9e5db32202e74af49040'
LINEAGE_DIGEST = 'sha256:925d46ee18e40d1a79859c498b6df09525c7b38369db7daee2b211f48f6e196b'
MEASUREMENT_CONTRACT = {
    'version': 'tehm-r5-i2c-nack-measurement-v3',
    'scope': PROFILE,
    'mechanism_family': FAMILY,
    'common_obligation': 'nack_from_non_acknowledging_device_is_reported_in_public_status',
    'preservation_required': True,
    'per_task_oracle_instance_witness_required': True,
    'native_oracle_equivalence': False,
    'heldout_target_claim': False,
    'production_authority': False,
}
# Sealed-execution scope labels from the TRAIN contract oracle table, not private test IDs.
OBLIGATIONS = {
    'alex': ('phase5_absent_device_got_missed_ack', 'phases_1_to_4_present'),
    'zip': ('nack_status_pass', 'original_read_write_checks_and_success'),
    'freecores': ('check_for_nack_without_expected_nack_error',
                  'received_a5_received_5a_testbench_done'),
    'chance189': ('aug_nack_latched_after_absent_address_write',
                  'aug_p1_write_and_p3_repeated_start_read_exact'),
}
SOURCE_FILES = {
    'alex': ('rtl/i2c_master.v',),
    'zip': ('rtl/wbi2cmaster.v',),
    'freecores': ('rtl/verilog/i2c_master_top.v', 'rtl/verilog/i2c_master_byte_ctrl.v',
                  'rtl/verilog/i2c_master_bit_ctrl.v', 'rtl/verilog/i2c_master_defines.v'),
    'chance189': ('i2c_master.v',),
}


def _digest(value: object) -> str:
    return 'sha256:' + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def acquisition(case_id: str, role: str) -> dict:
    if case_id not in CASES or role not in {'control', 'treatment'}:
        raise ValueError('unsupported I2C v3 TRAIN case or role')
    return {'version': ACQUISITION_VERSION, 'campaign_id': CAMPAIGN,
            'case_id': case_id, 'role': role,
            'expected_raw_digest': RAW_DIGEST,
            'expected_lineage_digest': LINEAGE_DIGEST,
            'expected_train_receipt_sha256': raw_train.RECEIPT_SHA,
            'source_role': SOURCE_ROLE}


def verify_acquisition(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError('I2C v3 TRAIN acquisition must be a mapping')
    case, role = value.get('case_id'), value.get('role')
    if case not in CASES or role not in {'control', 'treatment'} or dict(value) != acquisition(case, role):
        raise ValueError('I2C v3 TRAIN acquisition identity/role drift')
    checked_raw = raw_train.verify()
    checked = lineage.audit()
    if (checked_raw['digest'] != RAW_DIGEST or checked['digest'] != LINEAGE_DIGEST or
            checked['valid'] is not True or
            checked['train_receipt_sha256'] != raw_train.RECEIPT_SHA or
            checked['lineage_ids'] != sorted(item['repository'] for item in raw_train.SOURCES.values())):
        raise ValueError('I2C v3 raw TRAIN or bounded source audit drift')
    return {**copy.deepcopy(raw_train.SOURCES[case]), 'case_id': case, 'role': role,
            'source_files': list(SOURCE_FILES[case]),
            'raw_train_digest': checked_raw['digest'],
            'lineage_audit_digest': checked['digest'],
            'train_receipt_sha256': 'sha256:' + raw_train.RECEIPT_SHA,
            'source_role': value['source_role']}


def _source(checked: Mapping) -> str:
    # train_source() re-checks every role hash against the sealed fault inputs.
    return evidence.train_source(checked['case_id'])


def _matrix(case: str) -> dict:
    return raw_train.EXPECTED_MATRIX[case]


def _witness(checked: Mapping, payload: Mapping, source: str, candidate: str) -> dict:
    case = checked['case_id']
    witness = {
        'version': 'tehm-r5-oracle-instance-witness-i2c-v3',
        'role': checked['source_role'], 'case_id': case,
        'repository': checked['repository'], 'source_git_sha': checked['commit'],
        'source_files': list(checked['source_files']),
        'source_before_sha256': _sha(source.encode()),
        'source_after_sha256': _sha(candidate.encode()),
        'source_before_role_sha256': copy.deepcopy(checked['fault_sha256']),
        'source_after_role_sha256': copy.deepcopy(checked['candidate_sha256']),
        'closure_transport': 'tehm-closure-v1' if case == 'freecores' else 'single_file',
        'public_context': copy.deepcopy(checked['public_context']),
        'action_payload_digest': _digest(dict(payload)),
        'oracle_kind': checked['oracle_kind'],
        'preregistered_target_obligation': OBLIGATIONS[case][0],
        'preregistered_preservation_obligation': OBLIGATIONS[case][1],
        'semantic_mapping': 'nack_reported_in_public_status',
        'raw_train_digest': checked['raw_train_digest'],
        'lineage_audit_digest': checked['lineage_audit_digest'],
        'train_receipt_sha256': checked['train_receipt_sha256'],
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
    payload = payload_from_source_i2c_v3(source, checked['public_context'])
    candidate, edit = apply_rtl_action(source, payload)
    if (not raw_train._equal(evidence._source_hashes(case, candidate), checked['candidate_sha256']) or
            edit.get('rewritten') != 1 or edit.get('source_binding_rederived') is not True):
        raise ValueError('I2C v3 action does not replay TRAIN candidate')
    matrix = _matrix(case)
    if (matrix['fault']['target'] != 'FAIL' or matrix['rollback']['target'] != 'FAIL'
            or matrix['candidate']['target'] != 'PASS' or
            any(matrix[arm]['preservation'] != 'PASS' for arm in ('fault', 'candidate', 'rollback'))):
        raise ValueError('I2C v3 raw TRAIN target or preservation is incomplete')
    before_sha, after_sha = _sha(source.encode()), _sha(candidate.encode())
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
    witness = _witness(checked, payload, source, candidate)
    pair = {'contract': contract, 'contract_digest': contract_digest,
            'controlled_measurement_valid': True, 'native_oracle_equivalence': False}
    for side, result in (('before', before_result), ('after', repaired_result)):
        pair[side] = {'contract': contract, 'oracle_instance': checked['oracle_kind'],
                      'verdict': result['verdict'],
                      'target_verdict': result['target_verdict'],
                      'preservation_verdict': result['preservation_verdict'],
                      'source_sha256': before_sha if side == 'before' else after_sha,
                      'train_receipt_digest': checked['raw_train_digest'],
                      'oracle_instance_witness': copy.deepcopy(witness)}
    pair['receipt_digest'] = _digest(pair)
    scoped = {'version': SCOPED_VERSION, 'role': 'before' if is_control else 'after',
              'case_id': case, 'acquisition_digest': _digest(dict(value)),
              'train_receipt_digest': checked['raw_train_digest'],
              'lineage_receipt_digest': checked['lineage_audit_digest'],
              'pair_receipt': pair,
              'oracle_instance_witness_digest': witness['witness_digest']}
    run_tag = 'r5-train-i2c-v3-' + case + '-' + raw_train.RECEIPT_SHA[-12:]
    config = {'public_context': copy.deepcopy(checked['public_context']),
              'compatibility_profile': PROFILE, 'source_files': list(checked['source_files'])}

    def state(side: str, result: Mapping) -> dict:
        return {'config': copy.deepcopy(config), 'reports': {'target_oracle': {
            'scope': PROFILE, 'oracle_kind': checked['oracle_kind'],
            'verdict': result['verdict'], 'target_verdict': result['target_verdict'],
            'preservation_verdict': result['preservation_verdict'],
            'provenance': {'run_tag': run_tag + ('-candidate' if side == 'after' else '')}}},
            'artifacts': {'rtl_source_sha256': before_sha if side == 'before' else after_sha,
                          'train_receipt_sha256': checked['train_receipt_sha256'],
                          'raw_train_digest': checked['raw_train_digest']}}

    before = state('before', before_result)
    before['failure_signature'] = {'mechanism_family': FAMILY,
                                   'failure': 'nack_not_reported_in_public_status'}
    after = copy.deepcopy(before) if is_control else state('after', repaired_result)
    action = {'domain': 'rtl.BASELINE_CONTROL' if is_control else DOMAIN,
              'transformation_family': FAMILY,
              'payload': {'control': True, 'observation_only': True,
                          'compatibility_profile': PROFILE,
                          'measurement_contract_digest': contract_digest}
              if is_control else payload}
    record = ExecutionRecord(
        record_id='r5-rtl-train-i2c-v3:' + _digest(scoped).split(':', 1)[1],
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
                                        checked['train_receipt_sha256'], witness['witness_digest']],
                      'tool_versions': {'source_group': checked['repository'],
                                        'train_receipt_sha256': checked['train_receipt_sha256']},
                      'full_oracle': {'before': {'complete': True, **before_result},
                                      'after': {'complete': True, **after_result}},
                      'scoped_execution': scoped})
    record.validate()
    return record


def replay_record(record: ExecutionRecord) -> dict:
    record.validate()
    scoped = record.verification.get('scoped_execution')
    if not isinstance(scoped, Mapping) or scoped.get('version') != SCOPED_VERSION:
        raise ValueError('unsupported I2C v3 scoped record')
    case, side = scoped.get('case_id'), scoped.get('role')
    expected = build_record(acquisition(case, 'control' if side == 'before' else 'treatment'))
    if asdict(record) != asdict(expected):
        raise ValueError('I2C v3 canonical record differs from raw TRAIN evidence')
    return expected.verification['scoped_execution']['pair_receipt']


def replay_persisted_rtl_train_i2c_v3(conn: sqlite3.Connection, transition_id: str,
                                      *, acquisition_data: Mapping) -> dict:
    from tehm.adapters.orfs_scoped import _compare_persisted_flow_record
    from tehm.causal.mechanism import load_transition_facts
    facts = load_transition_facts(conn, transition_id)
    scoped = facts.verifier.get('scoped_execution')
    if not isinstance(scoped, dict) or scoped.get('version') != SCOPED_VERSION:
        raise ValueError('transition lacks I2C v3 scoped execution')
    _compare_persisted_flow_record(conn, transition_id, build_record(acquisition_data))
    return {'version': SCOPED_VERSION, 'transition_id': transition_id,
            'acquisition_digest': _digest(dict(acquisition_data)),
            'persisted_binding_verified': True, 'learner_admission': False,
            'promotion_attempted': False}


def oracle_for(checked: Mapping, record: ExecutionRecord, arm: str):
    if arm not in {'target', 'preservation'}:
        raise ValueError('unsupported I2C v3 oracle obligation')
    pair = record.verification['scoped_execution']['pair_receipt']
    witness = pair['after']['oracle_instance_witness']
    if (not raw_train._equal(witness['source_after_role_sha256'], checked['candidate_sha256']) or
            witness['raw_train_digest'] != checked['raw_train_digest'] or
            witness['shared_contract_digest'] != _digest(MEASUREMENT_CONTRACT)):
        raise ValueError('I2C v3 oracle instance mismatch')
    expected = record.verification['full_oracle']['after']

    def check(candidate: str, _asset: Mapping) -> dict:
        return {'verdict': 'PASS' if _sha(candidate.encode()) == witness['source_after_sha256']
                and expected['complete'] is True and expected[arm + '_verdict'] == 'PASS'
                else 'FAIL'}
    return check
