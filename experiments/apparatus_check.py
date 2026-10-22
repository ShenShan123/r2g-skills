#!/usr/bin/env python3
"""Verify every declared ingredient is present and unchanged, before a campaign.

Three campaigns were launched with an ingredient missing and nothing noticed
until the verdicts were wrong. Reading the launch script cannot reveal an
omission -- the script says what it does, not what it fails to do. So this
checks the declaration in apparatus.json against what is actually on disk and
in the image, and refuses rather than warns: a campaign that costs 29 hours
should not start at all if a file it needs is absent.

Fingerprints are compared by prefix, since that is what the manifest records.
A file that is present but CHANGED is reported separately from one that is
missing, because the two mean different things: the first invalidates the
comparison with earlier rounds, the second invalidates the run.

Usage:
  apparatus_check.py                      # check everything
  apparatus_check.py --platform nangate45 # only what that platform needs
  apparatus_check.py --layer repair       # what the repair arm adds
  apparatus_check.py --print              # the declaration as a table, no checks
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "apparatus.json"
IMAGE_PREFIX = "IMAGE:"
IMAGE_ROOT = "/r2g_toolchain/OpenROAD-flow-scripts/flow"


def sha_prefix(path: Path, n: int = 16) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:n]
    except OSError:
        return None


def image_sha(image: str, rel: str, n: int = 16) -> str | None:
    """Hash a file inside the image. Needs podman; absence is reported, not
    silently treated as a pass."""
    if not shutil.which("podman"):
        return "NO_PODMAN"
    try:
        out = subprocess.run(
            ["podman", "run", "--rm", image, "bash", "-lc",
             "sha256sum %s/%s 2>/dev/null | cut -c1-%d" % (IMAGE_ROOT, rel, n)],
            capture_output=True, text=True, timeout=180)
        got = out.stdout.strip()
        return got or None
    except (subprocess.SubprocessError, OSError):
        return None


def check_one(ing: dict, d: Path, image: str) -> list[tuple[str, str, str]]:
    """Return (status, detail, severity) rows for one ingredient."""
    rows = []
    sev_missing = "FATAL" if "must stop" in (ing.get("fail_if_missing") or "") else "WARN"

    # a declaration that is a value, not a file -- nothing on disk to hash
    if "values" in ing or "value" in ing:
        v = ing.get("values") or ing.get("value")
        rows.append(("DECLARED", json.dumps(v, ensure_ascii=False), "INFO"))
        return rows

    want = ing.get("sha256")
    path = ing.get("path", "")

    # several files under one id
    if isinstance(want, dict):
        base = d / Path(path).parent if not path.startswith(IMAGE_PREFIX) else None
        for name, exp in want.items():
            p = (base / name) if base else None
            got = sha_prefix(p) if p else None
            if got is None:
                rows.append(("MISSING", "%s" % name, sev_missing))
            elif not got.startswith(exp[:len(got)]) and not exp.startswith(got[:len(exp)]):
                rows.append(("CHANGED", "%s: %s != 声明的 %s" % (name, got, exp), "WARN"))
            else:
                rows.append(("OK", name, "INFO"))
        return rows

    if path.startswith(IMAGE_PREFIX):
        rel = path[len(IMAGE_PREFIX):]
        got = image_sha(image, rel)
        if got == "NO_PODMAN":
            rows.append(("SKIP", "无 podman，镜像内文件未校验: %s" % rel, "WARN"))
        elif got is None:
            rows.append(("MISSING", "镜像内缺 %s" % rel, sev_missing))
        elif want and not got.startswith(want[:len(got)]):
            rows.append(("CHANGED", "%s: %s != 声明的 %s" % (rel, got, want), "WARN"))
        else:
            rows.append(("OK", rel, "INFO"))
        return rows

    p = d / path
    got = sha_prefix(p)
    if got is None:
        rows.append(("MISSING", str(p), sev_missing))
    elif ing.get("symbol") and ("def %s(" % ing["symbol"]) not in p.read_text(errors="replace"):
        # A code ingredient is declared as file + function. The path used to
        # carry both ("orfs_spec.py write_config()"), which no file is named, so
        # every run printed "!! 缺" above a closing "全部配料齐备" -- a control
        # that reports missing and passes at once teaches the reader to skip it.
        rows.append(("MISSING", "%s 里没有 %s()" % (path, ing["symbol"]), sev_missing))
    elif want and not got.startswith(want[:len(got)]):
        rows.append(("CHANGED", "%s: %s != 声明的 %s" % (path, got, want), "WARN"))
    else:
        rows.append(("OK", path, "INFO"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", help="只校验这个平台需要的（含 all）")
    ap.add_argument("--layer", choices=["checking", "apparatus", "repair"])
    ap.add_argument("--print", action="store_true", dest="just_print")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--d", default=os.environ.get("D", "/proj/workarea/yangao"))
    a = ap.parse_args()

    man = json.loads(Path(a.manifest).read_text())
    d = Path(a.d)
    image = man["image"]["ref"]
    ings = man["ingredients"]
    if a.platform:
        ings = [i for i in ings if i.get("platform") in (a.platform, "all")]
    if a.layer:
        ings = [i for i in ings if i.get("layer") == a.layer]

    print("器具声明 %s   主机 %s   D=%s" % (Path(a.manifest).name, man.get("host"), d))
    print("镜像 %s (%s)" % (image, man["image"]["id"]))
    print()

    if a.just_print:
        for i in ings:
            print("  [%s] %-24s %s" % (i["layer"][:9].ljust(9), i["id"], i.get("platform")))
            print("      为什么: %s" % (i.get("why") or "")[:150])
            if i.get("equivalence"):
                print("      等价性: %s" % i["equivalence"][:150])
            if i.get("not_gaming"):
                print("      非放水: %s" % i["not_gaming"][:150])
        return 0

    fatal = changed = 0
    for i in ings:
        rows = check_one(i, d, image)
        head = "  [%s] %-24s" % (i["layer"][:9].ljust(9), i["id"])
        mine_fatal = False
        for status, detail, sev in rows:
            mark = {"OK": "  ok", "DECLARED": "声明", "MISSING": "!! 缺",
                    "CHANGED": "!! 变", "SKIP": "  跳"}[status]
            print("%s %s %s" % (head, mark, detail[:110]))
            head = "  " + " " * 36
            if status == "MISSING":
                if sev == "FATAL":
                    fatal += 1
                mine_fatal = True
            elif status == "CHANGED":
                changed += 1
        if mine_fatal and i.get("fail_if_missing"):
            print("  " + " " * 36 + "     后果: %s" % i["fail_if_missing"][:110])

    print()
    if fatal:
        print("!! %d 项致命缺失 —— 不要开跑。" % fatal)
        return 70
    if changed:
        print("!! %d 项指纹与声明不符 —— 与早先各轮不再可比，先确认是有意的改动。" % changed)
        return 71
    print("全部配料齐备且指纹相符。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
