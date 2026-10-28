#!/usr/bin/env python3
"""Read remote case receipts and reports; merge platform costs on the controller.

Transfers only the selected experiment's reports, model-event receipts and ORFS
journal. Runtime credentials, databases, layouts and datasets are excluded.
"""
import argparse
import base64
import fcntl
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import time

REMOTE = r'''
import base64,json
from pathlib import Path
root=Path(REMOTE_DIR)
names=['STATUS.md','costs.csv','status_snapshot.json','measurement_summary.json',
       'launch_parameters.json','orfs_audit.jsonl']
paths=[root/name for name in names if (root/name).is_file()]
for folder in (root/'cases').glob('*'):
    if folder.is_dir():
        paths.extend(p for p in folder.iterdir() if p.is_file() and
                     (p.suffix in ('.json','.sse')))
total=sum(p.stat().st_size for p in paths)
if total>128*1024*1024:raise RuntimeError('Report sync exceeded bounded payload')
alive=False
try:
    pid=int((root/'runner.pid').read_text())
    cmd=Path('/proc',str(pid),'cmdline').read_bytes()
    alive=b'measure_agent_repair_cost.py' in cmd and str(root).encode() in cmd
except (OSError,ValueError):pass
print(json.dumps({'runner_alive':alive,'files':{
    str(p.relative_to(root)):base64.b64encode(p.read_bytes()).decode() for p in paths}}))
'''


def install_receipts(destination, files):
    """Explicit relative paths only; never follow a local directory symlink."""
    destination = destination.resolve()
    for name, encoded in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid remote report path')
        target = destination.joinpath(*relative.parts)
        if not target.resolve().is_relative_to(destination):
            raise ValueError('Remote report would escape local experiment')
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.sync.tmp')
        temp.write_bytes(base64.b64decode(encoded, validate=True))
        temp.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--remote-dir', required=True)
    parser.add_argument('--source', type=Path, required=True,
                        help='Local population directory containing the two parent manifests')
    parser.add_argument('--interval', type=int, default=20)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    args.source = args.source.resolve()
    if not 5 <= args.interval <= 60:
        parser.error('interval must be between 5 and 60 seconds')
    spec = importlib.util.spec_from_file_location('population', Path(__file__).with_name('summarize_repair_population.py'))
    population = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(population)
    with (args.source / '.remote_report_sync.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        remote_code = 'REMOTE_DIR=' + repr(args.remote_dir) + '\n' + REMOTE
        while True:
            try:
                response = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                           args.host, 'python3', '-'], input=remote_code,
                                          text=True, capture_output=True, timeout=60, check=True)
                payload = json.loads(response.stdout)
                install_receipts(args.source / 'sky130hd', payload['files'])
                report = population.render(args.source)
                print(json.dumps({'generated_at': report['generated_at'],
                                  'platform_usage': report['platform_usage'],
                                  'remote_runner_alive': payload['runner_alive']}, ensure_ascii=False), flush=True)
                local_pid_file = args.source / 'nangate45/runner.pid'
                local_alive = local_pid_file.is_file() and population.process_identity(int(local_pid_file.read_text())) is not None
                if not args.watch or report['all_terminated'] or not (payload['runner_alive'] or local_alive):
                    break
            except Exception as exc:
                print(json.dumps({'sync_error': type(exc).__name__, 'detail': str(exc)[:300]}), flush=True)
                if not args.watch:
                    raise
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
