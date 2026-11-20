"""Export full physical netlist against independently published ASAP7 PEX models."""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

p = argparse.ArgumentParser()
p.add_argument('project')
args = p.parse_args()
root = Path(__file__).resolve().parent
project = root / args.project
run = sorted((project / 'backend').glob('RUN_*'))[-1]
out = root / (args.project + '_input')
out.mkdir(exist_ok=True)
library = root / 'lvs_diagnosis/asap7_R_official_pex_full.cdl'
script = out / 'write_cdl.tcl'
raw = out / 'design_with_physical.cdl'
script.write_text(f'read_db {{{run}/results/6_final.odb}}\n'
                  f'write_cdl -include_fillers -masters {{{library}}} {{{raw}}}\n')
with (out / 'write_cdl.log').open('w') as log:
    subprocess.run(['/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad',
                    '-no_init', '-exit', str(script)], stdout=log, stderr=subprocess.STDOUT, check=True)
physical = {'TAPCELL_ASAP7_75t_R', 'FILLER_ASAP7_75t_R', 'FILLERxp5_ASAP7_75t_R'}
removed = Counter()
kept = []
for line in raw.read_text().splitlines(keepends=True):
    fields = line.split()
    if fields and fields[0].upper().startswith('X') and fields[-1] in physical:
        removed[fields[-1]] += 1
    else:
        kept.append(line)
final = out / 'source.cdl'
final.write_text(library.read_text() + '\n' + ''.join(kept))
(out / 'source_provenance.json').write_text(json.dumps({
    'library': str(library), 'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
    'zero_device_physical_instances_omitted': dict(removed), 'logic_blackboxes': 0,
    'reference_from_tested_layout': False,
}, indent=2) + '\n')
print(final)
