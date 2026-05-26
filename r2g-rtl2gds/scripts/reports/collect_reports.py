#!/usr/bin/env python3
from pathlib import Path
import json
import sys


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    report = {
        'root': str(root.resolve()),
        'artifacts': {}
    }

    candidates = {
        'lint_log': root / 'lint' / 'lint.log',
        'sim_log': root / 'sim' / 'sim.log',
        'sim_compile_log': root / 'sim' / 'compile.log',
        'vcd': root / 'sim' / 'output.vcd',
        'synth_log': root / 'synth' / 'synth.log',
        'synth_netlist': root / 'synth' / 'synth_output.v',
    }

    for name, path in candidates.items():
        if path.exists():
            report['artifacts'][name] = str(path)

    # Check backend runs
    backend = root / 'backend'
    if backend.exists():
        runs = sorted([d for d in backend.iterdir() if d.is_dir() and d.name.startswith('RUN_')])
        if runs:
            latest = runs[-1]
            report['artifacts']['backend_run'] = str(latest)
            flow_log = latest / 'flow.log'
            if flow_log.exists():
                report['artifacts']['flow_log'] = str(flow_log)
            for gds in latest.rglob('*.gds'):
                report['artifacts']['gds'] = str(gds)
                break
            for def_file in latest.rglob('*.def'):
                report['artifacts']['def'] = str(def_file)
                break

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
