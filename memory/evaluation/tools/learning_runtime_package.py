#!/usr/bin/env python3
"""Supplement a private R4 code copy with the missing *runtime* evaluation package.

Top-level memory/evaluation contains operator notes, but memory/tehm/evaluation
is imported by crystallization/evolution. Do not mutate any live R4 registration.
This package is a DEV candidate, not a release or a repair-dispatch authority.
"""
import argparse
import json
from pathlib import Path
import shutil
import time

from learning_pilot_qualification import digest, save, verify_hashes


def add_runtime_evaluation(source, code):
    source, code = Path(source), Path(code)
    package = source / 'memory/tehm/evaluation'
    target = code / 'memory/tehm/evaluation'
    if target.exists():
        raise FileExistsError('Never overwrite existing runtime modules')
    files = sorted(package.glob('*.py'))
    if not files or not (package / '__init__.py').is_file():
        raise ValueError('Missing runtime evaluation package')
    target.mkdir(parents=True)
    bindings = {}
    for p in files:
        if p.is_symlink():
            raise ValueError('Linked runtime module rejected')
        out = target / p.name
        shutil.copy2(p, out)
        bindings[str(p)] = digest(p)
        if digest(out) != bindings[str(p)]:
            raise ValueError('Runtime module copy drift')
    return bindings


def prepare(runtime, source, output):
    runtime, source, output = Path(runtime), Path(source), Path(output)
    if output.exists():
        raise FileExistsError('Existing package; no overwrite')
    started, cpu = time.time(), time.process_time()
    parent = json.loads((runtime / 'registration.json').read_text())
    verify_hashes(parent['files'])
    output.mkdir(parents=True)
    # Existing runtime files are byte-for-byte preserved; only missing modules
    # are added. No notebooks, operator docs, datasets or credentials are copied.
    shutil.copytree(runtime / 'code', output / 'code',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
    added = add_runtime_evaluation(source, output / 'code')
    files = {str(p): digest(p) for p in (output / 'code').rglob('*') if p.is_file()}
    for name, sha in parent['files'].items():
        p = Path(name)
        if p.is_relative_to(runtime / 'code'):
            if digest(output / 'code' / p.relative_to(runtime / 'code')) != sha:
                raise ValueError('Changed existing frozen runtime file')
    (output / 'operator').mkdir()
    shutil.copy2(__file__, output / 'operator' / Path(__file__).name)
    save(output / 'registration.json', {'schema': 'learning-runtime-supplement-v1',
         'status': 'DEV_CANDIDATE_NOT_RELEASED', 'time': time.time(),
         'parent_runtime': str(runtime), 'parent_registration_sha256': digest(runtime / 'registration.json'),
         'added_source_files': added, 'files': files,
         'operator_sha256': digest(__file__), 'new_model_requests': 0, 'new_eda_flows': 0,
         'release_allowed': False, 'growth_dispatch_allowed': False,
         'reason': 'Recursive evaluation directory exclusion removed a lazy runtime dependency',
         'wall_s': time.time() - started, 'cpu_s': time.process_time() - cpu})
    print(json.dumps({'package': str(output), 'added_runtime_modules': len(added),
                      'status': 'DEV_CANDIDATE_NOT_RELEASED'}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    prepare(a.runtime.resolve(), a.source.resolve(), a.output.resolve())
