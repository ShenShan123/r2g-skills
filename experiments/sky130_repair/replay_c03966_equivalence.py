#!/usr/bin/env python3
"""Compare independent SV elaborations of the frozen C03966 configuration.

This checks translation, not synthesis mapping, layout, or all parameters.
clk2fflogic is applied only to verification models, preserving reset events.
No internal named-wire correspondence or reset assumption is imposed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv, log, timeout=60):
    with log.open('w') as out:
        return subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT,
                              timeout=timeout).returncode


def quote(path):
    # Tcl brace quoting; paths containing braces are deliberately unsupported.
    value = str(path)
    if any(c in value for c in '{}\n\r'):
        raise ValueError('unsupported Tcl path')
    return '{' + value + '}'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--yosys', default='/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys')
    p.add_argument('--slang-yosys', default='/opt/OpenROAD/oss-cad-suite/bin/yosys')
    p.add_argument('--sv2v', default='/opt/OpenROAD/2_convert_verilog_tool/sv2v-Linux/sv2v')
    a = p.parse_args()
    root = a.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = HERE / 'semantic_evidence/c03966_translation/rv_plic_target.sv'
    if sha(source) != 'f866f3186dd9a339b045c7647773681c5b2560cc6fd097a1ce1ebbf7b4ea9471':
        raise ValueError('frozen source changed')
    with (root / 'sv2v.v').open('w') as out, (root / 'sv2v.log').open('w') as err:
        subprocess.run([a.sv2v, '-DSYNTHESIS', str(source)], stdout=out,
                       stderr=err, timeout=60, check=True)
    slang = root / 'slang.tcl'
    slang.write_text('yosys plugin -i slang\n'
        'yosys read_slang -D SYNTHESIS --keep-hierarchy --compat=vcs '
        '--ignore-assertions --top rv_plic_target ' + quote(source) + '\n'
        'yosys hierarchy -check -top rv_plic_target\nyosys proc\nyosys opt_clean\n'
        'yosys write_verilog -noattr ' + quote(root / 'slang.v') + '\n')
    if run([a.slang_yosys, '-T', '-c', str(slang)], root / 'slang.log'):
        raise RuntimeError('Slang conversion failed')
    text = (root / 'slang.v').read_text()
    if text.count("threshold + 3'h1") != 1:
        raise ValueError('negative-control target changed')
    (root / 'negative.v').write_text(text.replace("threshold + 3'h1", "threshold + 3'h2"))
    results = []
    for negative in (False, True):
        name = 'negative' if negative else 'equivalent'
        script = ''
        for module, filename in [('gold', 'sv2v.v'), ('gate', 'negative.v' if negative else 'slang.v')]:
            script += ('yosys read_verilog ' + quote(root / filename) + '\n'
                       'yosys hierarchy -check -top rv_plic_target\n'
                       'yosys proc\nyosys flatten\nyosys opt\nyosys clk2fflogic\n'
                       'yosys rename -hide\nyosys rename rv_plic_target ' + module + '\n'
                       'yosys design -stash ' + module + '\n')
        script += ('yosys design -copy-from gold -as gold gold\n'
                   'yosys design -copy-from gate -as gate gate\n'
                   'yosys equiv_make gold gate equiv\nyosys hierarchy -top equiv\n'
                   'yosys equiv_simple -seq 2\nyosys equiv_induct -seq 4\n'
                   'yosys equiv_status -assert\n')
        path = root / (name + '.tcl')
        path.write_text(script)
        log = root / (name + '.log')
        rc = run([a.yosys, '-Q', '-T', '-c', str(path)], log)
        output = log.read_text()
        expected = ('0 are proven and 7 are unproven.' if negative
                    else '7 are proven and 0 are unproven.')
        if expected not in output or (rc == 0) == negative:
            raise RuntimeError(name + ': unexpected verifier result')
        results.append(dict(case=name, returncode=rc, proven_outputs=0 if negative else 7))
    record = dict(design='C03966__rv_plic_target__rv_plic',
        source_sha256=sha(source), results=results,
        parameters=dict(N_SOURCE=32, MAX_PRIO=7, ALGORITHM='SEQUENTIAL', SRCW=6, PRIOW=3),
        reference='Independent sv2v translation of identical original SystemVerilog',
        scope='Seven real output bits; internal wires hidden; event-sampled clk2fflogic reset model; no input/reset constraints',
        limits='Default frozen configuration only; not four-state simulator equivalence, cell mapping, timing closure, or physical signoff',
        original_threshold_wrap_preserved=True, physical_signoff=False,
        sha256={f.name:sha(f) for f in root.iterdir() if f.is_file()},
        tool_sha256={k:sha(Path(v)) for k,v in [('yosys',a.yosys),('slang_yosys',a.slang_yosys),('sv2v',a.sv2v)]})
    (root / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
