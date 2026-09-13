"""Independent cold replay of actual S0->S1->S2 evaluation preparation.

Uses no producer imports, no hardware/provider calls and no writes to source
databases. Negative SQL mutations occur only in disposable audit RAM copies.
"""
import argparse
import copy
import json
from pathlib import Path
import sqlite3

from tehm import db
from tehm.evolution import AppliedShadowUpdateReceipt
from tehm.evolution.gap_evaluation import GapEvaluationAdmissionReceipt, verify_gap_evaluation_admission
from scripts.audit_capability_gap_shadow_add import reference, verify_reference, load_report, digest, logical


def audit(report_path, output):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('new audit output must be absent')
    report = load_report(report_path)
    if report['version'] != 'core-gap-actual-disposable-evaluation-source-v1':
        raise ValueError('unexpected evaluation source contract')
    frozen = load_report(report['prospective_freeze']['path'])
    if frozen['current_runtime_binding'] != report['current_runtime_binding']:
        raise ValueError('prospective runtime differs from reported generation')
    refs = [reference(report_path), reference(__file__), report['prospective_freeze'],
        report['baseline_source'], report['actual_added_shadow_source'], report['evaluation_source'],
        *report['inputs']]
    for ref in refs: verify_reference(ref)
    warm = load_report(report['p13_report']['path'])
    p13 = AppliedShadowUpdateReceipt.from_dict(warm['applied_shadow_update'])
    receipt = GapEvaluationAdmissionReceipt.from_dict(report['evaluation_admission_receipt'])
    source = db.connect_read_only(report['baseline_source']['path'])
    added = db.connect_read_only(report['actual_added_shadow_source']['path'])
    evaluated = db.connect_read_only(report['evaluation_source']['path'])
    try:
        before = tuple(logical(c) for c in (source, added, evaluated))
        expected = tuple(report[k]['logical_digest'] for k in ('baseline_source', 'actual_added_shadow_source', 'evaluation_source'))
        if before != expected: raise ValueError('actual full SQL states differ from frozen S0/S1/S2')
        checked = verify_gap_evaluation_admission(source, added, evaluated, p13, receipt)
        if not checked['verified'] or not checked['eligible_for_disposable_evaluation']:
            raise ValueError('cold core evaluation preparation replay rejected: '+str(checked))
        if report['memory_delta'] != receipt.payload['memory_delta']:
            raise ValueError('composed delta differs from actual admission receipt')
        negatives = {}
        for kind in ('S1_substituted_for_S2', 'S2_substituted_for_S1', 'source_digest_relabel',
                     'asset_promoted', 'authority_deleted', 'unrelated_metadata_changed', 'new_table'):
            s1, s2, altered = added, evaluated, receipt
            copy_db = None
            try:
                if kind == 'S1_substituted_for_S2': s2 = added
                elif kind == 'S2_substituted_for_S1': s1 = evaluated
                elif kind == 'source_digest_relabel':
                    payload = copy.deepcopy(receipt.payload);payload['baseline_memory_digest'] = 'sha256:'+'0'*64
                    altered = GapEvaluationAdmissionReceipt(payload)
                else:
                    copy_db = sqlite3.connect(':memory:');copy_db.row_factory = sqlite3.Row
                    evaluated.backup(copy_db);s2 = copy_db
                    if kind == 'asset_promoted':
                        copy_db.execute("UPDATE tehm_asset_status SET status='promoted' WHERE asset_id=?", (receipt.payload['asset_id'],))
                    elif kind == 'authority_deleted':
                        copy_db.execute('DELETE FROM tehm_knowledge_authority_receipts WHERE authority_receipt_id=?',
                            (receipt.payload['current_status_v2_authority']['authority_receipt_id'],))
                    elif kind == 'new_table': copy_db.execute('CREATE TABLE audit_forbidden_extra(x TEXT)')
                    else: copy_db.execute("UPDATE tehm_meta SET value='audit_tampered' WHERE key='schema_version'")
                rejected = verify_gap_evaluation_admission(source, s1, s2, p13, altered)
                if rejected['verified'] or rejected['eligible_for_disposable_evaluation']:
                    raise ValueError('cold negative preparation accepted: '+kind)
                negatives[kind] = {'rejected': True, 'reasons': rejected['reasons']}
            finally:
                if copy_db is not None: copy_db.close()
        if before != tuple(logical(c) for c in (source, added, evaluated)):
            raise ValueError('cold audit mutated actual S0/S1/S2')
        for ref in refs: verify_reference(ref)
        result = {'version': 'core-gap-independent-evaluation-preparation-audit-v1', 'status': 'PASS',
            'inputs': refs, 'full_core_receipt_replayed': True, 'actual_S0_S1_S2_bound': True,
            'S1_is_not_S2': before[1] != before[2], 'core_verification': checked,
            'negative_rejections': negatives, 'source_full_states_unchanged': True,
            'provider_calls': 0, 'new_hardware_measurements': 0, 'learner_ingestion': False,
            'production_admission': False, 'promotion_attempted': False,
            'fresh_post_add_hardware_trials_established': False}
        result['report_digest'] = digest(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as stream: json.dump(result, stream, indent=2, sort_keys=True)
        return result
    finally:
        source.close();added.close();evaluated.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True);parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit(args.report, args.output)
    print(json.dumps({'status': result['status'], 'report_digest': result['report_digest'],
                      'negative_rejections': len(result['negative_rejections'])}), flush=True)


if __name__ == '__main__': main()
