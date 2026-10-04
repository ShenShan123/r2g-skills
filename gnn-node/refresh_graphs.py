"""Re-export a manifest into a new directory from existing raw physical inputs.

No physical implementation is launched; original artifacts and splits stay intact.
Each completed design has a checkpoint; inputs and runtime must match on resume.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

from stage_data import digest, load_graph, save_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from export_downstream_pilot import bounded_command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    runtime, out = Path(args.runtime), Path(args.output)
    scripts = runtime/'r2g-skills/def-graph/scripts/r2g2'
    manifest = json.loads(Path(args.manifest).read_text())
    configs, input_hashes = {}, {}
    for row in manifest['records']:
        source = Path(row['graphs']['cts']['path']).parents[3]/'method_config.json'
        cfg = json.loads(source.read_text())
        configs[row['design_id']] = cfg
        input_hashes[str(source)] = digest(source)
        for value in cfg.values():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and item.startswith('/') and Path(item).is_file():
                    input_hashes[item] = digest(item)
    identity = {'source_manifest_sha256': digest(args.manifest), 'raw_input_hashes': input_hashes,
                'runtime_hashes': {str(p): digest(p) for p in sorted(runtime.rglob('*.py'))},
                'script_sha256': digest(__file__)}
    identity_path = out/'identity.json'
    if out.exists():
        if not identity_path.is_file() or json.loads(identity_path.read_text()) != identity:
            raise ValueError('Refresh input/runtime changed; use a new output directory')
    else:
        save_json(identity_path, identity)
    records = []
    commands = ['01_build_base_graph.py', '02_extract_features.py', '03_extract_labels.py',
                '04_assemble_heterograph.py', '05_build_stage_snapshots.py', 'checks/validate_four_stage.py']
    for record in manifest['records']:
        key = record['design_id']
        case = out/'designs'/key
        checkpoint = case/'checkpoint.json'
        if checkpoint.is_file():
            row = json.loads(checkpoint.read_text())
            for stage in row['graphs']:
                load_graph(row, stage)
        else:
            cfg = dict(configs[key], output_dir=str(case/'generated'))
            config = case/'method_config.json'
            save_json(config, cfg)
            for script in commands:
                print(key+': '+script, flush=True)
                command = [sys.executable, str(scripts/script), '--config', str(config)]
                if script == '03_extract_labels.py':
                    command.append('--skip-irdrop')
                result = bounded_command(command, case/'logs'/(Path(script).name+'.log'), 900)
                if result['returncode'] != 0:
                    raise RuntimeError('Refresh failed, see '+str(case/'logs'))
            row = copy.deepcopy(record)
            validation = case/'generated/four_stage.validation.json'
            for stage, info in row['graphs'].items():
                path = case/'generated/stages'/stage/'heterograph.pt'
                info.update(path=str(path), sha256=digest(path), validation_path=str(validation),
                            validation_sha256=digest(validation))
                load_graph(row, stage)
            sidecar = case/'generated/labels/gate_con_IR.csv'
            row['congestion_labels'] = {'path': str(sidecar), 'sha256': digest(sidecar)}
            save_json(checkpoint, row)
        records.append(row)
        save_json(out/'status.json', {'completed': len(records), 'total': len(manifest['records']), 'last': key})
    manifest.update(records=records, refresh_identity_sha256=digest(identity_path))
    manifest.pop('congestion_audit', None)
    save_json(out/'manifest.json', manifest)
    print('refresh_complete', flush=True)


if __name__ == '__main__':
    main()
