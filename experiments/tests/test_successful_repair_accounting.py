import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('successful_cost', Path(__file__).parents[1] / 'account_successful_repair_cost.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def attempt(tmp_path, label='run', created='2026-10-28T10:00:00+08:00', repair=True):
    root = tmp_path / label
    folder = root / 'cases/sky130hd_C00001'
    case = {'id': folder.name, 'platform': 'sky130hd', 'source_sha256': {'rtl.v': 'original'}}
    source = {'path': str(root), 'eligible': True, 'cores_per_design': 8, 'code_commit': 'frozen', 'host': '203'}
    state = {'status': 'complete', 'session_id': label, 'created': created}
    usage = {'calls': [{'usage_reported': True, 'stream_complete': True, 'total_tokens': 100}],
             'model_calls': 1, 'totals': {'total_tokens': 100}, 'known_reported_totals': {'total_tokens': 100}}
    drc = {'status': 'clean', 'total_violations': 0, 'exit_code': 0, 'drc_mode': 'full', 'run_tag': 'RUN_1', 'gds_sha256': 'gds'}
    lvs = {'status': 'clean', 'mismatch_count': 0, 'run_tag': 'RUN_1', 'gds_sha256': 'gds'}
    confirm = {'consensus': True, 'run_tag': 'RUN_1', 'gds_sha256': 'gds', 'def_sha256': 'def'}
    report_hashes = {n: 'report-' + n for n in ['drc.json', 'lvs.json']}
    proof = {'job_id': 'job1', 'project': '/selected/project', 'reports': {'drc.json': drc, 'lvs.json': lvs,
             'signoff_manifest.json': {'confirming_run': confirm, 'reports': {n: {'sha256': v} for n, v in report_hashes.items()}}},
             'artifact_sha256': {'gds': 'gds', 'def': 'def'}, 'report_sha256': report_hashes}
    save(root / 'launch_parameters.json', {'cores_per_design': 8, 'fixed_cores': True})
    save(folder / 'state.json', state)
    save(folder / 'input_receipt.json', {'sha256': {'rtl.v': 'original'}, 'historical_metadata_removed': True})
    save(folder / 'signoff_evidence.json', {'session_id': label, 'source_input_sha256': {'rtl.v': 'original'}, 'projects': [proof]})
    save(folder / 'summary.json', {'status': 'complete', 'session_id': label, 'job_ids': ['job1'], 'usage': usage,
         'orfs_audit_coverage': 'paired_observed_events', 'orfs_wrapper_calls': 4, 'orfs_make_launches': 22,
         'designs': [{'job_id': 'job1', 'project': '/selected/project', 'signoff': {'dual_clean': True}}]})
    save(folder / 'orfs_audit.json', {'coverage': 'paired_observed_events', 'orfs_wrapper_calls': 4, 'orfs_make_launches': 22})
    save(folder / 'job1.job.json', {'spec': {'session_id': label, 'platform': 'sky130hd', 'params': {}},
         'designs': [{'detail': {'verify': {'passed': 296, 'total': 297},
                     'repair': [{'strategy': 'generic', 'verdict': 'cleared'}] if repair else []}}]})
    return folder, case, source


def modify(path, change):
    obj = ledger.read(path)
    change(obj)
    save(path, obj)


def test_success_includes_all_normal_in_attempt_orfs_and_preserves_dataset_failure(tmp_path):
    folder, case, source = attempt(tmp_path)
    row, accepted = ledger.assess(folder, case, source)
    assert row['eligible']
    assert accepted['orfs_wrapper_calls'] == 4
    assert accepted['orfs_make_launches'] == 22
    assert accepted['provider_usage']['total_tokens'] == 100
    assert accepted['dataset_verify'] == {'passed': 296, 'total': 297}


def test_failed_debug_and_already_clean_do_not_inflate_repair_totals(tmp_path):
    folder, case, source = attempt(tmp_path, repair=False)
    manifest = tmp_path / 'master.json'
    save(manifest, {'candidates': [case]})
    debug_folder, _, debug_source = attempt(tmp_path, 'debug')
    debug_source.update(eligible=False, exclusion_reason='engineering')
    config = tmp_path / 'config.json'
    save(config, {'output': str(tmp_path / 'ledger'), 'manifests': [str(manifest)], 'sources': [source, debug_source]})
    result = ledger.render(config)['sky130hd']
    assert result['successful_repair_only']['cases'] == 0
    assert result['already_clean_separate']['provider_usage']['total_tokens'] == 100


def test_first_success_is_retained_instead_of_cheapest_retry(tmp_path):
    first, case, source1 = attempt(tmp_path, 'first')
    later, _, source2 = attempt(tmp_path, 'later', created='2026-10-28T11:00:00+08:00')
    modify(later / 'summary.json', lambda x: x['usage']['totals'].update(total_tokens=1))
    modify(later / 'summary.json', lambda x: x['usage']['calls'][0].update(total_tokens=1))
    manifest, config = tmp_path / 'master.json', tmp_path / 'config.json'
    save(manifest, {'candidates': [case]})
    save(config, {'output': str(tmp_path / 'ledger'), 'manifests': [str(manifest)], 'sources': [source2, source1]})
    ledger.render(config)
    accepted = ledger.read(tmp_path / 'ledger/accepted_attempts.json')[case['id']]
    assert accepted['session_id'] == 'first'
    assert accepted['provider_usage']['total_tokens'] == 100
    ledger.render(config)  # stable, idempotent receipt


def test_incomplete_usage_or_unpaired_audit_cannot_be_zero_cost_success(tmp_path):
    folder, case, source = attempt(tmp_path)
    modify(folder / 'summary.json', lambda x: x['usage']['calls'][0].update(stream_complete=False))
    assert ledger.assess(folder, case, source)[0]['reason'] == 'incomplete_provider_usage'
    modify(folder / 'summary.json', lambda x: x['usage']['calls'][0].update(stream_complete=True))
    modify(folder / 'summary.json', lambda x: x.update(orfs_audit_coverage='gaps_observed'))
    assert ledger.assess(folder, case, source)[0]['reason'] == 'incomplete_orfs_audit'


def test_same_layout_binding_and_original_inputs_are_required(tmp_path):
    folder, case, source = attempt(tmp_path)
    modify(folder / 'signoff_evidence.json', lambda x: x['source_input_sha256'].update({'rtl.v': 'repaired'}))
    assert ledger.assess(folder, case, source)[0]['reason'] == 'original_input_hash_or_identity_conflict'
    modify(folder / 'signoff_evidence.json', lambda x: x['source_input_sha256'].update({'rtl.v': 'original'}))
    modify(folder / 'signoff_evidence.json', lambda x: x['projects'][0]['reports']['lvs.json'].update(gds_sha256='other-gds'))
    assert ledger.assess(folder, case, source)[0]['reason'] == 'signoff_not_bound_to_same_archived_layout'


def test_zero_check_infrastructure_failure_requires_fresh_attempt(tmp_path):
    folder, case, source = attempt(tmp_path)
    modify(folder / 'job1.job.json', lambda x: x['designs'][0]['detail'].update(verify={'passed': 0, 'total': 0}))
    assert ledger.assess(folder, case, source)[0]['reason'] == 'verifier_infrastructure_failure_zero_checks'
