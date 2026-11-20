"""Verify any completed ASAP7 GCD project against its saved original RTL.

Usage: /usr/bin/python3 run_gcd_formal.py gcd_asym_via
The proof uses the original pinned Liberty semantics, with arbitrary initial
register states and a reset/first-request base case. It does not use LVS PEX.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
YOSYS = Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys')


def artifact(path):
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def preparation(rtl, netlist, libraries, prepared):
    lines = [f'read_verilog {rtl}', 'hierarchy -check -top gcd', 'proc', 'opt', 'flatten', 'opt',
             'rename gcd gold', 'design -stash gold']
    lines += [f'read_liberty -ignore_miss_func {lib}' for lib in libraries]
    lines += [f'read_verilog {netlist}', 'hierarchy -check -top gcd', 'proc', 'flatten', 'opt',
              'check -assert', 'select -assert-none t:*_ASAP7*',
              'rename gcd gate', 'design -stash gate', 'design -copy-from gold -as gold gold',
              'design -copy-from gate -as gate gate', f'write_rtlil {prepared}']
    return '\n'.join(lines) + '\n'


def base_script(prepared, depth, vcd):
    return f'''read_rtlil {prepared}
miter -equiv -make_outputs -flatten gold gate miter
hierarchy -top miter
techmap
opt_clean
check -assert
sat -seq {depth + 2} -set-at 1 in_reset 1 -set-at 2 in_req_val 1 -prove trigger 0 -prove-skip 2 -verify -timeout 90 -show-ports -dump_vcd {vcd}
'''


def run_stage(output, name, script, expect_failure=False):
    path = output / (name + '.ys')
    path.write_text(script)
    log = output / (name + '.log')
    start = time.monotonic()
    with (output / (name + '.stdout')).open('w') as stream:
        run = subprocess.run([str(YOSYS), '-Q', '-T', '-l', str(log), str(path)],
                             stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    text = log.read_text()
    if expect_failure:
        assert run.returncode != 0 and 'SAT proof finished - model found: FAIL!' in text, log
    else:
        assert run.returncode == 0, log
    seconds = round(time.monotonic() - start, 3)
    print(f'{name}: {"expected counterexample" if expect_failure else "PASS"}, {seconds}s', flush=True)
    return text, seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project', help='Project name below qualification root or an absolute project path')
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_absolute():
        project = ROOT / project
    project = project.resolve()
    rtl = project / 'rtl/gcd.v'
    netlist = sorted((project / 'backend').glob('RUN_*/results/6_final.v'))[-1]
    run_name = netlist.parent.parent.name
    output = HERE / 'formal' / project.name / run_name
    output.mkdir(parents=True, exist_ok=True)
    original_proof = json.loads((ROOT / 'reference_diagnosis/formal/verification.json').read_text())
    libraries = [Path(x['path']) for x in original_proof['liberties']]
    prepared = output / 'prepared.il'
    proof_script = preparation(rtl, netlist, libraries, prepared) + '''equiv_make gold gate equiv
hierarchy -top equiv
opt_clean
techmap
opt_clean
equiv_simple -seq 4
equiv_induct -seq 8
equiv_status -assert
'''
    proof, induction_s = run_stage(output, 'equiv', proof_script)
    assert 'Equivalence successfully proven!' in proof
    points = re.search(r'Of those cells (\d+) are proven and (\d+) are unproven', proof)
    assert points and points.groups() == ('18', '0')
    steps = re.findall(r'Proving induction step (\d+)', proof)
    depth = int(steps[-1]) if steps else 1
    base, base_s = run_stage(output, 'reset_base', base_script(prepared, depth, output / 'reset_counterexample.vcd'))
    assert 'SAT proof finished - no model found: SUCCESS!' in base

    source = netlist.read_text()
    pattern = re.compile(r'\b(BUFx\w+_ASAP7_75t_R)\s+\S+\s*\([^;]*?\.Y\(resp_val\)[^;]*?\);', re.S)
    match = pattern.search(source)
    assert match, 'Expected the final resp_val buffer for the isolated negative control'
    mutant = output / 'mutant_output_inverted.v'
    mutant.write_text(source[:match.start(1)] + 'INVx1_ASAP7_75t_R' + source[match.end(1):])
    mutant_prepared = output / 'mutant_prepared.il'
    _, mutation_prepare_s = run_stage(output, 'mutant_prepare', preparation(rtl, mutant, libraries, mutant_prepared))
    _, negative_s = run_stage(output, 'mutant_base', base_script(mutant_prepared, depth, output / 'mutant_counterexample.vcd'), True)
    result = {
        'status': 'PASS', 'project': str(project), 'backend_run': run_name,
        'method': 'Yosys RTL versus post-route Verilog using original pinned Liberty logic; induction plus reset/first-request base',
        'tool': subprocess.check_output([str(YOSYS), '-V'], text=True).strip(),
        'rtl': artifact(rtl), 'post_route_netlist': artifact(netlist), 'liberties': [artifact(p) for p in libraries],
        'logical_standard_cell_instances': len(re.findall(r'\b\w+_ASAP7_75t_R\s+\S+\s*\(', source)),
        'induction': {'status': 'PASS', 'depth': depth, 'proven_output_bits': 18, 'unproven_output_bits': 0},
        'base_case': {'status': 'PASS', 'initial_register_states': 'arbitrary', 'reset_high_step': 1,
                      'first_request_step': 2, 'output_equality_steps': list(range(3, depth + 3)),
                      'other_inputs': 'unconstrained, including all request data and subsequent control inputs'},
        'negative_control': {'status': 'EXPECTED_FAIL', 'mutation': 'Invert only the final resp_val output buffer in an isolated copy',
                             'counterexample': artifact(output / 'mutant_counterexample.vcd')},
        'limits': ['Synchronous two-state digital semantics; no physical delays or analog behavior.',
                   'First request initializes the 32 unreset datapath registers; earlier arbitrary resp_msg values are outside the claim.',
                   'Liberty proof does not directly establish PEX sequential transistor semantics; independent MOS topology comparison is required.'],
        'stage_seconds': {'induction': induction_s, 'reset_base': base_s, 'negative_prepare': mutation_prepare_s, 'negative_base': negative_s},
        'logs': {name: artifact(output / (name + '.log')) for name in ['equiv', 'reset_base', 'mutant_prepare', 'mutant_base']},
    }
    (output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    (output / 'REPORT.md').write_text(f'''# {project.name} 最终网表形式等价验证

**PASS。** 使用原始 ASAP7 Liberty 语义，保存的 RTL 与最终布线网表在复位并接受第一笔请求后，全部 18 个输出位持续等价。

- {depth} 步归纳：18 个等价点全部证明。
- 初始寄存器值任意；第 1 步复位，第 2 步请求数据任意，证明第 3 至 {depth + 2} 步全部输出相等，其余输入自由。
- 单独副本翻转 `resp_val` 的负控确实产生反例，原始网表未修改。

输入网表：`{netlist}`。逻辑单元共 {result['logical_standard_cell_instances']} 个。输入、Liberty、日志哈希及实际证明深度见 [verification.json](verification.json)。

本证明使用 Liberty 布尔/状态转移语义，不直接证明 PEX 序贯晶体管行为；该部分需结合独立晶体管网络同构检查。它不覆盖时延、时钟偏斜或物理 DRC/LVS。

复现：`/usr/bin/python3 {Path(__file__).resolve()} {project}`。
''')
    print(output / 'verification.json', flush=True)


if __name__ == '__main__':
    main()
