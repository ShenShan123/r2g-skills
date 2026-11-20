"""Preserve logic/decap devices, omit only zero-device physical cell instances."""
from collections import Counter
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
source = root / 'gcd_smoke/lvs'
text = (source / 'asap7_R_all_physical.cdl').read_text()
design = (source / '6_final_with_fillers.cdl').read_text()
physical = {'TAPCELL_ASAP7_75t_R', 'FILLER_ASAP7_75t_R', 'FILLERxp5_ASAP7_75t_R'}
removed = Counter()
kept = []
decaps = 0
for line in design.splitlines(keepends=True):
    tokens = line.split()
    if tokens and tokens[0].upper().startswith('X'):
        master = tokens[-1]
        if master in physical:
            removed[master] += 1
            continue
        decaps += master.startswith('DECAP')
    kept.append(line)
output = root / 'gcd_calibre_input/gcd_device_complete.cdl'
output.write_text(text + ''.join(kept))
provenance = {
    'physical_zero_transistor_instances_omitted': dict(removed),
    'active_logic_instances_omitted': 0,
    'decap_instances_retained': decaps,
    'reason': 'Physical welltap/filler cells have zero MOS in extracted GDS. Active logic, decaps and bulk connectivity remain checked.',
    'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
}
(output.parent / 'cdl_adapter_provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
