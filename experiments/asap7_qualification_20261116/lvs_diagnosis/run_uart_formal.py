"""Prove saved UART RTL against a completed final netlist; no production edits.

State aliases are proof points, not assumptions. Two unreset internal data
registers are observed as zero while their bit counter is zero (idle). The real
data paths and every external output remain unchanged. The masked observation
allows a reset-only base case with arbitrary physical initial register states.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
YOSYS = '/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys'


def artifact(p):
    return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def annotate(source, target):
    states = {}
    pattern = r'DFFHQNx\d+_ASAP7_75t_R\s+\\(\S+)\s+\(.*?\.QN\(([^)]+)\)\);'
    for inst, qn in re.findall(pattern, source, re.S):
        base = inst.split('$_')[0]
        match = re.fullmatch(r'(.+)\[(\d+)\]', base)
        reg, bit = (match[1], int(match[2])) if match else (base, None)
        states.setdefault(reg, {})[bit] = qn
    assert len(states) == 15 and sum(map(len, states.values())) == 79, states
    extra = []
    for reg, bits in states.items():
        if None in bits:
            extra += [f'(* keep = 1 *) wire \\{reg} ;', f'assign \\{reg} = ~{bits[None]};']
        else:
            assert set(bits) == set(range(max(bits) + 1)), reg
            extra.append(f'(* keep = 1 *) wire [{max(bits)}:0] \\{reg} ;')
            extra += [f'assign \\{reg} [{bit}] = ~{qn};' for bit, qn in sorted(bits.items())]
    assert source.count('endmodule') == 1
    target.write_text(source.replace('endmodule', '\n'.join(extra) + '\nendmodule'))
    return {k: {str(b): v for b, v in d.items()} for k, d in states.items()}


def preparation(rtl, netlist, libs, prepared):
    lines = [f'read_verilog {" ".join(map(str, rtl))}', 'hierarchy -check -top uart',
             'proc', 'opt', 'flatten', 'opt', 'rename uart gold', 'design -stash gold']
    lines += [f'read_liberty -ignore_miss_func {lib}' for lib in libs]
    lines += [f'read_verilog {netlist}', 'hierarchy -check -top uart', 'proc', 'flatten',
              'opt', 'check -assert', 'select -assert-none t:*_ASAP7*', 'rename uart gate',
              'design -stash gate', 'design -copy-from gold -as gold gold',
              'design -copy-from gate -as gate gate', 'setattr -unset init w:*',
              f'write_rtlil {prepared}']
    return '\n'.join(lines) + '\n'


def mask_idle_observations(prepared, target):
    source = prepared.read_text()
    integrity = {}
    for top in ['gold', 'gate']:
        match = re.search(r'(module \\' + top + r'\n)(.*?)(\nend)', source, re.S)
        body = match[2]
        original_body = body
        for which, width in [('tx', 9), ('rx', 8)]:
            reg = f'uart_{which}_inst.data_reg'
            raw = f'__raw_{top}_{reg}'
            body = body.replace('\\' + reg + ' ', '\\' + raw + ' ').replace('\\' + reg + '\n', '\\' + raw + '\n')
            prefix = f'masked_{which}'
            body += f'''
  attribute \\keep 1
  wire width {width} \\{reg}
  wire \\{prefix}_idle
  cell $logic_not \\{prefix}_is_idle
    parameter \\A_SIGNED 0
    parameter \\A_WIDTH 4
    parameter \\Y_WIDTH 1
    connect \\A \\uart_{which}_inst.bit_cnt
    connect \\Y \\{prefix}_idle
  end
  cell $mux \\{prefix}_mask
    parameter \\WIDTH {width}
    connect \\A \\{raw}
    connect \\B {width}'{'0' * width}
    connect \\S \\{prefix}_idle
    connect \\Y \\{reg}
  end
'''
        restored = body
        for which in ['tx', 'rx']:
            restored = restored.replace(f'\\__raw_{top}_uart_{which}_inst.data_reg',
                                        f'\\uart_{which}_inst.data_reg')
        cell_pattern = re.compile(r'^  cell .*?^  end$', re.M | re.S)
        original_cells = cell_pattern.findall(original_body)
        restored_cells = [c for c in cell_pattern.findall(restored)
                          if not re.match(r'  cell \S+ \\masked_(tx|rx)_', c)]
        assert original_cells == restored_cells, 'Observation transformation changed an existing primitive cell'
        outside_original = cell_pattern.sub('', original_body)
        outside_restored = cell_pattern.sub('', restored)
        original_connections = re.findall(r'^  connect .*$', outside_original, re.M)
        restored_connections = re.findall(r'^  connect .*$', outside_restored, re.M)
        assert original_connections == restored_connections, 'Observation transformation changed an existing direct connection'
        integrity[top] = {'existing_cells_unchanged': len(original_cells),
                          'direct_connections_unchanged': len(original_connections),
                          'primitive_graph_sha256_after_undoing_wire_rename': hashlib.sha256('\n'.join(original_cells + original_connections).encode()).hexdigest(),
                          'added_observation_cells': 4}
        source = source[:match.start(2)] + body + source[match.end(2):]
    target.write_text(source)
    target.with_suffix('.observation_integrity.json').write_text(json.dumps(integrity, indent=2) + '\n')


def run(output, name, script, negative=False):
    path = output / (name + '.ys')
    path.write_text(script)
    start = time.monotonic()
    with (output / (name + '.stdout')).open('w') as stream:
        result = subprocess.run([YOSYS, '-Q', '-T', '-l', str(output / (name + '.log')), str(path)],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    log = (output / (name + '.log')).read_text()
    assert (result.returncode != 0 and 'SAT proof finished - model found: FAIL!' in log) if negative else result.returncode == 0, path
    seconds = round(time.monotonic() - start, 3)
    print(name, 'EXPECTED_FAIL' if negative else 'PASS', seconds, flush=True)
    return log, seconds


def base_script(prepared, vcd):
    return f'''read_rtlil {prepared}
expose -shared gold/w:uart_* gate/w:uart_*
miter -equiv -make_outputs -flatten gold gate miter
hierarchy -top miter
techmap
opt_clean
check -assert
sat -seq 4 -set-at 1 in_rst 1 -prove trigger 0 -prove-skip 1 -verify -timeout 90 -show-ports -dump_vcd {vcd}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_absolute():
        project = ROOT / project
    project = project.resolve()
    out = project / 'formal' / 'final_verification'
    out.mkdir(parents=True, exist_ok=True)
    rtl = [project / 'rtl' / f for f in ['uart.v', 'uart_rx.v', 'uart_tx.v']]
    net = sorted((project / 'backend').glob('RUN_*/results/6_final.v'))[-1]
    libs = [Path(p) for p in json.loads((ROOT / 'multi_uart/formal/inputs.json').read_text())['libs']]
    annotated = out / 'annotated_netlist.v'
    states = annotate(net.read_text(), annotated)
    prepared, masked = out / 'prepared.il', out / 'masked_prepared.il'
    _, prep_s = run(out, 'prepare', preparation(rtl, annotated, libs, prepared))
    mask_idle_observations(prepared, masked)
    proof, proof_s = run(out, 'equiv', f'''read_rtlil {masked}
equiv_make gold gate equiv
hierarchy -top equiv
opt_clean
techmap
opt_clean
equiv_simple -seq 4
equiv_induct -seq 4
equiv_status -assert
''')
    assert 'Of those cells 81 are proven and 0 are unproven' in proof
    base, base_s = run(out, 'reset_base', base_script(masked, out / 'reset_counterexample.vcd'))
    assert 'SAT proof finished - no model found: SUCCESS!' in base
    source = net.read_text()
    pattern = re.compile(r'\b(BUFx\w+_ASAP7_75t_R)\s+\S+\s*\([^;]*?\.Y\(txd\)[^;]*?\);', re.S)
    match = pattern.search(source)
    assert match
    mutation = source[:match.start(1)] + 'INVx1_ASAP7_75t_R' + source[match.end(1):]
    mutant = out / 'mutant_output_inverted.v'
    annotate(mutation, mutant)
    mutant_prepared, mutant_masked = out / 'mutant_prepared.il', out / 'mutant_masked.il'
    run(out, 'mutant_prepare', preparation(rtl, mutant, libs, mutant_prepared))
    mask_idle_observations(mutant_prepared, mutant_masked)
    _, negative_s = run(out, 'mutant_reset', base_script(mutant_masked, out / 'mutant_counterexample.vcd'), True)
    result = {'status': 'PASS', 'project': str(project), 'rtl': [artifact(p) for p in rtl],
              'final_netlist': artifact(net), 'liberty': [artifact(p) for p in libs],
              'induction': {'depth': 2, 'proven_points': 81, 'unproven_points': 0,
                            'register_bits': 79, 'output_bits': 15},
              'base': {'initial_register_states': 'arbitrary; all init attributes removed',
                       'reset_high_step': 1, 'other_inputs': 'unconstrained at every step',
                       'all_output_and_observed_state_equal_steps': [2, 3, 4]},
              'idle_observations': 'Only internal RX/TX data_reg proof observations are zeroed while bit_cnt=0; real circuitry and all output functions unchanged.',
              'negative_control': 'TX output buffer inverted: SAT counterexample, expected failure',
              'seconds': {'preparation': prep_s, 'induction': proof_s, 'reset_base': base_s, 'negative_control': negative_s},
              'state_aliases': states,
              'limits': 'Synchronous two-state digital Liberty semantics; no analog timing or transistor-level PEX functionality claim.'}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print('FINAL PASS', out, flush=True)


if __name__ == '__main__':
    main()
