"""Freeze nested train-family subsets for the downstream scale ablation."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def family_order(families, seed):
    return sorted(families, key=lambda family: hashlib.sha256(
        f'{seed}\0{family}'.encode()).hexdigest())


def build_subset(parent, fraction, seed, parent_sha256):
    if not 0 < fraction <= 1:
        raise ValueError('Fraction must be in (0, 1]')
    train = [row for row in parent['records'] if row['split'] == 'train']
    fixed = [row for row in parent['records'] if row['split'] != 'train']
    families = family_order({row['family_id'] for row in train}, seed)
    count = max(1, math.ceil(len(families) * fraction))
    selected = set(families[:count])
    records = [row for row in train if row['family_id'] in selected] + fixed
    result = dict(parent)
    result['records'] = records
    result['ablation'] = {
        'kind': 'nested_train_family_fraction',
        'fraction': fraction,
        'selection_seed': seed,
        'selected_train_families': families[:count],
        'parent_manifest_sha256': parent_sha256,
        'validation_and_test_unchanged': True,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20260919)
    parser.add_argument('--percent', type=int, nargs='+', default=[25, 50])
    parser.add_argument('--receipt-name', default='subset_receipt.json')
    args = parser.parse_args()
    parent = json.loads(args.manifest.read_text())
    if not parent.get('formal_training_ready') or parent.get('pilot_subset'):
        raise ValueError('Expected the reviewed formal manifest')
    parent_sha = digest(args.manifest)
    percents = sorted(set(args.percent))
    if not percents or any(percent <= 0 or percent > 100 for percent in percents):
        raise ValueError('Percent values must be unique integers in [1, 100]')
    outputs = {}
    for percent in percents:
        path = args.output_dir / f'manifest_train{percent}.json'
        if path.exists():
            raise FileExistsError(path)
        manifest = build_subset(parent, percent / 100, args.seed, parent_sha)
        save(path, manifest)
        outputs[str(percent)] = str(path.resolve())

    manifests = {key: json.loads(Path(path).read_text()) for key, path in outputs.items()}
    parent_fixed = {(r['design_id'], r['split']) for r in parent['records'] if r['split'] != 'train'}
    for manifest in manifests.values():
        fixed = {(r['design_id'], r['split']) for r in manifest['records'] if r['split'] != 'train'}
        if fixed != parent_fixed:
            raise ValueError('Validation/test changed')
    family_sets = [set(manifests[str(percent)]['ablation']['selected_train_families'])
                   for percent in percents]
    for smaller, larger in zip(family_sets, family_sets[1:]):
        if not smaller < larger:
            raise ValueError('Expected strict nested family subsets')
    receipt = {
        'status': 'PASS',
        'parent_manifest': str(args.manifest.resolve()),
        'parent_manifest_sha256': parent_sha,
        'selection_rule': 'ascending sha256(seed NUL family_id); first ceil(fraction * families)',
        'selection_seed': args.seed,
        'outputs': {key: {'path': path, 'sha256': digest(path),
                          'train_designs': sum(r['split'] == 'train' for r in manifests[key]['records']),
                          'train_families': len(manifests[key]['ablation']['selected_train_families'])}
                    for key, path in outputs.items()},
        'validation_test_records': len(parent_fixed),
        'nested_percentages': percents,
    }
    if percents == [25, 50]:
        receipt['nested_25_in_50'] = True
    save(args.output_dir / args.receipt_name, receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
