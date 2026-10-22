#!/usr/bin/env python3
"""Q1: complete missing provenance/checklist evidence; NEVER rerun EDA or repairs.

Preserve original receipts. Only use completed qualification projects with unchanged
registered config/SDC. Costs append to each original project's ledger. No implicit retry.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time

import t2a_qualify as Q


def complete(root, design):
    output = root / (design + '-evidence-completion.json')
    if output.exists():
        raise RuntimeError('Evidence-completion receipt already exists; no implicit retry')
    registration = json.loads((root / 'registration.json').read_text())
    if not (root / (design + '-summary.json')).exists():
        raise RuntimeError('Qualification still running or incomplete')
    H = Q.module('t2a_evidence_harness', root / 'code/memory/scripts/r2g_memory_eval.py')
    H.SKILL = root / 'code/r2g-skills/signoff-loop'
    H.FLOW = H.SKILL / 'scripts/flow'
    os.environ['R2G_BUDGET_FILE'] = str(root / 'budget.json')
    gate = Q.REPO / 'r2g-skills/def-graph/scripts/flow/signoff_gate.py'
    checker = H.SKILL / 'scripts/reports/acceptance_check.py'
    env = H.base_env('none')
    env.update(R2G_BUDGET_COMPONENT='qualification_evidence_completion', R2G_BUDGET_PURPOSE='final_acceptance',
               R2G_ENV_FILE=str(H.SKILL / 'references/env.local.sh'), PYTHONDONTWRITEBYTECODE='1',
               R2G_JOURNAL_DB=str(root / (design + '-journal.sqlite')))
    records = []
    for task in [t for t in registration['tasks'] if t['design'] == design]:
        receipt = root / (design + '__' + task['variant'] + '.json')
        if not receipt.exists():
            continue
        raw = json.loads(receipt.read_text())
        project = Path(task['project'])
        if not raw.get('final_layout'):
            records.append({'task': task['variant'], 'status': 'NO_FINAL_LAYOUT', 'original_receipt': str(receipt)})
            continue
        for name, key in [('config.mk', 'config_sha256'), ('constraint.sdc', 'sdc_sha256')]:
            if Q.digest(project / 'constraints' / name) != task[key]:
                raise RuntimeError('Original task input changed: ' + str(project / name))
        clock = re.search(r'^\s*set\s+clk_period\s+([0-9.]+)', (project / 'constraints/constraint.sdc').read_text(), re.M)
        if not clock:
            raise RuntimeError('Clock reference cannot be determined')
        runs = sorted((project / 'backend').glob('RUN_*'))
        run_dir = runs[-1]
        final_def = run_dir / 'final/6_final.def'
        if not final_def.is_file():
            final_def = run_dir / 'results/6_final.def'
        if not final_def.is_file():
            raise RuntimeError('Final DEF missing')
        H._EPISODE['ledger'] = project
        log = project / 'qualification_evidence_completion.log'
        evidence = project / 'reports/qualification_acceptance_q1.json'
        started = time.time()
        rc_gate = H.run(['python3', gate, project, '--run-dir', run_dir, '--def', final_def, '--mode', 'strict'], env, log, 300)
        rc_accept = H.run(['python3', checker, project, '--expected-clock-period', clock[1], '--out', evidence], env, log, 300)
        records.append({'task': task['variant'], 'original_receipt': str(receipt), 'original_receipt_sha256': Q.digest(receipt),
                        'started': started, 'ended': time.time(), 'gate_rc': rc_gate, 'acceptance_rc': rc_accept,
                        'acceptance': H.jload(evidence), 'run_dir': str(run_dir), 'def_sha256': Q.digest(final_def),
                        'eda_flows_launched': 0, 'llm_requests': 0})
    Q.save(output, {'amendment': 'Q1', 'records': records, 'formal_test_manifest_frozen': False,
                   'checker_files': {str(p.relative_to(Q.REPO)): Q.digest(p)
                                     for p in (Q.REPO / 'r2g-skills/def-graph').rglob('*.py')},
                   'acceptance_checker_sha256': Q.digest(checker)})
    print(json.dumps({'receipt': str(output), 'checked': len(records), 'eda_flows_launched': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--design', choices=Q.DESIGNS, required=True)
    args = parser.parse_args()
    complete(args.root.resolve(), args.design)
