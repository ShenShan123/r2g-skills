"""Report corpus readiness without treating software pilots as paper results."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--ready', action='store_true')
    args = p.parse_args()
    r = args.root
    evidence_root = r.parent if args.ready else r
    manifest = read(r/'manifest.json')
    coverage = {c['design_id']:c for c in read(evidence_root/'coverage.json')['records']}
    designs = []
    for record in manifest['records']:
        generated = Path(record['graphs']['route']['path']).parents[2]
        stats = read(generated/'statistics/four_stage_data_statistics.json')
        counts = stats['stages']['route']['node_counts']
        columns = {c['column']: c for c in stats['column_statistics']
                   if c['stage'] == 'route' and c['tensor'] == 'y'}
        labels = {t: {'valid': columns[column]['mask_valid_count']}
                  for t, column in [('wirelength', 'routed_wirelength_um'), ('congestion', 'cell_congestion')]}
        row = {k: record[k] for k in ('design_id','family_id','split','mapped_cells')}
        row.update({k+'_nodes':counts[k] for k in ('gate','net','pin','io_pin')})
        row.update({k+'_valid': labels[k]['valid'] for k in ('wirelength','congestion')})
        row['coverage_review_flag'] = coverage[record['design_id']]['review_flag']
        designs.append(row)
    with (r/'design_statistics.csv').open('w', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(designs[0]))
        writer.writeheader();writer.writerows(designs)
    numeric = [k for k in designs[0] if k.endswith(('_nodes','_valid'))]
    scale=[]
    for split in ('train','validation','test','all'):
        rows=[d for d in designs if split=='all' or d['split']==split]
        scale.append(dict(split=split, designs=len(rows),
            source_groups=len({d['family_id'] for d in rows}),
            mapped_cells_min=min(d['mapped_cells'] for d in rows),
            mapped_cells_max=max(d['mapped_cells'] for d in rows),
            **{k:sum(d[k] for d in rows) for k in numeric}))
    with (r/'dataset_scale.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(scale[0]));writer.writeheader();writer.writerows(scale)
    save(r/'dataset_scale.json', dict(rows=scale, labels_counted_once_across_stages=True,
         scope='Common eligible subset of 280 exported designs; not all experiment-one outputs.',
         grouping='Conservative source groups, not a formal equivalence assertion.',
         formal_test_evaluated=False))
    source = read(evidence_root/'source_review.json')
    save(r/'grouping_checks.json', dict(
        split_overlaps={k: [v for v in {d[k] for d in manifest['records']}
            if len({d['split'] for d in manifest['records'] if d[k]==v})>1]
            for k in ('family_id','source_sha256')},
        previous_development_outside_train=[d['design_id'] for d in manifest['records']
            if d['design_id'] in manifest['forced_training_designs'] and d['split']!='train'],
        source_pairs_checked=source['pairs_checked'], added_group_joins=len(source['added_joins'])))
    if (r/'pilots/status.json').exists():
        status=read(r/'pilots/status.json')
        results=[]
        for p in sorted((r/'pilots').glob('*/result.json')):
            d=read(p)
            results.append(dict(configuration=p.parent.name,status=d['status'],
                test_evaluated=d['test_evaluated'],parameters=d['parameters'],
                validation_macro_mae=d['validation']['macro_mae'],
                unit=d['validation']['unit']))
        save(r/'pilot_summary.json',dict(status=status,records=results,
            purpose='Software acceptance only; not a formal accuracy comparison or proof of GNN superiority.'))
    paths=[r/n for n in (('manifest.json','source_review_decisions.json', 'wirelength_audit.json',
                         'congestion_audit.json','dataset_scale.json') if args.ready else
                        ('manifest.json','pilot_manifest.json','source_review.json',
                         'coverage.json','wire_audit.json','congestion_audit.json','dataset_scale.json'))]
    paths+=list(Path(__file__).parent.glob('*downstream*training*.py'))
    save(r/'preparation_receipt.json',dict(sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        graphs_modified=bool(args.ready and read(r/'receipt.json')['repaired_designs']),
        original_exports_overwritten=False, physical_runs_started=False, official_experiment_scores_modified=False))
    print(json.dumps(scale,indent=2))


if __name__ == '__main__':
    main()
