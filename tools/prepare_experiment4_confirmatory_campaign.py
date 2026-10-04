#!/usr/bin/env python3
"""Create an immutable-ready Experiment 4 campaign without running converters."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


RUNTIME_TOOLS = (
    'audit_experiment4_prelaunch.py', 'experiment4_batch_smoke.py', 'experiment4_contract.py',
    'experiment4_metamorphic_canary.py', 'experiment4_protocol.py', 'experiment4_raw_oracle.tcl',
    'experiment4_semantic_audit.py', 'report_experiment4_semantic_audit.py',
    'run_experiment4_formal_pipeline.py', 'run_experiment4_graph_conversion.py',
    'run_experiment4_llm_converter.py', 'run_experiment4_transport_canary.py',
)
PROTOCOL_DOCS = {
    'experiment4_protocol_v3_zh.md': 'experiment4_protocol_v3_zh.md',
    'experiment4_execution_runbook_zh.md': 'experiment4_execution_runbook_zh.md',
    'experiment4_model_routes.json': 'experiment4_model_routes.json',
    'experiment4_resource_limits_v3.json': 'experiment4_resource_limits.json',
    'converter_response_protocol_v2.json': 'converter_response_protocol_v2.json',
    'converter_response.schema.json': 'converter_response.schema.json',
    'pure_llm_converter_system_prompt.md': 'pure_llm_converter_system_prompt.md',
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def copied_files(root: Path):
    for directory in (root / 'runtime', root / 'protocol'):
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                yield path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--contract-source', type=Path, required=True,
                        help='audited prior protocol directory containing public_contract_v2 JSON files')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo, cohort, source, output = map(Path.resolve, (args.repo, args.cohort, args.contract_source, args.output))
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f'refusing to modify non-empty campaign: {output}')
    output.mkdir(parents=True, exist_ok=True)
    runtime_tools = output / 'runtime/tools'
    runtime_tools.mkdir(parents=True)
    for name in RUNTIME_TOOLS:
        shutil.copy2(repo / 'tools' / name, runtime_tools / name)
    shutil.copytree(repo / 'r2g-skills/def-graph', output / 'runtime/r2g-skills/def-graph',
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc', '*.pyo'))
    protocol_source = repo / 'docs/experiments/experiment4'
    protocol = output / 'protocol'
    protocol.mkdir()
    for original, frozen in PROTOCOL_DOCS.items():
        shutil.copy2(protocol_source / original, protocol / frozen)
    for name in ('public_contract_v2.json', 'public_model_contract_v2.json'):
        shutil.copy2(source / name, protocol / name)
    shutil.copy2(cohort, output / 'cohort.json')

    hashes = []
    for path in sorted(copied_files(output)):
        hashes.append(f'{digest(path)}  {path.relative_to(output)}')
    checksum = output / 'runtime_and_protocol.SHA256SUMS'
    checksum.write_text('\n'.join(hashes) + '\n')
    commit = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True,
                            stdout=subprocess.PIPE, check=False).stdout.strip()
    manifest = {
        'schema_version': 'experiment4-confirmatory-preparation-1.0',
        'created_at': now(), 'status': 'prepared_not_started',
        'campaign_root': str(output), 'cohort_sha256': digest(output / 'cohort.json'),
        'repo_commit': commit, 'frozen_runtime_is_authoritative': True,
        'runtime_and_protocol_sha256': digest(checksum),
        'hidden_test_materialized': False,
        'next_actions': ['materialize canary and development only', 'run R2G canary',
                         'run fresh transport canaries', 'pass converter_development prelaunch audit'],
    }
    write_json(output / 'preparation_manifest.json', manifest)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
