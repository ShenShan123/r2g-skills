"""Isolated, bounded Calibre performance experiment; keep full raw evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('name')
p.add_argument('--gds', required=True)
p.add_argument('--seconds', type=int, default=120)
p.add_argument('--mode', choices=['hier', 'hyper', 'flat'], default='hyper')
p.add_argument('--select', nargs='+', default=[])
p.add_argument('--extra-svrf', default='')
a = p.parse_args()
root = Path('/proj/workarea/yangao/asap7_qualification_20261116')
out = root / 'calibre_runs' / a.name / 'drc'
out.mkdir(parents=True, exist_ok=False)
gds = Path(a.gds).resolve()
deck = root / 'private_rules/calibredecks_r1p7/calibre/ruledirs/drc/drcRules_calibre_asap7.rul'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
(out / 'test.gds').symlink_to(gds)
lines = ['DRC MAXIMUM RESULTS ALL', f'INCLUDE "{deck}"']
if a.select:
    lines.append('DRC SELECT CHECK ' + ' '.join(a.select))
if a.extra_svrf:
    lines.append(a.extra_svrf)
(out / 'run.svrf').write_text('\n'.join(lines) + '\n')
mode = [] if a.mode == 'flat' else ['-hier', '-turbo', '4']
if a.mode == 'hyper':
    mode.append('-hyper')
cmd = ['/usr/bin/time', '-v', 'timeout', '--kill-after=5', str(a.seconds),
       'calibre', '-drc', *mode, 'run.svrf']
env = dict(os.environ, MGLS_LICENSE_FILE='27020@sv01',
           pdk_path='/proj/workarea/LIB/asap7/asap7_pdk_r1p7')
env.update({name: '0' for name in ['VAR_M4_YOFFSET', 'VAR_M5_XOFFSET',
                                  'VAR_M6_YOFFSET', 'VAR_M7_XOFFSET']})
receipt = {'command': cmd, 'gds': str(gds), 'gds_sha256': sha(gds),
           'deck_sha256': sha(deck), 'runset_sha256': sha(out / 'run.svrf'),
           'selected_rules': a.select, 'full_rule_scope': not a.select,
           'maximum_results': 'ALL', 'timeout_s': a.seconds,
           'resource_scope': 'time -v may exclude independent HDB workers',
           'operation_index_not_comparable': True}
(out / 'probe_setup.json').write_text(json.dumps(receipt, indent=2) + '\n')
start = time.monotonic()
with (out / 'run.log').open('w') as log:
    proc = subprocess.run(cmd, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT)
receipt.update(process_exit=proc.returncode, wall_s=time.monotonic() - start)
log = (out / 'run.log').read_text()
completed = {m[0]: int(m[2] or m[1]) for m in re.findall(
    r'DRC RuleCheck (\S+) COMPLETED\. Number of Results = (\d+)(?:\s*\((\d+)\))?', log)}
receipt.update(completed_rulechecks=len(completed), completed_rules=completed,
               reported_expanded_results_so_far=sum(completed.values()),
               result='INCOMPLETE')
report = out / 'drc_report'
if proc.returncode == 0 and report.exists():
    txt = report.read_text()
    checks = re.search(r'TOTAL DRC RuleChecks Executed:\s*(\d+)', txt)
    count = re.search(r'TOTAL DRC Results Generated:\s*(\d+)(?:\s*\((\d+)\))?', txt)
    if checks and count:
        receipt.update(rulechecks=int(checks[1]), results=int(count[1]),
                       expanded_results=int(count[2] or count[1]),
                       result='COMPLETE' if not a.select else 'SELECTED_RULES_COMPLETE')
(out / 'invocation.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({k: v for k, v in receipt.items() if k != 'completed_rules'}))
