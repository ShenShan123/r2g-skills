"""Exhaustively compare old CDL, official-PEX MOS switches and vendor Verilog.

This validates static binary functionality, not timing or analog behavior.
"""
from collections import Counter
from itertools import product
from pathlib import Path
import argparse
import json
import re
from derive_oai21_reference import HERE, ROOT, Union, circuits

VERILOG = Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/verilog/stdcell')


def verilog_modules():
    modules = {}
    for path in VERILOG.glob('*RVT_TT*.v'):
        text = re.sub(r'//[^\n]*', '', path.read_text())
        for m in re.finditer(r'\bmodule\s+(\w+)\s*\([^;]*;(.+?)\bendmodule', text, re.S):
            body = m.group(2).split('specify')[0]
            inputs = [p.strip() for line in re.findall(r'\binput\s+([^;]+);', body) for p in line.split(',')]
            outputs = [p.strip() for line in re.findall(r'\boutput\s+([^;]+);', body) for p in line.split(',')]
            primitives = [(op, [p.strip() for p in ports.split(',')]) for op, ports in
                          re.findall(r'\b(not|buf|and|nand|or|nor|xor|xnor)\s*\(([^;]+)\);', body)]
            modules[m.group(1).lower()] = {'inputs': inputs, 'outputs': outputs, 'primitives': primitives, 'source': str(path)}
    return modules


def eval_verilog(module, inputs):
    signals = dict(inputs)
    pending = module['primitives'][:]
    while pending:
        new = []
        for op, pins in pending:
            if not all(p in signals for p in pins[1:]):
                new.append((op, pins))
                continue
            args = [signals[p] for p in pins[1:]]
            if op in ('not', 'buf'):
                v = args[0] ^ int(op == 'not')
            elif op in ('and', 'nand'):
                v = int(all(args)) ^ int(op == 'nand')
            elif op in ('or', 'nor'):
                v = int(any(args)) ^ int(op == 'nor')
            else:
                v = (sum(args) % 2) ^ int(op == 'xnor')
            signals[pins[0]] = v
        assert len(new) < len(pending), 'Unresolved vendor Verilog primitive'
        pending = new
    return tuple(signals[p] for p in module['outputs'])


def eval_mos(cell, inputs, outputs):
    mos = [line.split('$', 1)[0].split() for line in cell['lines'] if line[0].upper() == 'M']
    known = {p.upper(): v for p, v in inputs.items()} | {'VDD': 1, 'VSS': 0}
    unknown_gates = sorted({d[2].upper() for d in mos} - set(known))
    assert len(unknown_gates) <= 12
    outcomes = set()
    for values in product((0, 1), repeat=len(unknown_gates)):
        signals = known | dict(zip(unknown_gates, values))
        union = Union()
        for d in mos:
            on = signals[d[2].upper()] == int(d[5].lower().startswith('nmos'))
            if on:
                union.union(d[1], d[3])
        high, low = union.find('VDD'), union.find('VSS')
        if high == low:
            continue
        consistent = True
        for node in unknown_gates:
            group = union.find(node)
            if group not in (high, low) or signals[node] != int(group == high):
                consistent = False
                break
        if not consistent:
            continue
        groups = [union.find(n) for n in outputs]
        if all(g in (high, low) for g in groups):
            outcomes.add(tuple(int(g == high) for g in groups))
    assert len(outcomes) == 1, f'Non-unique switch-level output: {inputs} {outcomes}'
    return next(iter(outcomes))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    args = parser.parse_args()
    old = circuits((ROOT / 'gcd_calibre_input/gcd_device_complete.cdl').read_text())
    new = circuits((HERE / 'asap7_R_official_pex_full.cdl').read_text())
    modules = verilog_modules()
    design = old['gcd'] if not args.project else circuits((ROOT / (args.project + '_input') / 'source.cdl').read_text())['gcd']
    usage = Counter(line.split()[-1] for line in design['lines'] if line.startswith('X'))
    checked, excluded = [], []
    for name, count in sorted(usage.items()):
        if name.startswith('DECAP') or name.startswith('DFF'):
            excluded.append({'cell': name, 'instances': count, 'reason': 'Decap has no Boolean function' if name.startswith('DECAP') else 'Sequential cell; independently checked by exact transistor topology isomorphism in sequential_topology_verification.json'})
            continue
        module = modules[name.lower()]
        cases = []
        for values in product((0, 1), repeat=len(module['inputs'])):
            inputs = dict(zip(module['inputs'], values))
            expected = eval_verilog(module, inputs)
            previous = eval_mos(old[name.lower()], inputs, module['outputs'])
            derived = eval_mos(new[name.lower()], inputs, module['outputs'])
            assert previous == expected == derived, (name, inputs, previous, derived, expected)
            cases.append({'inputs': inputs, 'outputs': dict(zip(module['outputs'], derived))})
        checked.append({'cell': name, 'instances': count, 'input_vectors': len(cases),
                        'old_cdl_official_pex_and_verilog_equal': True, 'truth_table': cases,
                        'vendor_verilog': module['source']})
    report = {'status': 'PASS', 'method': 'Exhaustive binary input enumeration; static CMOS switch conduction with internal-gate fixed point, compared to independently published vendor Verilog primitive logic',
              'combinational_cell_types': len(checked), 'input_vectors': sum(x['input_vectors'] for x in checked),
              'combinational_instances_covered': sum(x['instances'] for x in checked),
              'scope': 'Function only; does not assert electrical/timing equivalence',
              'cells': checked, 'excluded': excluded}
    output_name = 'combinational_truth_verification.json' if not args.project else 'combinational_truth_' + args.project + '.json'
    report['project'] = args.project or 'original_gcd_smoke'
    (HERE / output_name).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'cells'}, indent=2))


if __name__ == '__main__':
    main()
