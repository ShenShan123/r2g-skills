#!/usr/bin/env python3
"""Refresh bounded execution-host evidence and the success-only cost ledger.

No model/EDA calls, source transfers, database copies or credential reads.
Remote source entries explicitly declare host and remote_path. Static retired
cohorts are read from their existing local mirrors without revisiting the host.
"""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

import account_successful_repair_cost as accounting
import export_repair_cost_evidence as evidence
import sync_remote_cost_reports as sync


def alive(root):
    try:
        pid = int((root / 'runner.pid').read_text())
        command = Path('/proc', str(pid), 'cmdline').read_bytes()
        return b'measure_agent_repair_cost.py' in command and str(root).encode() in command
    except (OSError, ValueError):
        return False


def refresh(config_path):
    config = accounting.read(config_path)
    any_alive = False
    for source in config['sources']:
        if not source.get('refresh_evidence'):
            continue
        local = Path(source['path'])
        if source.get('remote_path'):
            # Both remote snippets contain stdlib imports only. Reports are a
            # bounded allowlist defined by the shared sync implementation.
            exporter = Path(evidence.__file__).read_text()
            code = ('namespace={"__name__":"evidence_module"}\n'
                    'exec(compile(' + repr(exporter) + ', "evidence_export", "exec"),namespace)\n'
                    'namespace["export"](namespace["Path"](' + repr(source['remote_path']) + '))\n'
                    'REMOTE_DIR=' + repr(source['remote_path']) + '\n' + sync.REMOTE)
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                     source['ssh_host'], 'python3', '-'], input=code,
                                    text=True, capture_output=True, check=True, timeout=60)
            payload = json.loads(result.stdout)
            sync.install_receipts(local, payload['files'])
            any_alive |= payload['runner_alive']
        else:
            evidence.export(local)
            any_alive |= alive(local)
    return accounting.render(config_path), any_alive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.interval <= 60:
        parser.error('interval must be between 5 and 60 seconds')
    output = Path(accounting.read(args.config)['output'])
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.refresh.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                result, running = refresh(args.config)
                print(json.dumps({'platforms': result, 'runners_alive': running}, ensure_ascii=False), flush=True)
                if not args.watch or not running:
                    return
            except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
                print(json.dumps({'refresh_error': type(exc).__name__, 'detail': str(exc)[:300]}), flush=True)
                if not args.watch:
                    raise
            # Accounting integrity errors intentionally stop the monitor. They
            # must be reviewed rather than silently rewriting an accepted row.
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
