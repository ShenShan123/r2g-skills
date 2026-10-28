#!/usr/bin/env python3
"""Audit completed selftest jobs; run inside the acceptance environment."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def read(path):
    return json.loads(path.read_text())


def audit(home, job_ids):
    rows = []
    with sqlite3.connect(f"file:{home / 'jobs.sqlite'}?mode=ro", uri=True) as db:
        for jid in job_ids:
            job = db.execute("SELECT status,spec,result FROM jobs WHERE id=?", (jid,)).fetchone()
            if not job:
                raise ValueError(f"Missing job: {jid}")
            status, spec, result = job[0], json.loads(job[1]), json.loads(job[2] or '{}')
            designs = db.execute("SELECT name,project,status FROM designs WHERE job_id=?", (jid,)).fetchall()
            if status != 'done' or not designs or result.get('passed') != len(designs):
                raise ValueError(f"Job not fully successful: {jid} {status} {result}")
            for name, project, state in designs:
                p = Path(project)
                gate = read(p / 'reports/signoff_gate.json')
                verify = read(p / 'reports/verify.json')
                drc = read(p / 'reports/drc.json')
                lvs = read(p / 'reports/lvs.json')
                checks = verify.get('results', [])
                if (state != 'passed' or gate.get('status') != 'pass'
                        or drc.get('status') != 'clean' or lvs.get('status') != 'clean'
                        or not checks or any(c.get('ok') is not True for c in checks)):
                    raise ValueError(f"Report failed: {jid}/{name}")
                for binding in ('binding', 'report_binding', 'artifact_digest'):
                    if gate['checks'][binding]['status'] != 'bound':
                        raise ValueError(f"Unbound {binding}: {jid}/{name}")
                package = Path(result['package'])
                if not package.is_file() or not package.stat().st_size:
                    raise ValueError(f"Missing dataset package: {package}")
                rows.append(dict(job=jid, design=name, platform=spec.get('platform'),
                                 drc='clean', lvs='clean', dataset_checks=len(checks),
                                 package_bytes=package.stat().st_size,
                                 package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
                                 gds_sha256=drc.get('gds_sha256')))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_ids', nargs='+')
    parser.add_argument('--home', type=Path, default=Path.home() / '.r2g')
    args = parser.parse_args()
    print(json.dumps(audit(args.home, args.job_ids), indent=2))
