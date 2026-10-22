#!/usr/bin/env python3
"""Frontend repair: restore memory-init files the RTL package left out.

Nine nangate45 dev designs fail synthesis on `$readmemh/$readmemb` because the
handoff package carries the Verilog but not the .hex/.bin/.mem it loads. Every
one of those files exists in the design's OWN upstream repository at the commit
the package pins (DESIGN.json "repository"), so the repair restores exactly
those bytes -- verified against git's blob hash from the upstream tree -- into
an overlay copy of the design's source directory. The package is never
modified; the flow reads the overlay instead.

For each design writes <out>/<design>/_fe/<repo>/... and <out>/<design>/fe.json
(files restored, their upstream path and blob sha, and any path rewrite).

Usage: fetch_datafiles.py <out_root> <design> [<design> ...]
"""
import base64
import hashlib
import os
import time
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

PKG = Path("/proj/workarea/yangao/rtl-certified-handoff-0002")
READMEM = re.compile(r'\$readmem[hb]\s*\(\s*"([^"]+)"')


def http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r2g-fetch"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


def tree(url: str, sha: str):
    if "github.com/" in url:
        repo = url.split("github.com/")[1].rstrip("/")
        t = json.loads(http("https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, sha)))
        raw = "https://api.github.com/repos/%s/git/blobs/%%s" % repo
    elif "codeberg.org/" in url:
        repo = url.split("codeberg.org/")[1].rstrip("/")
        t = json.loads(http("https://codeberg.org/api/v1/repos/%s/git/trees/%s?recursive=1" % (repo, sha)))
        raw = "https://codeberg.org/api/v1/repos/%s/git/blobs/%%s" % repo
    else:
        raise SystemExit("unsupported host: " + url)
    return [e for e in t.get("tree", []) if e.get("type") == "blob"], raw


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main() -> int:
    out_root = Path(sys.argv[1])
    for name in sys.argv[2:]:
        dd = PKG / "certified" / name
        meta = json.loads((dd / "DESIGN.json").read_text())
        repo_dir = (dd / meta["source_dir"]).resolve()
        rep = meta["repository"]
        url = rep.get("url")
        sha = rep.get("commit") or rep.get("sha") or rep.get("revision")
        ov = out_root / name / "_fe" / repo_dir.name
        if ov.parent.parent.exists():
            shutil.rmtree(ov.parent.parent)
        shutil.copytree(repo_dir, ov)
        blobs, raw = tree(url, sha)
        record = {"design": name, "upstream": url, "commit": sha, "restored": [], "rewrites": []}
        # every $readmem filename named by a listed source, plus the defaults
        # of parameters used as one (INITIAL_COEFFS / INIT_FILE)
        wanted = set()
        for f in meta["source_files"]:
            src = (dd / f["file"]).resolve()
            txt = src.read_text(errors="replace")
            wanted |= {(src, m) for m in READMEM.findall(txt)}
            alltxt = "\n".join(re.sub(r"//[^\n]*", "", (dd / d["file"]).resolve().read_text(errors="replace"))
                               for d in meta["source_files"])
            for p in re.findall(r'\$readmem[hb]\s*\(\s*([A-Za-z_]\w*)\s*,', txt):
                # a default (`P = "x"`) or an instance override (`.P("x")`), and
                # one level of indirection (`.P(Q)` with `Q = "x"` in the parent)
                names = {p} | set(re.findall(r'\.%s\s*\(\s*([A-Za-z_]\w*)\s*\)' % p, alltxt))
                for q in names:
                    for v in re.findall(r'\b%s\s*(?:=|\()\s*"([^"]+)"' % q, alltxt):
                        if v:
                            wanted.add((src, v))
        for src, fname in sorted(wanted, key=str):
            rel_dir = src.parent.relative_to(repo_dir)
            if re.match(r"^[A-Za-z]:/", fname):        # an author's absolute Windows path
                base = Path(fname).name
                tail = "/".join(Path(fname).parts[-2:])
                cands = [b for b in blobs if b["path"].endswith(tail)]
                target_rel = Path(cands[0]["path"]) if len(cands) == 1 else None
            else:
                target_rel = Path(os.path.normpath(os.path.join(str(rel_dir), fname)))
                cands = [b for b in blobs if b["path"] == str(target_rel)]
            if target_rel is None or len(cands) != 1:
                record["restored"].append({"name": fname, "status": "not_found_upstream"})
                continue
            b = cands[0]
            dst = ov / b["path"]
            if dst.exists():
                record["restored"].append({"name": fname, "status": "already_in_package"})
                continue
            data = base64.b64decode(json.loads(http(raw % b["sha"]))["content"])
            if blob_sha(data) != b["sha"]:
                raise SystemExit("blob sha mismatch for %s %s" % (name, b["path"]))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            record["restored"].append({"name": fname, "upstream_path": b["path"], "blob_sha": b["sha"],
                                       "bytes": len(data)})
            if re.match(r"^[A-Za-z]:/", fname):
                # the source names the file by the author's local path; point it
                # at the restored copy (the ONLY source change this repair makes)
                ov_src = ov / src.relative_to(repo_dir)
                for f2 in meta["source_files"]:
                    p2 = ov / (dd / f2["file"]).resolve().relative_to(repo_dir)
                    t = p2.read_text(errors="replace")
                    if fname in t:
                        p2.write_text(t.replace(fname, "/work/_fe/%s/%s" % (repo_dir.name, b["path"])))
                        record["rewrites"].append({"file": str(p2.relative_to(ov)), "from": fname,
                                                   "to": "/work/_fe/%s/%s" % (repo_dir.name, b["path"])})
        (out_root / name / "fe.json").write_text(json.dumps(record, indent=1))
        print(name, [(r.get("upstream_path") or r["name"], r.get("status", "ok")) for r in record["restored"]],
              "rewrites=%d" % len(record["rewrites"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
