"""RAM-only v8 TRAIN canonical, causal and strict Knowledge admission.

The six records reuse three registered DEV tasks and their sealed raw oracle
executions. This does not build M+, test unseen transfer, or run a simulator.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v8 as adapter
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture
from tehm.causal.intervention import build_intervention_pair
from tehm.causal.path_builder import build_transition_causal_fragment, consolidate_causal_path
from tehm.causal.replication import evaluate_replicated_effect
from tehm.knowledge import (build_knowledge_from_path, get_knowledge_status,
                            record_knowledge_authority, register_knowledge,
                            set_knowledge_status, verify_knowledge_authority)
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.verified_execution import require_verified_transition, scoped_learning_replay

RAM_CLOCK = '2026-09-27T00:00:00+00:00'
ROOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training')


def _software() -> dict:
    repo = Path(__file__).resolve().parents[3]
    paths = ('memory/tehm/adapters/research_r5_rtl_scoped_v8.py',
             'memory/tehm/evaluation/research_r5_rtl_scoped_v8_checks.py',
             'memory/tehm/verified_execution.py')
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1',
                             '--untracked-files=all'], capture_output=True,
                            text=True, check=True).stdout
    return {'git_head': head, 'tree_clean': not bool(status),
            'files_sha256': {name: adapter._sha((repo / name).read_bytes())
                             for name in paths}}


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, KeyError, TypeError):
        return True
    return False


def check() -> dict:
    with patch('tehm.db.now_local', return_value=RAM_CLOCK):
        return _check()


def _check() -> dict:
    with tempfile.TemporaryDirectory(prefix='r5-scoped-v8-') as temp:
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        recovered = sqlite3.connect(':memory:')
        recovered.row_factory = sqlite3.Row
        try:
            db.ensure_schema(conn)
            store = ArtifactStore(Path(temp) / 'artifacts')
            acquisitions, ids, records, witnesses, cases = {}, {}, {}, {}, {}
            for case in adapter.CASES:
                for role in ('control', 'treatment'):
                    acquisition = adapter.acquisition(case, role)
                    record = adapter.build_record(acquisition)
                    receipt = capture(conn, store, record, dataset_campaign_id=adapter.CAMPAIGN,
                                      dataset_split='training', dataset_learner_eligible=True)
                    ids[(case, role)] = receipt.transition_id
                    acquisitions[receipt.transition_id] = acquisition
                    records[(case, role)] = record
                    pair = record.verification['scoped_execution']['pair_receipt']
                    witness = pair['before']['oracle_instance_witness']
                    witnesses[(case, role)] = witness
                    cases[case + '/' + role + '/same_oracle_instance'] = (
                        pair['after']['oracle_instance_witness'] == witness and
                        witness['shared_contract_digest'] == pair['contract_digest'] and
                        witness['witness_digest'] == adapter._digest({
                            key: value for key, value in witness.items() if key != 'witness_digest'}))
                checked = adapter.verify_acquisition(adapter.acquisition(case, 'treatment'))
                source = adapter._source(checked)
                record = records[(case, 'treatment')]
                candidate, edit = apply_rtl_action(source, record.action['payload'])
                cases[case + '/source_action_and_exact_candidate_oracle'] = (
                    edit.get('rewritten') == 1 and edit.get('source_binding_rederived') is True and
                    adapter._sha(candidate.encode()) == 'sha256:' + checked['candidate_sha256'] and
                    adapter.oracle_for(checked, record, 'target')(candidate, {})['verdict'] == 'PASS' and
                    adapter.oracle_for(checked, record, 'preservation')(candidate, {})['verdict'] == 'PASS' and
                    adapter.oracle_for(checked, record, 'target')(source, {})['verdict'] == 'FAIL')
            cases['six_distinct_transitions'] = len(set(ids.values())) == 6
            cases['three_distinct_oracle_instances'] = (
                len({witnesses[(case, 'treatment')]['witness_digest'] for case in adapter.CASES}) == 3 and
                all(witnesses[(case, 'control')] == witnesses[(case, 'treatment')]
                    for case in adapter.CASES))
            contract = adapter.MEASUREMENT_CONTRACT
            contract_text = json.dumps(contract, sort_keys=True)
            cases['shared_contract_has_no_case_identity'] = (
                all(case not in contract_text for case in adapter.CASES) and
                contract['scope'] == adapter.PROFILE and
                contract['native_oracle_equivalence'] is False and
                contract['heldout_target_claim'] is False)
            cases['axis_nine_actual_ids'] = (
                len(witnesses[('axis_register', 'treatment')]['target_ids']) == 2 and
                len(witnesses[('axis_register', 'treatment')]['preservation_ids']) == 7 and
                'run_test_tuser_assert_001' in witnesses[('axis_register', 'treatment')]['preservation_ids'])
            cases['zipcpu_not_native_equivalence'] = (
                witnesses[('zipcpu_skidbuffer', 'treatment')]['oracle_kind'] ==
                'research_augmented_not_native')
            forged = copy.deepcopy(records[('axis_register', 'treatment')])
            forged.verification['scoped_execution']['pair_receipt']['before'][
                'oracle_instance_witness']['source_after_sha256'] = 'sha256:forged'
            cases['forged_oracle_instance_rejected'] = _reject(lambda: adapter.replay_record(forged))
            forged = copy.deepcopy(records[('mux_skid', 'treatment')])
            forged.verification['scoped_execution']['pair_receipt']['contract'][
                'common_obligation'] = 'anything'
            cases['forged_shared_contract_rejected'] = _reject(lambda: adapter.replay_record(forged))
            cases['forged_acquisition_rejected'] = _reject(lambda: adapter.verify_acquisition({
                **adapter.acquisition('mux_skid', 'treatment'),
                'expected_raw_digest': 'sha256:forged'}))
            cases['outside_ram_replay_context_rejected'] = _reject(lambda:
                require_verified_transition(conn, ids[('axis_register', 'treatment')]))
            with scoped_learning_replay(conn, campaign_id=adapter.CAMPAIGN,
                                        acquisitions=acquisitions,
                                        expected_digest=adapter._digest(acquisitions)):
                for transition_id in ids.values():
                    require_verified_transition(conn, transition_id)
                cases['six_scoped_transitions_verified'] = True
                pairs = [build_intervention_pair(
                    conn, ids[(case, 'control')], ids[(case, 'treatment')],
                    campaign_id=adapter.CAMPAIGN, target_scope=adapter.PROFILE)
                    for case in adapter.CASES]
                cases['three_controlled_pairs_l2'] = all(
                    pair.validity_status == 'VALID_CONTROLLED_PAIR' and
                    pair.evidence_level == 'L2_CONTROLLED_INTERVENTION' for pair in pairs)
                fragments = [build_transition_causal_fragment(
                    conn, transition_id, campaign_id=adapter.CAMPAIGN)
                    for transition_id in sorted(acquisitions)]
                path = consolidate_causal_path(conn, fragments, campaign_id=adapter.CAMPAIGN,
                                               status='shadow')
                replication = evaluate_replicated_effect(conn, path.path_id,
                                                         campaign_id=adapter.CAMPAIGN)
                cases['three_bounded_source_groups_l3'] = (
                    replication.eligible is True and
                    replication.evidence_level == 'L3_REPLICATED_EFFECT' and
                    set(replication.unique_lineages) == {
                        adapter.raw_train.SOURCES[case]['repository'] for case in adapter.CASES})
                knowledge = build_knowledge_from_path(conn, path.path_id)
                measurement = knowledge.intervention.get('measurement_contract', {})
                cases['knowledge_exact_shared_contract'] = (
                    measurement.get('contract_digest') == adapter._digest(contract) and
                    measurement.get('scope') == adapter.PROFILE)
                register_knowledge(conn, knowledge, target_scope=adapter.PROFILE)
                authority = record_knowledge_authority(conn, knowledge,
                                                       target_scope=adapter.PROFILE)
                cold = verify_knowledge_authority(conn, authority)
                conn.commit()
                conn.backup(recovered)
                cases['recovered_without_scoped_replay_rejected'] = not verify_knowledge_authority(
                    recovered, authority)['eligible']
                with scoped_learning_replay(recovered, campaign_id=adapter.CAMPAIGN,
                                            acquisitions=acquisitions,
                                            expected_digest=adapter._digest(acquisitions)):
                    cold_recovered = verify_knowledge_authority(recovered, authority)
                cases['strict_knowledge_authority'] = (
                    authority.eligible is True and cold['eligible'] is True and
                    cold_recovered['eligible'] is True)
                status_args = {'knowledge_id': knowledge.knowledge_id,
                               'version': knowledge.version, 'target_scope': adapter.PROFILE}
                cases['not_automatically_validated'] = get_knowledge_status(
                    conn, **status_args)['status'] == 'candidate'
                cases['no_receipt_validation_rejected'] = _reject(lambda:
                    set_knowledge_status(conn, **status_args, status='validated'))
                cases['boolean_validation_rejected'] = _reject(lambda:
                    set_knowledge_status(conn, **status_args, status='validated',
                                         authority_receipt={'eligible': True}))
                set_knowledge_status(conn, **status_args, status='validated',
                                     authority_receipt=authority,
                                     provenance={'purpose': 'v8_ram_only_research_admission'})
                cases['explicit_validation_in_ram'] = get_knowledge_status(
                    conn, **status_args)['status'] == 'validated'
                cases['spent_authority_receipt_rejected'] = not verify_knowledge_authority(
                    conn, authority)['eligible']
                swapped = dict(acquisitions)
                a, b = ids[('axis_register', 'treatment')], ids[('mux_skid', 'treatment')]
                swapped[a], swapped[b] = swapped[b], swapped[a]
                with scoped_learning_replay(conn, campaign_id=adapter.CAMPAIGN,
                                            acquisitions=swapped,
                                            expected_digest=adapter._digest(swapped)):
                    cases['swapped_acquisition_rejected'] = _reject(lambda:
                        require_verified_transition(conn, a))
                conn.execute('SAVEPOINT membership_negative')
                conn.execute("UPDATE tehm_dataset_membership SET split='heldout', learner_eligible=0 "
                             "WHERE transition_id=? AND campaign_id=?", (a, adapter.CAMPAIGN))
                cases['heldout_membership_rejected'] = _reject(lambda:
                    require_verified_transition(conn, a))
                conn.execute('ROLLBACK TO SAVEPOINT membership_negative')
                conn.execute('RELEASE SAVEPOINT membership_negative')
            return {'schema': 'tehm-r5-v8-canonical-knowledge-ram-checks-v1',
                    'valid': all(cases.values()), 'case_count': len(cases),
                    'failed': sorted(name for name, passed in cases.items() if not passed),
                    'cases': cases, 'role': 'TRAIN_RAM_ONLY_NOT_M_PLUS_NOT_TRANSFER',
                    'knowledge_object_id': knowledge.object_id,
                    'knowledge_authority_receipt_digest': authority.receipt_digest,
                    'knowledge_authority_eligible': authority.eligible,
                    'knowledge_cold_reasons': cold['reasons'],
                    'knowledge_recovered_reasons': cold_recovered['reasons'],
                    'replication_evidence_level': replication.evidence_level,
                    'source_groups': sorted(replication.unique_lineages),
                    'source_group_claim': 'bounded_pinned_source_groups_only',
                    'transition_ids': {case + '/' + role: transition_id
                                       for (case, role), transition_id in ids.items()},
                    'shared_contract_digest': adapter._digest(contract),
                    'memory_m_plus_constructed': False,
                    'ram_only_research_validation': True,
                    'heldout_transfer': False, 'model_calls': 0,
                    'new_simulator_executions': 0, 'persistent_memory_changed': False,
                    'ram_fixture_clock': RAM_CLOCK, 'software': _software(),
                    'raw_train_digest': adapter.RAW_DIGEST,
                    'lineage_audit_digest': adapter.LINEAGE_DIGEST,
                    'package_seal_sha256': {name: value['seal'] for name, value in
                                            adapter.raw_train.PACKAGES.items()}}
        finally:
            recovered.close()
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--output', type=Path)
    mode.add_argument('--verify', type=Path)
    args = parser.parse_args()
    path = args.output or args.verify
    if path is not None and (path.parent != ROOT or path.is_symlink() or
                             args.output is not None and path.exists() or
                             args.verify is not None and not path.is_file()):
        raise ValueError('v8 Knowledge RAM receipt must be a direct training child')
    result = check()
    if args.output is not None:
        if not result['valid'] or not result['software']['tree_clean']:
            raise ValueError('v8 Knowledge receipt requires valid clean software epoch')
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write('\n')
    elif args.verify is not None and json.loads(args.verify.read_bytes()) != result:
        raise ValueError('v8 Knowledge RAM receipt differs from cold replay')
    print(json.dumps({'valid': result['valid'], 'case_count': result['case_count'],
                      'failed': result['failed'],
                      'knowledge_object_id': result['knowledge_object_id'],
                      'knowledge_authority_eligible': result['knowledge_authority_eligible']},
                     sort_keys=True))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
