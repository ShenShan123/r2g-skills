#!/usr/bin/env python3
"""
Serve the multi-project EDA dashboard with auto-refresh.
Regenerates HTML on each page request.

Designed for remote-server use: prints SSH tunnel command so the
dashboard can be opened on your laptop browser.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import subprocess
import socket
import os
import sys


SKILL_DIR = Path(__file__).resolve().parents[2]
BASE = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else (SKILL_DIR.parent / 'design_cases').resolve()
OUT = BASE / '_dashboard'
GENERATE_SCRIPT = SKILL_DIR / 'scripts' / 'dashboard' / 'generate_multi_project_dashboard.py'


def refresh():
    try:
        subprocess.run(
            ['python3', str(GENERATE_SCRIPT), str(BASE)],
            check=True, timeout=30
        )
    except Exception as e:
        print(f'Dashboard refresh failed: {e}')


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/index.html'] or self.path.endswith('.html'):
            refresh()
        return super().do_GET()

    def log_message(self, format, *args):
        """Suppress per-request logs to keep terminal clean."""
        pass


SERVER_IP = '192.10.84.203'


def _get_remote_info():
    """Detect hostname and SSH connection details for tunnel instructions."""
    hostname = socket.gethostname()
    user = os.environ.get('USER', os.environ.get('LOGNAME', '<user>'))
    return hostname, SERVER_IP, user


def _print_access_instructions(port):
    """Print how to access the dashboard from a laptop via SSH tunnel."""
    hostname, remote_ip, user = _get_remote_info()
    is_ssh = bool(os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_CLIENT'))

    print()
    print('=' * 62)
    print('  EDA Spec-to-GDS Dashboard')
    print('=' * 62)
    print(f'  Server  : http://0.0.0.0:{port}/')
    print(f'  Host    : {hostname} ({remote_ip})')
    print(f'  Projects: {BASE}')
    print()

    if is_ssh:
        print('  To view on your laptop, run this on your LOCAL machine:')
        print()
        print(f'    ssh -N -L {port}:localhost:{port} {user}@{remote_ip}')
        print()
        print(f'  Then open in your browser:')
        print(f'    http://localhost:{port}/')
        print()
        print('  Tip: Add -f to run the tunnel in the background:')
        print(f'    ssh -f -N -L {port}:localhost:{port} {user}@{remote_ip}')
    else:
        print(f'  Open in your browser:')
        print(f'    http://localhost:{port}/')

    print('=' * 62)
    print()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    OUT.mkdir(parents=True, exist_ok=True)
    refresh()
    os.chdir(str(OUT))
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    _print_access_instructions(port)
    server.serve_forever()


if __name__ == '__main__':
    main()
