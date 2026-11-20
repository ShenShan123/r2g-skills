"""Audit used reference models and zero-device omission for additional designs."""
import argparse
from collections import Counter
import hashlib
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lvs_diagnosis'))
from derive_oai21_reference import circuits
from verify_combinational_truth import eval_mos, eval_verilog, verilog_modules
from verify_sequential_topology import merged_mos, isomorphic

p = argparse.ArgumentParser()
p.add_argument('project')
p.add_argument('--evidence', help='Calibre run name; defaults to project name')
p.add_argument('--lvs-dir', type=Path, help='Explicit local LVS evidence directory')
args = p.parse_args()
inp = ROOT / (args.project + '_input')
top = json.loads((inp / 'provenance.json').read_text())['lvs_top_name'].lower()
old_path = ROOT / 'gcd_calibre_input/gcd_device_complete.cdl'
new_path = ROOT / 'lvs_diagnosis/asap7_R_official_pex_full.cdl'
old, new = circuits(old_path.read_text()), circuits(new_path.read_text())
before = circuits(new_path.read_text() + '\n' + (inp / 'design_with_physical.cdl').read_text())
after = circuits((inp / 'source.cdl').read_text())
physical = {'TAPCELL_ASAP7_75t_R', 'FILLER_ASAP7_75t_R', 'FILLERxp5_ASAP7_75t_R'}
before_instances = [line.split() for line in before[top]['lines'] if line.upper().startswith('X')]
after_instances = [line.split() for line in after[top]['lines'] if line.upper().startswith('X')]
assert [x for x in before_instances if x[-1] not in physical] == after_instances
assert before[top]['ports'] == after[top]['ports']
lvs_dir = args.lvs_dir or ROOT / 'calibre_evidence' / (args.evidence or args.project) / 'lvs'
extracted = circuits((lvs_dir / 'mask.lvs.rep.sp').read_text())
omitted = Counter(x[-1] for x in before_instances if x[-1] in physical)
for master in omitted:
    assert not before[master.lower()]['lines']
    assert not extracted[master.lower()]['lines']

def expanded(cells):
    records = []
    for inst in (line.split() for line in cells[top]['lines'] if line.upper().startswith('X')):
        name, master = inst[0], inst[-1]
        cell = cells[master.lower()]
        assert len(cell['ports']) == len(inst[1:-1])
        ports = {p.upper(): value for p, value in zip(cell['ports'], inst[1:-1])}
        for line in cell['lines']:
            fields = line.split()
            assert fields[0].upper().startswith('M')
            nodes = [ports.get(n.upper(), name + '/' + n) for n in fields[1:5]]
            records.append((name, master, fields[0], *nodes, *fields[5:]))
    return sorted(records)

a, b = expanded(before), expanded(after)
assert a == b
usage = Counter(x[-1] for x in after_instances)
modules = verilog_modules()
comb, seq, decap = [], [], []
for name, instances in sorted(usage.items()):
    key = name.lower()
    if name.startswith('DECAP'):
        decap.append({'cell': name, 'instances': instances})
    elif name.startswith(('DFF', 'SDF', 'LATCH')):
        ports = {p.upper() for p in old[key]['ports']}
        assert ports == {p.upper() for p in new[key]['ports']}
        mapping = isomorphic(merged_mos(old[key]), merged_mos(new[key]), ports)
        assert mapping is not None, name
        seq.append({'cell': name, 'instances': instances, 'exact_transistor_graph_match': True})
    else:
        module = modules[key]
        vectors = 0
        for values in product((0, 1), repeat=len(module['inputs'])):
            inputs = dict(zip(module['inputs'], values))
            # Vendor tie-cell primitives use sized binary constants as inputs.
            expected = eval_verilog(module, {"1'b0": 0, "1'b1": 1, **inputs})
            assert eval_mos(old[key], inputs, module['outputs']) == expected, (name, inputs, 'original CDL')
            assert eval_mos(new[key], inputs, module['outputs']) == expected, (name, inputs, 'official PEX')
            vectors += 1
        comb.append({'cell': name, 'instances': instances, 'exhaustive_vectors': vectors})

digest = lambda data: hashlib.sha256(data).hexdigest()
result = {
    'status': 'PASS', 'project': args.project, 'top': top,
    'source_sha256': digest((inp / 'source.cdl').read_bytes()),
    'original_cdl_sha256': digest(old_path.read_bytes()),
    'official_pex_reference_sha256': digest(new_path.read_bytes()),
    'zero_device_instances_omitted': dict(omitted),
    'empty_reference_cells_also_empty_in_actual_extraction': True,
    'all_other_instance_connections_and_top_ports_unchanged': True,
    'mos_before': len(a), 'mos_after': len(b),
    'all_mos_D_G_S_B_model_L_W_nfin_unchanged': True,
    'expanded_mos_sha256': digest(json.dumps(a, separators=(',', ':')).encode()),
    'combinational': {'cell_types': len(comb), 'instances': sum(x['instances'] for x in comb),
                      'vectors': sum(x['exhaustive_vectors'] for x in comb), 'cells': comb,
                      'method': 'Exhaustive static CMOS function: original CDL, official PEX and vendor Verilog'},
    'sequential': {'cell_types': len(seq), 'instances': sum(x['instances'] for x in seq), 'cells': seq,
                   'method': 'Exact transistor graph isomorphism, ports/gate/bulk/model/L/W/nfin fixed; S/D swappable'},
    'decaps': decap,
    'scope': 'Reference integrity and cell function; separate top-level functional validation is required.'
}
out = ROOT / args.project / 'reports/reference_validation.json'
out.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'project': args.project, 'status': 'PASS', 'mos': len(a),
                  'combinational_types': len(comb), 'sequential_types': len(seq)}))
