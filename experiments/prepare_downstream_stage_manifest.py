"""Add audited floorplan and placement graph references to a formal manifest."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


STAGES = {
    'floorplan': 'post_yosys',
    'placement': 'floorplan',
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()

    parent = json.loads(args.parent.read_text())
    if not parent.get('formal_training_ready') or parent.get('pilot_subset'):
        raise ValueError('Expected the reviewed formal manifest')

    validation_cache = {}
    counts = {stage: 0 for stage in STAGES}
    for record in parent['records']:
        cts_path = Path(record['graphs']['cts']['path'])
        stages_root = cts_path.parents[1]
        validation_path = Path(record['graphs']['cts']['validation_path'])
        validation_hash = record['graphs']['cts']['validation_sha256']
        if validation_path not in validation_cache:
            if sha(validation_path) != validation_hash:
                raise ValueError(f'Validation evidence changed: {validation_path}')
            evidence = json.loads(validation_path.read_text())
            if evidence.get('status') != 'PASS':
                raise ValueError(f'Validation did not pass: {validation_path}')
            validation_cache[validation_path] = evidence
        for stage, cutoff in STAGES.items():
            graph_path = stages_root / stage / 'heterograph.pt'
            metadata_path = stages_root / stage / 'heterograph.metadata.json'
            if not graph_path.is_file() or not metadata_path.is_file():
                raise FileNotFoundError(f'Missing {stage} graph for {record["design_id"]}')
            metadata = json.loads(metadata_path.read_text())
            snapshot = metadata.get('snapshot', metadata)
            actual_stage = metadata.get('prediction_stage', snapshot.get('prediction_stage'))
            actual_cutoff = metadata.get('feature_cutoff', snapshot.get('feature_cutoff'))
            # The tensor loader independently rechecks these values before training.
            if actual_stage not in (None, stage) or actual_cutoff not in (None, cutoff):
                raise ValueError(f'Incorrect metadata contract: {metadata_path}')
            record['graphs'][stage] = {
                'path': str(graph_path.resolve()),
                'sha256': sha(graph_path),
                'validation_path': str(validation_path.resolve()),
                'validation_sha256': validation_hash,
            }
            counts[stage] += 1

    parent['schema'] = 'r2g_downstream_training_four_stage_manifest_v1'
    parent['derived_from'] = {
        'path': str(args.parent.resolve()),
        'sha256': sha(args.parent),
    }
    parent['stage_extension'] = {
        'stages': STAGES,
        'records_per_stage': counts,
        'labels': 'shared post-route labels; never model inputs',
    }
    save(args.output, parent)
    receipt = {
        'schema': 'r2g_downstream_stage_manifest_receipt_v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS',
        'parent_manifest': str(args.parent.resolve()),
        'parent_manifest_sha256': sha(args.parent),
        'derived_manifest': str(args.output.resolve()),
        'derived_manifest_sha256': sha(args.output),
        'records': len(parent['records']),
        'stage_counts': counts,
    }
    save(args.receipt, receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
