"""Re-export selected graph artifacts, retaining all original exports and ORFS results."""
import argparse
import copy
import json
from pathlib import Path
import sys

import export_downstream_corpus as corpus
import export_downstream_pilot as pilot
import run_experiment4_graph_conversion as conversion
from audit_downstream_lineage import audit
from congestion_audit import audit_record
from stage_data import digest, save_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--export', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--design-id', action='append', required=True)
    p.add_argument('--yosys', type=Path, required=True)
    args = p.parse_args()
    plan = json.loads((args.export/'plan.json').read_text())
    rows = {r['task_id']: r for r in plan['rows']}
    records = {r['design_id']: r for r in json.loads(args.manifest.read_text())['records']}
    conversion.run_command = pilot.bounded_command
    identity = {str(p): digest(p) for p in args.runtime.rglob('*.py')}
    identity[str(args.manifest)] = digest(args.manifest)
    for key in args.design_id:
        previous = records[key]
        checkpoint = args.output/'checkpoints'/(key+'.json')
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text())
            if saved['identity'] != identity or saved['status'] != 'PASS':
                raise ValueError('Repair checkpoint changed or failed')
            print(key+': already verified', flush=True)
            continue
        link = args.output/'inputs'/key
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(args.export/'inputs'/key, target_is_directory=True)
        elif link.resolve() != (args.export/'inputs'/key).resolve():
            raise ValueError('Unexpected raw input link')
        print(key+': convert from existing raw artifacts', flush=True)
        result = conversion.run_one(rows[key], args.output, args.runtime, Path(sys.executable),
                                    'r2g-frozen-v3', None, 900)
        if result['status'] != 'completed':
            raise ValueError('Conversion failed')
        score = conversion.evaluate_one(rows[key], args.output, args.runtime, Path(sys.executable),
                                        'r2g-frozen-v3', 900)
        if not score['strict_pass']:
            raise ValueError('Structural validation failed')
        record, counts = corpus.make_record(args.output, rows[key], plan['encoding_sha256'])
        record, congestion = audit_record(record)
        for field in ('family_id', 'split'):
            record[field] = previous[field]
        wire = audit(record, args.output/'lineage_audit', args.yosys)
        if wire['status'] != 'PASS' or congestion['status'] != 'PASS':
            raise ValueError('Independent label audit failed')
        save_json(checkpoint, dict(status='PASS', record=record, label_counts=counts,
            congestion_audit=congestion, wire_audit_sha256=digest(args.output/'lineage_audit'/key/'report.json'),
            previous_record=previous, identity=identity, orfs_rerun=False))
        print(key+': PASS', flush=True)


if __name__ == '__main__':
    main()
