#!/usr/bin/env python3
"""Pull finished ASAP7 baseline runs from 203 to 216, verify, then free 203 (runs ON 216).

  pull_203.py --remote-runs /data/yangao/asap7_baseline/baseline/runs --local-runs DIR [--loop 300] [--once]

216 initiates every connection (203 cannot reach 216's sshd), with ~/.ssh/r2g_from203_ed25519, authorized on
203 for 192.10.84.216 only. Nothing passes through 208.

A run is pulled when its result.json is at least --settle seconds old and it is not the first half of a PDN-0185
retry (pdn_insufficient without pdn_fallback: the driver is about to overwrite it). GDS files are not pulled:
signoff writes its own GDS on 216 with the image's KLayout 0.30.7 (regen_gds.sh), and no dataset reads one.
Hard links inside a run survive (tar keeps them).

Verification is per file: the relative path + byte size list from 203 must equal 216's. Only then are the run's
files on 203 deleted -- except result.json, which stays so a restarted 203 driver still skips the design.
A ledger (LOCAL_RUNS/../pull_ledger.jsonl) records every pull with its file count and bytes.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SSH = ["ssh", "-o", "BatchMode=yes", "-i", os.path.expanduser("~/.ssh/r2g_from203_ed25519"), "yangao@192.10.84.203"]
EXCL = ["--exclude=6_final.gds", "--exclude=*.gds"]


def remote(cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(SSH + [cmd], capture_output=True, text=True, **kw)


LIST_PY = r"""
import glob, json, os, sys, time
root, settle = sys.argv[1], int(sys.argv[2])
out = []
for rj in glob.glob(root + "/*/result.json"):
    d = os.path.dirname(rj)
    if os.path.exists(d + "/.pulled") or time.time() - os.path.getmtime(rj) < settle:
        continue
    try:
        j = json.load(open(rj))
    except Exception:
        continue
    if j.get("pdn_insufficient") and not j.get("pdn_fallback"):
        continue
    out.append(os.path.basename(d))
print("\n".join(sorted(out)))
"""


def candidates(rruns: str, settle: int) -> list[str]:
    r = subprocess.run(SSH + [f"python3 - {rruns} {settle}"], input=LIST_PY, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    return [x for x in r.stdout.split() if x]


def manifest_cmd(root: str) -> str:
    return (f"cd {root} && find . -type f ! -name '*.gds' -printf '%P %s\\n' | LC_ALL=C sort")


def pull_one(rruns: str, lruns: Path, tag: str, ledger) -> str:
    rman = remote(manifest_cmd(f"{rruns}/{tag}"))
    if rman.returncode != 0:
        return f"manifest-failed {rman.stderr[-200:]}"
    dest = lruns / tag
    tmp = lruns / f".{tag}.part"
    subprocess.run(["rm", "-rf", str(tmp)])
    tmp.mkdir(parents=True)
    p1 = subprocess.Popen(SSH + [f"cd {rruns}/{tag} && tar cf - {' '.join(EXCL)} ."], stdout=subprocess.PIPE)
    p2 = subprocess.run(["tar", "xf", "-", "-C", str(tmp)], stdin=p1.stdout)
    p1.stdout.close()
    rc1 = p1.wait()
    if rc1 != 0 or p2.returncode != 0:
        return f"tar-failed rc={rc1}/{p2.returncode}"
    lman = subprocess.run(["bash", "-c", manifest_cmd(str(tmp))], capture_output=True, text=True).stdout
    if lman != rman.stdout:
        return "verify-mismatch"
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)])
    tmp.rename(dest)
    files = [l.split() for l in lman.splitlines() if l.strip()]
    nbytes = sum(int(s) for _, s in files)
    # free 203: everything but result.json; mark pulled
    rm = remote(f"cd {rruns}/{tag} && find . -mindepth 1 ! -name result.json -delete && touch .pulled")
    ledger.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "files": len(files),
                             "bytes": nbytes, "remote_freed": rm.returncode == 0}) + "\n")
    ledger.flush()
    return f"ok {len(files)} files {nbytes / 1e6:.0f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remote-runs", required=True)
    ap.add_argument("--local-runs", required=True, type=Path)
    ap.add_argument("--loop", type=int, default=300)
    ap.add_argument("--settle", type=int, default=600)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    a.local_runs.mkdir(parents=True, exist_ok=True)
    ledger = open(a.local_runs.parent / "pull_ledger.jsonl", "a")
    while True:
        try:
            todo = candidates(a.remote_runs, a.settle)
        except Exception as exc:                                  # noqa: BLE001
            print(time.strftime("%H:%M:%S"), "list failed:", exc, flush=True)
            todo = []
        for tag in todo:
            print(time.strftime("%H:%M:%S"), tag, pull_one(a.remote_runs, a.local_runs, tag, ledger), flush=True)
        if a.once or (a.local_runs.parent / "PULL_STOP").exists():
            return 0
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
