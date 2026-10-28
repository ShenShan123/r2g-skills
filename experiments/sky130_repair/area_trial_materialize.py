"""Expose fresh physical observations to ordinary ingest without inventing stages."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from area_trial_evidence import classify

REPO = Path(__file__).resolve().parents[2]


def ingest_hashes(project, backend):
    paths = list((project/'reports').glob('*')) + [backend/'stage_log.jsonl']
    return {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def materialize(project, execution):
    project = Path(project)
    run = Path(execution['run'])
    accepted = classify(run, execution['flow_returncode'])
    if accepted != execution['acceptance'] or accepted['classification'] == 'inconclusive':
        raise ValueError('unverified physical evidence')
    backend = project / 'backend/RUN_Z_FRESH_AB'
    receipt = backend / 'acceptance.json'
    if backend.exists():
        if not receipt.exists() or json.loads(receipt.read_text()) != accepted:
            raise ValueError('partial or changed materialization; inspect before retry')
        # Recheck all original physical acceptance artifacts against the copy.
        copied = classify(backend/'results', execution['flow_returncode'])
        # The report location changes when copying; all verdicts and hashes must match.
        if 'strict_signoff' in copied:
            copied['strict_signoff']['lvs_final_source'] = accepted['strict_signoff']['lvs_final_source']
        if copied != accepted:
            raise ValueError('materialized evidence changed')
        recorded = json.loads((backend/'ingest_hashes.json').read_text())
        if ingest_hashes(project, backend) != recorded:
            raise ValueError('ingest reports or stage ledger changed')
        return
    results = backend / 'results'
    results.mkdir(parents=True)
    for p in run.iterdir():
        if p.is_file():
            shutil.copy2(p, results/p.name)
    logs = list((run/'logs').glob('sky130hd/*/base'))
    if len(logs) != 1:
        raise ValueError('ambiguous fresh ORFS logs')
    shutil.copytree(logs[0], backend/'logs')
    shutil.copy2(run/'flow.log', backend/'flow.log')
    (project/'reports').rename(project/'reports_before_ab')
    reports = project/'reports'
    reports.mkdir()
    subprocess.run([sys.executable, str(REPO/'r2g-skills/signoff-loop/scripts/extract/extract_ppa.py'),
                    str(project), str(reports/'ppa.json')], check=True)
    ppa = json.loads((reports/'ppa.json').read_text())
    if ppa['run_dir'] != str(backend):
        raise ValueError('PPA reader selected stale evidence')
    if accepted['classification'] == 'route_congestion_failure':
        event = dict(stage='route', status='fail',
                     provenance='reconstructed_from_fresh_terminal_result_and_log',
                     source_result_sha256=accepted['files']['result.json'],
                     source_log_sha256=accepted['files']['flow.log'],
                     failure_code=accepted['failure_code'])
        (backend/'stage_log.jsonl').write_text(json.dumps(event)+'\n')
        (reports/'route.json').write_text(json.dumps(dict(status='fail', **{'failure_code': accepted['failure_code']}))+'\n')
    else:
        # No native per-stage ledger exists for this driver. An empty current
        # ledger prevents ingest from falling back to the frozen failed run;
        # success still requires the fresh strict DRC/LVS reports below.
        (backend/'stage_log.jsonl').write_text('')
        signed = accepted['strict_signoff']
        (reports/'drc.json').write_text(json.dumps(dict(
            status='clean' if signed['drc']=='clean' else 'fail',
            total_violations=signed['drc_items'], report_file=str(results/'6_drc.lyrdb')))+'\n')
        (reports/'lvs.json').write_text(json.dumps(dict(
            status='clean' if signed['lvs']=='clean' else 'fail', raw_final_status=signed['lvs'],
            mismatch_class='top_pin_mismatch' if signed['lvs']=='pin_mismatch' else None,
            tool='netgen', report_file=str(results/'netgen_lvs.rpt')))+'\n')
    (backend/'ingest_hashes.json').write_text(json.dumps(ingest_hashes(project, backend), indent=2)+'\n')
    receipt.write_text(json.dumps(accepted, indent=2)+'\n')
