#!/usr/bin/env python3
"""Start the installed web server on loopback and check its shipped assets/API."""
import argparse
import json
import re
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('r2g', help='Installed r2g executable')
args = parser.parse_args()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
opener = build_opener(ProxyHandler({}))
base = f'http://127.0.0.1:{port}'


def get(path):
    with opener.open(base + path, timeout=5) as response:
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}: {path}')
        return response.read()


proc = subprocess.Popen([args.r2g, 'ui', '--host', '127.0.0.1', '--port', str(port)])
try:
    deadline = time.monotonic() + 60
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f'Web server exited: {proc.returncode}')
        try:
            health = json.loads(get('/api/health'))
            break
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    html = get('/').decode()
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    if not assets or not any(p.endswith('.js') for p in assets):
        raise ValueError('Shipped UI assets missing')
    for path in assets:
        if not get(path):
            raise ValueError(f'Empty asset: {path}')
    platforms = json.loads(get('/api/platforms'))['platforms']
    expected = {'nangate45', 'sky130hd', 'gf180'}
    if {p['id'] for p in platforms} != expected:
        raise ValueError(f'Wrong platform list: {platforms}')
    defaults = {p: json.loads(get('/api/params?platform=' + p))['defaults'] for p in sorted(expected)}
    print(json.dumps(dict(status='pass', assets=assets, defaults=defaults), indent=2))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
