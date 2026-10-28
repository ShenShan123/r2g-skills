"""Classify fresh area A/B executions; infrastructure failures cannot count as wins."""
import hashlib,json,re
from pathlib import Path
from strict_signoff import inspect_run

def classify(run, flow_returncode):
    run=Path(run);result=json.loads((run/'result.json').read_text())
    text=(run/'flow.log').read_text(errors='replace')
    terminal=text[-24000:]
    files={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('result.json','flow.log')}
    out={'flow_returncode':flow_returncode,'files':files,'is_success':False,'classification':'inconclusive'}
    if flow_returncode==0 and result.get('status')=='pass':
        signed=inspect_run(run)
        if signed['drc'] not in ('clean','violations') or signed['lvs'] not in ('clean','pin_mismatch','mismatch'):
            return {**out,'reason':'missing_or_incomplete_signoff'}
        for n in ('6_final.gds','6_drc.lyrdb','netgen_lvs.rpt'):
            p=run/n
            if not p.is_file() or not p.stat().st_size:return {**out,'reason':'missing_signoff_artifact'}
            files[n]=hashlib.sha256(p.read_bytes()).hexdigest()
        return {**out,'classification':'signed_result','is_success':signed['dual_clean'],'strict_signoff':signed}
    if type(flow_returncode) is int and 0 < flow_returncode < 124 and result.get('status')=='fail' and str(result.get('stage','')).startswith('5_'):
        # Only the witnessed terminal congestion failures are eligible controls.
        # Timeout/signal/OOM/other-stage errors are not silently treated as a loss.
        codes=re.findall(r'\[ERROR ([A-Z]+-\d+)\]',terminal)
        if (codes and codes[-1] in ('GRT-0116','GRT-0118','GRT-0232')
                and not re.search(r'Segmentation fault|SIGSEGV|SIGABRT|out of memory|Killed|core dumped',terminal,re.I)
                and flow_returncode not in (124,137,139,-9,-11,-6)):
            return {**out,'classification':'route_congestion_failure','failure_code':codes[-1]}
    return {**out,'reason':'execution_does_not_prove_supported_route_failure_or_signed_result'}
