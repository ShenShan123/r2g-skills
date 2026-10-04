"""Reuse native E4 evaluation with isolated, tool-enabled converter execution."""
import ast
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import time

from experiment4_progressive_sandbox import command as sandbox_command

RUNTIME = Path('/home/yangao/r2g_exp4_confirmatory_v3_20260909/runtime')
sys.path.insert(0, str(RUNTIME / 'tools'))
spec = importlib.util.spec_from_file_location('legacy_referee', RUNTIME / 'experiments/run_experiment4_graph_conversion.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
original_run = legacy.run_command


def validate(path):
    text = path.read_text()
    if len(text.encode()) > 200000:
        raise ValueError('Source exceeds 200000 bytes')
    ast.parse(text)


def run(command, log_path, timeout):
    if log_path.name != 'converter.log':
        return original_run(command, log_path, timeout)
    cfg = command[command.index('--config') + 1]
    isolated = sandbox_command(command[0], command[1], cfg, '/home/yangao/r2g_toolchain/OpenROAD-flow-scripts')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open('w') as log:
        proc = subprocess.Popen(isolated, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            import os
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = 124
    return {'command': isolated, 'returncode': code, 'error': 'timeout' if code == 124 else '',
            'elapsed_seconds': round(time.monotonic() - started, 3), 'resources': {}, 'log': str(log_path)}


legacy.RUNNER_VERSION = str(legacy.RUNNER_VERSION) + '-progressive-isolated-1'
legacy.validate_converter = validate
legacy.run_command = run
if __name__ == '__main__':
    legacy.main()
