#!/usr/bin/env python3
"""Read-only failure inventory of the frozen SKY130 dev baseline."""
import argparse
import collections
import datetime
import hashlib
import json
import re
from pathlib import Path


def classify(result, log):
    if result.get('status') == 'pass':
        return 'drc_violation' if result.get('drc_status') == 'violations' else 'lvs_pin_mismatch'
    if result.get('stage','').startswith('6_'):
        return 'postroute_report_failure'
    if 'Can not open file' in log and '$readmem' in log:
        return 'frontend_readmem_path'
    if 'ALDFF' in log or '$aldff' in log:
        return 'frontend_async_load_flop'
    if 'altera_attribute' in log or (result.get('design','').startswith('C02098__') and 'STA-0171' in log):
        return 'frontend_vendor_attribute'
    if re.search(r'\.(?:vh|svh):\d+: ERROR:', log):
        return 'frontend_header_as_source'
    if any(x in log for x in ('OP_CAST', 'unexpected TOK_IMPORT', 'Failed to detect width for parameter')):
        return 'frontend_systemverilog'
    if result.get('stage','').startswith('1_1') and "unexpected '['" in log:
        return 'frontend_systemverilog'
    if result.get('failure_class') == 'parse_error':
        return 'frontend_parse_review'
    if result.get('failure_class') == 'timeout':
        return 'timeout_' + str(result.get('stage', 'unknown')).split('_', 1)[0]
    if result.get('failure_class') == 'oom':
        return 'memory_exhaustion'
    if 'GRT-0116' in log or 'GRT-0232' in log:
        return 'routing_congestion'
    if 'DPL-0036' in log or 'DPL-0038' in log or 'PPL-0024' in log:
        return 'placement_capacity'
    if result.get('stage', '').startswith('1_'):
        return 'frontend_other'
    return 'backend_other'


def inventory(root):
    rows, failures, hashes = [], [], {}
    for file in sorted((root / 'runs').glob('*/result.json')):
        raw = file.read_bytes(); r = json.loads(raw); rows.append(r)
        hashes[str(file.relative_to(root))] = hashlib.sha256(raw).hexdigest()
        if r.get('drc_status') == 'clean' and r.get('lvs_status') == 'clean':
            continue
        log = (file.parent / 'flow.log').read_text(errors='replace') if (file.parent / 'flow.log').exists() else r.get('log_tail', '')
        errors = [l[:500] for l in log.splitlines() if 'ERROR' in l or 'Error:' in l or 'out of memory' in l]
        failures.append(dict(design=r['design'], run=str(file.parent), category=classify(r, log),
            status=r.get('status'), stage=r.get('stage'), failure_class=r.get('failure_class'),
            drc=r.get('drc_status'), lvs=r.get('lvs_status'), cells=r.get('cells'), seconds=r.get('seconds'),
            errors=errors[-4:], drc_violations=r.get('drc_violations_signoff')))
    return dict(at=datetime.datetime.now().astimezone().isoformat(), baseline=str(root),
        total=len(rows), flow_pass=sum(r.get('status')=='pass' for r in rows),
        dual_clean=sum(r.get('drc_status')=='clean' and r.get('lvs_status')=='clean' for r in rows),
        category_counts=dict(collections.Counter(r['category'] for r in failures)),
        failures=failures, baseline_result_sha256=hashes,
        criterion='strict DRC clean AND LVS clean; missing/warnings/skipped do not count',
        baseline_policy='Do not alter baseline files. Attribute frontend, physical repair, and checker corrections separately.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('baseline', type=Path)
    args=parser.parse_args(); print(json.dumps(inventory(args.baseline), ensure_ascii=False, indent=2))
