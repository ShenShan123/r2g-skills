"""Record source review and label evidence without replacing the draft split."""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gnn-node'))
from stage_data import digest, save_json, validate_manifest


REVIEW = {
    'async_fifo': 'Separate single-file binary/Gray FIFO and SV FIFO with gray_counter helper; distinct reset/interfaces.',
    'control_top': 'AES/SHA control modules share source and are already in one group.',
    'dma_engine': 'AXI-lite/FIFO flash DMA versus simple request/ack memory copier; distinct interfaces and implementation sizes.',
    'i2c_master': 'Forencich, CircuitDen/Isagholian and Reimer implementations have distinct interfaces and control structure.',
    'serv_rf_top': 'SERV variants share modules and are already in one group.',
    'spi_master': 'Parameterized SPI mode/byte handshake implementation versus start/busy fixed-clock-divider implementation.',
    'spi_top': 'FSM/package-based SPI versus 12-bit CPOL/CPHA master/slave loopback; distinct closures.',
    'top': 'Time serialization, UART loopback, systolic array and door controller are distinct designs despite generic name.',
    'tt_um_example': 'Shared TinyTapeout wrapper name: interconnect/control and SHA block have distinct source closures; wrapper naming alone is not ancestry evidence.',
    'tt_um_tt06_pwm': 'PWM revisions already grouped together, despite different saturation handling.',
    'uart': 'Forencich revisions grouped; receive-only FSM, Ultraembedded and Casear98 dual-clock implementation remain distinct.',
    'uart_rx': 'Forencich, impl_top UART and Matias Wang Silva implementations remain distinct; shared variants grouped.',
    'uart_top': 'Single parameterized 16x baud pulse/parity receiver versus separate TX/RX enables and nonparity receiver.',
    'uart_tx': 'Forencich, impl_top, matt-alencar and start/done fixed-divider implementations remain distinct.',
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--preparation', type=Path, required=True)
    p.add_argument('--export', type=Path, required=True)
    args = p.parse_args()
    root = args.preparation
    draft = root/'manifest.json'
    manifest = json.loads(draft.read_text())
    review = json.loads((root/'source_review.json').read_text())
    same_top = json.loads((args.export/'family_review.json').read_text())['same_top_across_groups_review_only']
    if set(REVIEW) != {r['top_module'] for r in same_top}:
        raise ValueError('Same-top review changed')
    for files in review['source_evidence'].values():
        for f in files:
            if digest(f['path']) != f['sha256']:
                raise ValueError('Reviewed source changed')
    audit_path = root/'lineage_audit_all_v2/summary.json'
    audit = json.loads(audit_path.read_text())
    ids = {r['design_id'] for r in manifest['records']}
    if (audit['manifest_sha256'] != digest(draft)
            or set(audit['reports']) != ids or audit['designs'] != len(ids)):
        raise ValueError('Independent label audit is not complete for the draft')
    final = copy.deepcopy(manifest)
    congestion = json.loads(Path(manifest['congestion_audit']['path']).read_text())
    congestion_rows = {r['design_id']: r for r in congestion['records']}
    report_refs, repaired, valid_labels = {}, [], 0
    for index, record in enumerate(manifest['records']):
        key = record['design_id']
        path = root/'lineage_audit_all_v2'/key/'report.json'
        report = json.loads(path.read_text())
        if digest(path) != audit['reports'][key]:
            raise ValueError('Label report changed')
        if report['graphs'] != record['graphs']:
            raise ValueError('Label report belongs to different graphs')
        module = next(iter(json.loads(path.with_name('yosys.json').read_text())['modules'].values()))
        nonstandard = any(s.get('offset', 0) or s.get('upto', 0)
                          for kind in ('netnames', 'ports') for s in module[kind].values())
        if report['status'] != 'PASS' or nonstandard:
            repair_root = root/'bus_index_repair_v1'
            state = json.loads((repair_root/'checkpoints'/(key+'.json')).read_text())
            if state['status'] != 'PASS' or state['previous_record'] != record:
                raise ValueError('Missing matching repair evidence')
            updated = state['record']
            if any(updated[k] != record[k] for k in ('design_id', 'split', 'family_id', 'source_sha256')):
                raise ValueError('Repair changed design grouping/split')
            final['records'][index] = updated
            congestion_rows[key] = state['congestion_audit']
            path = repair_root/'lineage_audit'/key/'report.json'
            report = json.loads(path.read_text())
            if digest(path) != state['wire_audit_sha256'] or report['status'] != 'PASS':
                raise ValueError('Repaired wire audit failed')
            repaired.append(key)
        report_refs[key] = {'path': str(path), 'sha256': digest(path)}
        valid_labels += report['valid_labels']
    note = dict(status='review_complete', review_method='source-only; no model test scores',
        source_review_sha256=digest(root/'source_review.json'),
        same_top_evidence_sha256=digest(args.export/'family_review.json'),
        added_joins=review['added_joins'], same_top_review=REVIEW,
        decision='Accept existing conservative source groups, preserve all draft assignments.',
        limits='Repository, exact normalized file and whole-closure structural similarity screening plus same-top source review; not formal equivalence or proof of no remote ancestry.')
    out = root/'ready_v1'
    if out.exists():
        raise FileExistsError('Keep prior readiness evidence; use a new version for changes')
    out.mkdir()
    save_json(out/'source_review_decisions.json', note)
    save_json(out/'wirelength_audit.json', dict(status='PASS', designs=len(ids), valid_labels=valid_labels,
              reports=report_refs, repaired_designs=repaired, original_audit_sha256=digest(audit_path)))
    congestion['records'] = [congestion_rows[r['design_id']] for r in final['records']]
    save_json(out/'congestion_audit.json', congestion)
    final.update(family_reviewed=True, formal_training_ready=True,
        group_review={'path': str(out/'source_review_decisions.json'),
                      'sha256': digest(out/'source_review_decisions.json')},
        wirelength_audit={'path': str(out/'wirelength_audit.json'), 'sha256': digest(out/'wirelength_audit.json')},
        congestion_audit={'path': str(out/'congestion_audit.json'), 'sha256': digest(out/'congestion_audit.json')},
        draft_manifest={'path': str(draft), 'sha256': digest(draft)})
    validate_manifest(final)
    save_json(out/'manifest.json', final)
    save_json(out/'receipt.json', dict(status='PASS', designs=len(ids),
        split_unchanged=True, independent_wirelength_labels=valid_labels, repaired_designs=repaired,
        model_test_evaluated=False, manifest_sha256=digest(out/'manifest.json')))
    print(json.dumps(dict(status='PASS', manifest=str(out/'manifest.json'))))


if __name__ == '__main__':
    main()
