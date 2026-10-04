#!/usr/bin/env python3
"""Rebuild only features/assembly in an isolated retrospective validation run."""
import argparse
import json
from pathlib import Path
import shutil
import sys

from experiment4_metamorphic_canary import execute
from experiment4_semantic_audit import digest, save


def tree_hashes(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main(args):
    source, output = args.campaign.resolve(), args.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('validation output must be independent from original campaign')
    relative = Path('r2g-skills/def-graph/scripts/r2g2/02_extract_features.py')
    runtime = source / 'runtime'
    patch = args.feature_script.resolve()
    cohort = json.loads((source / 'cohort.json').read_text())
    tasks = cohort['splits']['hidden_test']
    if args.task:
        tasks = [t for t in tasks if t['task_id'] in args.task]
        if {t['task_id'] for t in tasks} != set(args.task):
            raise ValueError('unknown task')
    plan = {'role': 'retrospective geometry bugfix validation; not a new formal result',
            'source': str(source), 'source_cohort_sha256': digest(source / 'cohort.json'),
            'frozen_runtime_sha256': tree_hashes(runtime), 'feature_patch': str(patch),
            'feature_patch_sha256': digest(patch), 'runner_sha256': digest(__file__),
            'tasks': [t['task_id'] for t in tasks],
            'reuse': 'copy frozen base_graph and labels; rerun features, graph assembly, snapshots'}
    plan_path = output / 'validation_plan.json'
    if output.exists():
        if not plan_path.exists() or json.loads(plan_path.read_text()) != plan:
            raise ValueError('existing validation directory has a different binding')
    else:
        output.mkdir(parents=True)
        save(plan_path, plan)
        shutil.copytree(runtime, output / 'runtime', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(patch, output / 'runtime' / relative)
        isolated_cohort = dict(cohort)
        isolated_cohort['splits'] = {'hidden_test': tasks}
        save(output / 'cohort.json', isolated_cohort)
    expected_runtime = dict(plan['frozen_runtime_sha256'])
    expected_runtime[str(relative)] = plan['feature_patch_sha256']
    if tree_hashes(output / 'runtime') != expected_runtime:
        raise ValueError('isolated runtime differs from declared single-file patch')
    rows = []
    for task in tasks:
        task_id = task['task_id']
        old = source / 'methods/r2g-frozen-v3' / task_id
        new = output / 'methods/r2g-geometry-fix' / task_id
        new.mkdir(parents=True, exist_ok=True)
        reused = {d: tree_hashes(old / 'generated' / d) for d in ('base_graph', 'labels')}
        binding = {'config_sha256': digest(old / 'method_config.json'), 'copied_evidence': reused}
        state_path = new / 'run_state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state.get('binding') != binding:
                raise ValueError('upstream evidence changed')
            if state.get('status') == 'completed' and state.get('outputs') == tree_hashes(new / 'generated'):
                rows.append(state)
                continue
        for directory in reused:
            shutil.copytree(old / 'generated' / directory, new / 'generated' / directory, dirs_exist_ok=True)
        cfg = json.loads((old / 'method_config.json').read_text())
        cfg['output_dir'] = str(new / 'generated')
        save(new / 'method_config.json', cfg)
        commands = []
        for step in ('02_extract_features.py', '04_assemble_heterograph.py', '05_build_stage_snapshots.py'):
            command = [sys.executable, str(output / 'runtime' / relative.parent / step),
                       '--config', str(new / 'method_config.json')]
            result = execute(command, new / (step + '.log'), args.timeout)
            commands.append(result)
            state = {'task': task_id, 'status': 'running', 'binding': binding, 'commands': commands}
            if result['status'] != 'COMPLETED':
                state['status'] = 'failed'
            save(state_path, state)
            if state['status'] == 'failed':
                break
        else:
            state.update(status='completed', outputs=tree_hashes(new / 'generated'))
            save(state_path, state)
        rows.append(state)
        save(output / 'progress.json', {'finished': len(rows), 'planned': len(tasks),
                                      'completed': sum(r['status'] == 'completed' for r in rows), 'last': state})
        print(json.dumps({'task': task_id, 'status': state['status']}), flush=True)
    save(output / 'rebuild_summary.json', {'rows': rows, 'planned': len(tasks)})
    if any(r['status'] != 'completed' for r in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--feature-script', type=Path, required=True)
    p.add_argument('--task', action='append')
    p.add_argument('--timeout', type=int, default=600)
    main(p.parse_args())
