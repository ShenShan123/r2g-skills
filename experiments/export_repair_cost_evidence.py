#!/usr/bin/env python3
"""Export bounded signoff evidence and live artifact/input hashes, never RTL/GDS.

Run on the execution host after a measured attempt closes. No EDA or model calls.
The receipt supplements, rather than changes, the original API task verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def export(source):
    count = 0
    for folder in sorted((source / 'cases').glob('*')):
        summary = read(folder / 'summary.json', {})
        if summary.get('status') != 'complete':
            continue
        receipt = read(folder / 'input_receipt.json', {})
        inp = Path(receipt.get('input', '/nonexistent'))
        input_hashes = {rel: digest(inp / rel) if (inp / rel).is_file() else None
                        for rel in receipt.get('sha256', {})}
        projects = []
        for design in summary.get('designs', []):
            project = Path(design['project'])
            if not project.resolve().is_relative_to((source / 'runtime/workspace').resolve()):
                raise ValueError(f'Project outside selected runtime: {project}')
            reports, report_hashes = {}, {}
            for name in ('drc.json', 'lvs.json', 'signoff_manifest.json',
                         'signoff_gate.json', 'verify.json'):
                p = project / 'reports' / name
                if p.is_file():
                    if p.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError(f'Oversized evidence report: {p}')
                    reports[name] = read(p)
                    report_hashes[name] = digest(p)
            metadata = read(project / 'metadata.json', {})
            drc = reports.get('drc.json', {})
            run = drc.get('run_tag')
            # Hash the archived confirming run, not an ORFS live directory that
            # another launch could later overwrite.
            artifact_hashes = {}
            if run and Path(run).name == run:
                for ext in ('gds', 'def'):
                    p = project / 'backend' / run / 'results' / f'6_final.{ext}'
                    if not p.is_file():
                        p = project / 'backend' / run / 'final' / f'6_final.{ext}'
                    artifact_hashes[ext] = digest(p) if p.is_file() else None
            projects.append({'job_id': design['job_id'], 'name': design['name'],
                             'project': str(project), 'reports': reports,
                             'report_sha256': report_hashes, 'artifact_sha256': artifact_hashes,
                             'frontend_fixes': metadata.get('frontend_fixes', []),
                             'flow_fixes': metadata.get('flow_fixes', [])})
        evidence = {'schema_version': 1, 'session_id': summary['session_id'],
                    'source_input_sha256': input_hashes, 'projects': projects}
        target = folder / 'signoff_evidence.json'
        text = json.dumps(evidence, ensure_ascii=False, indent=2) + '\n'
        # Stable receipt content makes repeated export auditable/idempotent.
        if not target.is_file() or target.read_text() != text:
            temp = target.with_suffix('.tmp')
            temp.write_text(text)
            temp.replace(target)
        count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'evidence_cases': export(args.source.resolve())}))
