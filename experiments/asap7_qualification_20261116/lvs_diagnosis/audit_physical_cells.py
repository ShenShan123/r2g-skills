"""Prove physical zero-device omission preserves the complete MOS reference."""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import re
from derive_oai21_reference import HERE, ROOT, circuits

PHYSICAL = {'TAPCELL_ASAP7_75t_R', 'FILLER_ASAP7_75t_R', 'FILLERxp5_ASAP7_75t_R'}


def instances(cell):
    return [line.split() for line in cell['lines'] if line[0].upper() == 'X']


def expand(cells):
    records = []
    for inst in instances(cells['gcd']):
        name, master = inst[0], inst[-1]
        cell = cells[master.lower()]
        assert len(cell['ports']) == len(inst[1:-1])
        ports = {p.upper(): v for p, v in zip(cell['ports'], inst[1:-1])}
        for line in cell['lines']:
            fields = line.split()
            assert fields[0][0].upper() == 'M', line
            nodes = [ports.get(n.upper(), name + '/' + n) for n in fields[1:5]]
            records.append((name, master, fields[0], *nodes, *fields[5:]))
    return sorted(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project', default='gcd_asym_via', nargs='?')
    args = parser.parse_args()
    inputs = ROOT / (args.project + '_input')
    library = (HERE / 'asap7_R_official_pex_full.cdl').read_text()
    before = circuits(library + '\n' + (inputs / 'design_with_physical.cdl').read_text())
    after = circuits((inputs / 'source.cdl').read_text())
    b_instances, a_instances = instances(before['gcd']), instances(after['gcd'])
    active_before = [i for i in b_instances if i[-1] not in PHYSICAL]
    assert active_before == a_instances
    assert before['gcd']['ports'] == after['gcd']['ports']
    removed = Counter(i[-1] for i in b_instances if i[-1] in PHYSICAL)
    extracted_path = ROOT / 'calibre_evidence' / args.project / 'lvs/mask.lvs.rep.sp'
    extracted = circuits(extracted_path.read_text())
    for master in PHYSICAL:
        assert not before[master.lower()]['lines']
        assert not after[master.lower()]['lines']
        assert not extracted[master.lower()]['lines']
    b_mos, a_mos = expand(before), expand(after)
    assert b_mos == a_mos
    digest = lambda records: hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()
    source_decap = Counter(i[-1] for i in b_instances if i[-1].startswith('DECAP'))
    validated = circuits((ROOT / 'gcd_calibre_input/gcd_device_complete.cdl').read_text())
    validated_logic = Counter(i[-1] for i in instances(validated['gcd']) if not i[-1].startswith('DECAP'))
    current_logic = Counter(i[-1] for i in a_instances if not i[-1].startswith('DECAP'))
    project_truth = HERE / ('combinational_truth_' + args.project + '.json')
    verified_types = set(validated_logic)
    if project_truth.exists():
        additional_truth = json.loads(project_truth.read_text())
        assert additional_truth['status'] == 'PASS'
        verified_types.update(c['cell'] for c in additional_truth['cells'])
    assert set(current_logic).issubset(verified_types)
    effective_report = (extracted_path.parent / 'mask.lvs.rep').read_text()
    assert re.search(r'^\s*LVS IGNORE PORTS\s+NO$', effective_report, re.M)
    assert re.search(r'^\s*LVS SOFT SUBSTRATE PINS\s+NO$', effective_report, re.M)
    assert not re.search(r'^\s*LVS IGNORE DEVICE PIN\b', effective_report, re.M)
    for dev in ('mn\\(nmos_rvt\\)', 'mp\\(pmos_rvt\\)'):
        for prop in ('l', 'w', 'nfin'):
            assert re.search(r'TRACE PROPERTY\s+' + dev + r'\s+' + prop + r'\s+' + prop + r'\s+0 ABSOLUTE', effective_report)
    report = {
        'status': 'PASS', 'project': args.project,
        'method': 'Expand every retained reference cell to MOS; compare exact instance names, D/G/S/B net bindings, models and parameters before versus after omission.',
        'physical_cells_omitted': dict(removed),
        'omitted_cells_have_zero_reference_devices': True,
        'omitted_cells_have_zero_extracted_devices_in_this_design': True,
        'checked_extraction': str(extracted_path),
        'all_other_instances_in_original_order_unchanged': True,
        'top_ports_unchanged': True,
        'mos_before_omission': len(b_mos), 'mos_after_omission': len(a_mos),
        'flattened_reference_before_sha256': digest(b_mos), 'flattened_reference_after_sha256': digest(a_mos),
        'all_bulk_and_model_L_W_nfin_bindings_identical': True,
        'all_logic_cell_types_previously_functionally_validated': True,
        'logic_cell_types_and_counts_equal_to_functionally_validated_original': current_logic == validated_logic,
        'logical_cell_instances': sum(current_logic.values()),
        'effective_lvs_checks': {'ignore_ports': False, 'soft_substrate_pins': False,
                                 'ignore_device_pins': False, 'nmos_pmos_L_W_nfin_absolute_tolerance': 0},
        'decap_instances_retained': dict(source_decap),
        'scope': 'Only empty reference hierarchy is removed. Actual GDS well/tap/conductor geometry remains in extraction and full DRC; no substrate pin or MOS property is ignored.',
    }
    output = HERE / 'physical_audit'
    output.mkdir(exist_ok=True)
    (output / (args.project + '.json')).write_text(json.dumps(report, indent=2) + '\n')

    source = (HERE / 'gcd_official_pex_all_cells.cdl').read_text()
    pattern = re.compile(r'(?im)^\.SUBCKT INVx1_ASAP7_75t_R\b[^\n]*\n(.*?)^\.ENDS', re.S)
    match = pattern.search(source)
    assert match
    block = match[1]
    lines = block.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith('M') and 'nmos_rvt' in line)
    fields = lines[first].split()
    assert fields[4] == 'VSS'
    for kind in ('bulk', 'width'):
        mutant = fields[:]
        if kind == 'bulk':
            mutant[4] = 'VDD'
        else:
            w = next(i for i, value in enumerate(mutant) if value.startswith('W='))
            mutant[w] = 'W=1.62e-07'
            assert fields[w] == 'W=8.1e-08'
        modified = lines[:]
        modified[first] = ' '.join(mutant)
        text = source[:match.start(1)] + '\n'.join(modified) + '\n' + source[match.end(1):]
        (output / ('negative_' + kind + '.cdl')).write_text(text)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
