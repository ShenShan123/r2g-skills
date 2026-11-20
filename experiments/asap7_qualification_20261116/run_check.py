"""Execute unchanged official ASAP7 rules on a prepared input on memlab216."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('check', choices=('drc', 'lvs'))
p.add_argument('name')
p.add_argument('--gds', required=True)
p.add_argument('--top', default='gcd')
p.add_argument('--cdl')
p.add_argument('--flat', action='store_true')
p.add_argument('--property-limit', type=int)
p.add_argument('--offsets', nargs=4, default=['0'] * 4, metavar=('M4Y', 'M5X', 'M6Y', 'M7X'))
args = p.parse_args()
root = Path(__file__).resolve().parent
out = root / 'calibre_runs' / args.name / args.check
out.mkdir(parents=True, exist_ok=False)
gds = Path(args.gds).resolve()
deck = root / 'private_rules/calibredecks_r1p7/calibre/ruledirs' / args.check / f'{args.check}Rules_calibre_asap7.rul'
if args.check == 'drc':
    # The encrypted deck hardcodes TOPCELL and ./test.gds.
    (out / 'test.gds').symlink_to(gds)
    lines = ['DRC MAXIMUM RESULTS ALL', f'INCLUDE "{deck}"']
else:
    assert args.cdl
    lines = [f'LAYOUT PATH "{gds}"', f'LAYOUT PRIMARY "{args.top}"', 'LAYOUT SYSTEM GDSII',
             f'SOURCE PATH "{Path(args.cdl).resolve()}"', f'SOURCE PRIMARY "{args.top}"',
             'SOURCE SYSTEM SPICE', f'INCLUDE "{deck}"',
             'LVS POWER NAME "VDD" "VDD!" "vdd!"', 'LVS GROUND NAME "VSS" "VSS!" "vss!"']
    if args.property_limit:
        lines.append(f'LVS PROPERTY RESOLUTION MAXIMUM {args.property_limit}')
(out / 'run.svrf').write_text('\n'.join(lines) + '\n')
env = dict(os.environ, MGLS_LICENSE_FILE='27020@sv01', pdk_path='/proj/workarea/LIB/asap7/asap7_pdk_r1p7')
env.update(dict(zip(['VAR_M4_YOFFSET', 'VAR_M5_XOFFSET', 'VAR_M6_YOFFSET', 'VAR_M7_XOFFSET'], args.offsets)))
start = time.monotonic()
with (out / 'run.log').open('w') as log:
    mode = [] if args.flat else ['-hier', '-turbo', '4']
    done = subprocess.run(['/usr/bin/time', '-v', 'timeout', '600', 'calibre', '-' + args.check,
                           *mode, 'run.svrf'], cwd=out, env=env,
                          stdout=log, stderr=subprocess.STDOUT)
receipt = {'process_exit': done.returncode, 'wall_s': time.monotonic() - start,
           'gds_sha256': hashlib.sha256(gds.read_bytes()).hexdigest(),
           'deck_sha256': hashlib.sha256(deck.read_bytes()).hexdigest(),
           'offsets': args.offsets, 'flat': args.flat, 'property_limit': args.property_limit,
           'result': 'execution_failed'}
if args.cdl:
    receipt['source_sha256'] = hashlib.sha256(Path(args.cdl).read_bytes()).hexdigest()
if args.check == 'drc' and (out / 'drc_report').exists():
    report = (out / 'drc_report').read_text()
    checks = re.search(r'TOTAL DRC RuleChecks Executed:\s*(\d+)', report)
    count = re.search(r'TOTAL DRC Results Generated:\s*(\d+)(?:\s*\((\d+)\))?', report)
    if done.returncode == 0 and checks and count:
        expanded = int(count[2] or count[1])
        receipt.update(rulechecks=int(checks[1]), results=int(count[1]), expanded_results=expanded,
                       result='clean' if expanded == 0 else 'violations')
        receipt['violations'] = {m[0]: [int(m[1]), int(m[2] or m[1])] for m in re.findall(
            r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count =\s*(\d+)(?:\s*\((\d+)\))?', report, re.M) if int(m[1])}
if args.check == 'lvs' and (out / 'mask.lvs.rep').exists():
    log = (out / 'run.log').read_text()
    verdict = re.search(r'LVS completed\. (CORRECT|INCORRECT)\.', log)
    if done.returncode == 0 and verdict:
        receipt['result'] = verdict[1]
(out / 'invocation.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
