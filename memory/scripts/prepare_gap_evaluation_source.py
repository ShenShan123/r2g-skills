"""Freeze and prepare actual P13-derived disposable evaluation source memory.

This prepares S2, not production memory, and does not execute hardware. The
input must be the actual core P13 ADD report and its independent replay audit.
"""
import argparse
import json
from pathlib import Path

from tehm import db
from tehm.evolution import AppliedShadowUpdateReceipt
from tehm.evolution.gap_evaluation import prepare_gap_evaluation_shadow, verify_gap_evaluation_admission
from scripts.audit_capability_gap_shadow_add import load_report, reference, verify_reference, logical, digest
from scripts.build_p13_interference_policy_views import _runtime_code_binding


def prepare(p13_report, independent_audit, output):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('new evaluation preparation output must be absent')
    warm, audit = load_report(p13_report), load_report(independent_audit)
    if warm['version'] != 'r3-actual-core-gap-shadow-add-v1' or warm['status'] != 'ACTUAL_CORE_ADD_CANDIDATE_SHADOW_ONLY':
        raise ValueError('requires actual core P13 Knowledge/Asset ADD')
    p13 = AppliedShadowUpdateReceipt.from_dict(warm['applied_shadow_update'])
    if (audit['version'] != 'r3-independent-core-gap-shadow-add-replay-v1' or audit['status'] != 'PASS' or
            audit['core_receipt_digest'] != p13.receipt_digest or not all(audit['negative_rejections'].values()) or
            reference(p13_report) not in audit['inputs']):
        raise ValueError('independent replay must bind this actual core ADD')
    bound = _runtime_code_binding()
    refs = [reference(p13_report), reference(independent_audit), reference(__file__),
        warm['source_snapshot'], warm['shadow_snapshot'], *audit['inputs'], *bound['files']]
    for ref in refs: verify_reference(ref)
    source = db.connect_read_only(warm['source_snapshot']['path'])
    added = db.connect_read_only(warm['shadow_snapshot']['path'])
    try:
        before = (logical(source), logical(added))
        output.mkdir(parents=True, exist_ok=False)
        frozen = {'version': 'core-gap-evaluation-source-prospective-freeze-v1', 'inputs': refs,
            'current_runtime_binding': bound, 'p13_report': reference(p13_report),
            'independent_p13_replay': reference(independent_audit), 'p13_receipt_digest': p13.receipt_digest,
            'baseline_source': warm['source_snapshot'], 'actual_added_shadow_source': warm['shadow_snapshot'],
            'evaluation_only': True, 'production_admission': False, 'provider_calls': 0,
            'new_hardware_measurements': 0, 'frozen_before_evaluation_admission': True}
        frozen['report_digest'] = digest(frozen)
        with (output/'input-freeze.json').open('x') as stream: json.dump(frozen, stream, indent=2, sort_keys=True)
        captured = []
        receipt = prepare_gap_evaluation_shadow(source, added, p13, staging_artifact_sink=captured.append)
        if len(captured) != 1: raise ValueError('expected one actual isolated evaluation artifact')
        snapshot = output/'disposable-evaluation-source.sqlite'
        with snapshot.open('xb') as stream: stream.write(captured[0])
        evaluated = db.connect_read_only(snapshot)
        try:
            verified = verify_gap_evaluation_admission(source, added, evaluated, p13, receipt)
            if not verified['verified'] or not verified['eligible_for_disposable_evaluation']:
                raise ValueError('actual evaluated artifact failed core independent admission replay: '+str(verified))
            evaluated_ref = {**reference(snapshot), 'logical_digest': logical(evaluated)}
        finally: evaluated.close()
        if before != (logical(source), logical(added)): raise ValueError('immutable S0/S1 changed')
        for ref in refs: verify_reference(ref)
        if _runtime_code_binding() != bound: raise ValueError('runtime changed during preparation')
        result = {'version': 'core-gap-actual-disposable-evaluation-source-v1', 'status': 'PASS',
            'prospective_freeze': reference(output/'input-freeze.json'), 'inputs': refs,
            'current_runtime_binding': bound, 'p13_report': reference(p13_report),
            'baseline_source': warm['source_snapshot'], 'actual_added_shadow_source': warm['shadow_snapshot'],
            'evaluation_source': evaluated_ref, 'evaluation_admission_receipt': receipt.to_dict(),
            'core_verification': verified, 'memory_delta': receipt.payload['memory_delta'],
            'source_full_state_unchanged': True, 'p13_added_shadow_full_state_unchanged': True,
            'evaluation_only': True, 'production_admission': False, 'promotion_attempted': False,
            'provider_calls': 0, 'new_hardware_measurements': 0, 'learner_ingestion': False,
            'fresh_post_add_hardware_trials_established': False,
            'required_next_evidence': ['fresh owned-policy trials on S0/S2/removal',
                'independent execution replay and actual AntiForgettingWitness',
                'reason-aware actual P13/composed-delta P14 attribution', 'P15/P16/P17 closure']}
        result['report_digest'] = digest(result)
        with (output/'evaluation-source-report.json').open('x') as stream: json.dump(result, stream, indent=2, sort_keys=True)
        return result
    finally: source.close();added.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p13-report', required=True)
    parser.add_argument('--independent-audit', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = prepare(args.p13_report, args.independent_audit, args.output)
    print(json.dumps({'status': result['status'], 'report_digest': result['report_digest'],
        'admission_receipt_digest': result['evaluation_admission_receipt']['receipt_digest'],
        'S1_differs_from_S2': result['actual_added_shadow_source']['logical_digest'] != result['evaluation_source']['logical_digest']}), flush=True)


if __name__ == '__main__': main()
