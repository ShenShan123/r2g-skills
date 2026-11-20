"""Stream completed payloads from compute host to archive host, verifying SHA256.

The worker runs on the SSH control host. Compute keeps scores and logs; large
payloads are released only after the two stream checksums agree.
"""
import argparse
import json
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import time


def wait_for_archive(directory, kind, paths):
    request = Path(directory) / f"archive_{kind}.request.json"
    receipt = request.with_name(request.name.replace(".request.", ".receipt."))
    if receipt.exists():
        return
    pending = request.with_suffix(".tmp")
    pending.write_text(json.dumps({"paths": paths}))
    pending.replace(request)
    while not receipt.exists():
        time.sleep(5)


def ssh(host, code, *args):
    return ["ssh", "-o", "BatchMode=yes", host,
            shlex.join(["python3", "-c", code, *map(str, args)])]


INVENTORY = '''from pathlib import Path
import json,sys
r=Path(sys.argv[1]);result=[]
for p in sorted((r/'runs').rglob('archive_*.request.json')):
 if not p.with_name(p.name.replace('.request.','.receipt.')).exists():
  result.append(str(p.relative_to(r)))
print(json.dumps(result))
'''
SOURCE = '''from pathlib import Path
import hashlib,json,subprocess,sys
p=Path(sys.argv[1]); paths=json.loads(p.read_text())['paths']
t=subprocess.Popen(['tar','-cf','-','-C',str(p.parent),'--',*paths],stdout=subprocess.PIPE)
h=hashlib.sha256();n=0
while True:
 b=t.stdout.read(1024*1024)
 if not b:break
 h.update(b);n+=len(b);sys.stdout.buffer.write(b)
sys.stdout.buffer.flush()
if t.wait():raise SystemExit('tar failed')
print(json.dumps({'sha256':h.hexdigest(),'bytes':n}),file=sys.stderr)
'''
DESTINATION = '''from pathlib import Path
import hashlib,json,sys
p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.partial')
h=hashlib.sha256();n=0
with tmp.open('wb') as f:
 while True:
  b=sys.stdin.buffer.read(1024*1024)
  if not b:break
  h.update(b);n+=len(b);f.write(b)
tmp.replace(p)
print(json.dumps({'sha256':h.hexdigest(),'bytes':n}))
'''
FINALIZE = '''from pathlib import Path
import json,shutil,sys
p=Path(sys.argv[1]);receipt=json.loads(sys.argv[2])
def writable_tree(target):
 target.chmod(target.stat().st_mode | 0o700)
 for child in target.iterdir():
  if child.is_dir() and not child.is_symlink():writable_tree(child)
for name in json.loads(p.read_text())['paths']:
 target=p.parent/name
 if target.is_symlink() or target.is_file():target.unlink()
 elif target.exists():
  writable_tree(target)
  shutil.rmtree(target)
p.with_name(p.name.replace('.request.','.receipt.')).write_text(json.dumps(receipt,indent=2))
'''

VERIFIED = '''from pathlib import Path
import json,sys
p=Path(sys.argv[1]);receipt=json.loads(sys.argv[2])
p.with_name(p.name.replace('.request.','.verified.')).write_text(json.dumps(receipt,indent=2))
'''

READ_VERIFIED = '''from pathlib import Path
import sys
p=Path(sys.argv[1]).with_name(Path(sys.argv[1]).name.replace('.request.','.verified.'))
print(p.read_text() if p.exists() else 'null')
'''


def transfer(source_host, source_root, archive_host, archive_root, relative):
    request = Path(source_root) / relative
    destination = Path(archive_root) / relative.replace('.request.json', '.tar')
    receipt = json.loads(subprocess.check_output(ssh(source_host, READ_VERIFIED, request), text=True))
    if receipt:
        subprocess.run(ssh(source_host, FINALIZE, request, json.dumps(receipt)), check=True)
        print(json.dumps({'archived': relative, **receipt}), flush=True)
        return
    source = subprocess.Popen(ssh(source_host, SOURCE, request), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target = subprocess.run(ssh(archive_host, DESTINATION, destination), stdin=source.stdout,
                            capture_output=True, text=True)
    source.stdout.close()
    errors = source.stderr.read().decode()
    if source.wait() or target.returncode:
        raise RuntimeError(errors + target.stderr)
    sent = json.loads(errors.strip().splitlines()[-1]); received = json.loads(target.stdout)
    if sent != received:
        raise RuntimeError('Archive stream checksum mismatch')
    receipt = {**received, 'host': archive_host, 'path': str(destination)}
    # Persist verified transfer before cleanup can partially remove source files.
    subprocess.run(ssh(source_host, VERIFIED, request, json.dumps(receipt)), check=True)
    subprocess.run(ssh(source_host, FINALIZE, request, json.dumps(receipt)), check=True)
    print(json.dumps({'archived': relative, **receipt}), flush=True)


def sync_metadata(a):
    a.staging.mkdir(parents=True, exist_ok=True)
    excludes = ['image', 'data', 'native_bins', 'reference', 'work', 'tmp', 'home', 'out', 'api']
    subprocess.run(['rsync', '-a', *[f'--exclude={x}' for x in excludes],
                    f'{a.source_host}:{a.source_root}/', str(a.staging) + '/'], check=True)
    subprocess.run(['rsync', '-a', str(a.staging) + '/', f'{a.archive_host}:{a.archive_root}/'], check=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source-host', required=True); p.add_argument('--source-root', required=True)
    p.add_argument('--archive-host', required=True); p.add_argument('--archive-root', required=True)
    p.add_argument('--staging', type=Path, required=True)
    p.add_argument('--poll-seconds', type=float, default=30)
    p.add_argument('--metadata-seconds', type=float, default=30)
    a=p.parse_args()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    next_metadata = 0
    while not stopping.is_set():
        try:
            requests=json.loads(subprocess.check_output(ssh(a.source_host, INVENTORY, a.source_root),text=True))
            for relative in requests:
                transfer(a.source_host,a.source_root,a.archive_host,a.archive_root,relative)
            if time.monotonic() >= next_metadata or stopping.is_set():
                sync_metadata(a)
                next_metadata = time.monotonic() + a.metadata_seconds
                state=a.staging/'campaign_status.json'
                if state.exists() and json.loads(state.read_text())['status']=='complete':
                    return
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as exc:
            print(f'Archive retry: {exc}',flush=True)
        stopping.wait(a.poll_seconds)


if __name__=='__main__':
    main()
