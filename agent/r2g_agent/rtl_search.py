"""Find RTL for the user: search public repositories, fetch one, list its designs.

search()   rtl-acquire's discover_repo_manifest_candidates.py (GitHub search with
           its quality scoring) -> candidate repositories.
fetch()    shallow clone into R2G_HOME/rtl_sources, then one candidate design per
           directory holding HDL: its uninstantiated modules (top candidates)
           and the modules it instantiates but does not define. A directory
           with none missing is self-contained and can be submitted as a job
           path as it is; one with missing modules would fail synthesis, so it
           is reported, not hidden. The licence is read from the repository's
           own LICENSE file and shown; it is the user's call whether it fits.

Nothing is run here: the model shows the candidates and the user picks.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import project
from .paths import r2g_home, skills_dir

MAX_REPO_BYTES = 500 * 1024 * 1024
# yosys/ORFS-known cells a design may instantiate without defining
_PRIMS = {"and", "or", "nand", "nor", "xor", "xnor", "not", "buf", "bufif0", "bufif1",
          "notif0", "notif1", "pullup", "pulldown", "tran", "rtran",
          # statements _INST_RE also matches (`assert property (...)`, `wait (...)`)
          "assert", "assume", "cover", "restrict", "expect", "property", "sequence", "disable",
          "wait", "repeat", "while", "forever", "unique", "priority", "foreach", "do", "fork"}

_LICENSES = (("Apache License", "Apache-2.0"), ("MIT License", "MIT"),
             ("Permission is hereby granted, free of charge", "MIT"),
             ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"), ("GNU GENERAL PUBLIC LICENSE", "GPL"),
             ("Mozilla Public License", "MPL-2.0"), ("CERN Open Hardware Licence", "CERN-OHL"),
             ("Solderpad Hardware License", "SHL"), ("ISC License", "ISC"),
             ("Redistribution and use in source and binary forms", "BSD"),
             ("This is free and unencumbered software", "Unlicense"))


def search(keyword: str, count: int = 10, min_stars: int = 5) -> list[dict]:
    out = r2g_home() / "search" / time.strftime("%m%d%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    script = skills_dir() / "rtl-acquire" / "scripts" / "acquire" / "discover_repo_manifest_candidates.py"
    r = subprocess.run(["python3", str(script), "--keyword", keyword, "--backend", "github",
                        "--per-query-limit", str(max(1, min(count, 30))), "--min-stars", str(min_stars),
                        "--out-csv", str(out / "c.csv"), "--out-md", str(out / "c.md")],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not (out / "c.csv").is_file():
        raise RuntimeError("搜索失败：%s" % (r.stderr or r.stdout)[-400:])
    rows = list(csv.DictReader((out / "c.csv").open()))
    rows.sort(key=lambda x: -float(x.get("score") or 0))
    return [{"repo": x["full_name"], "url": x["repo_url"], "description": x.get("description", ""),
             "stars": int(x.get("stars") or 0), "size": x.get("size_guess"),
             "quality": x.get("quality_bucket"), "last_push": x.get("pushed_at")} for x in rows[:count]]


def _license(root: Path) -> str:
    for p in sorted(root.glob("LICEN[CS]E*")) + sorted(root.glob("COPYING*")):
        try:
            head = p.read_text(errors="replace")[:4000]
        except OSError:
            continue
        for needle, name in _LICENSES:
            if needle.lower() in head.lower():
                return name
        return "见 %s（未识别）" % p.name
    return "未找到许可证文件"


# verification-only code synthesis never reads (no define is set for it)
_VERIF = {"FORMAL", "SIMULATION", "SIM", "COCOTB_SIM", "VERILATOR", "TESTBENCH"}
_DIRECTIVE = re.compile(r"^\s*`(ifdef|ifndef|elsif|else|endif)\b\s*(\w*)")


def strip_verification(text: str) -> str:
    """Drop the code under `ifdef FORMAL (and the other verification macros),
    nesting tracked, keeping their `else branches. Each frame: is this branch dropped?"""
    out, stack = [], []          # stack of [dropped_now, verif_frame]
    for line in text.splitlines(keepends=True):
        m = _DIRECTIVE.match(line)
        if m:
            kind, name = m.groups()
            if kind in ("ifdef", "ifndef"):
                verif = name in _VERIF
                stack.append([verif and kind == "ifdef", verif])
            elif stack and kind in ("else", "elsif"):
                if stack[-1][1]:
                    stack[-1][0] = not stack[-1][0] if kind == "else" else False
            elif stack and kind == "endif":
                stack.pop()
            out.append("\n")
            continue
        out.append("\n" if any(f[0] for f in stack) else line)
    return "".join(out)


def _modules(files: list[Path]) -> tuple[list[str], set[str]]:
    defined, used = [], set()
    for f in files:
        if f.suffix.lower() in (".vh", ".svh"):
            continue
        t = project._strip_comments(f.read_text(errors="replace"))
        t = project._strip_param_overrides(strip_verification(t))
        defined += project._MODULE_RE.findall(t)
        for body in re.split(r"\bmodule\b", t)[1:]:
            for typ, _inst in project._INST_RE.findall(body.split("endmodule")[0]):
                if typ not in project._KEYWORDS and not typ.startswith("end"):
                    used.add(typ)
    return defined, used


def designs_in(root: Path, limit: int = 40) -> list[dict]:
    """One candidate per directory that holds HDL (its subdirectories included)."""
    dirs = sorted({p.parent for p in project.find_hdl(root)
                   if ".git" not in p.parts})
    out = []
    for d in dirs:
        files = project.find_hdl(d)
        defined, used = _modules(files)
        if not defined:
            continue
        tops = [m for m in dict.fromkeys(defined) if m not in used]
        missing = sorted(used - set(defined) - _PRIMS)
        out.append({"path": str(d), "rel": str(d.relative_to(root)) or ".", "files": len(files),
                    "lines": sum(f.read_text(errors="replace").count("\n") for f in files),
                    "top_candidates": tops[:8], "missing_modules": missing[:12],
                    "self_contained": not missing})
    # self-contained first, then the smaller (more focused) directories
    out.sort(key=lambda x: (not x["self_contained"], len(x["top_candidates"]) != 1, x["lines"]))
    return out[:limit]


def fetch(url: str) -> dict:
    m = re.match(r"^https://(github\.com|gitlab\.com|gitee\.com)/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    if not m:
        raise ValueError("只支持 github.com / gitlab.com / gitee.com 的仓库地址")
    dest = r2g_home() / "rtl_sources" / ("%s__%s" % (m.group(2), m.group(3)))
    if not (dest / ".git").is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--no-tags", url, str(dest)],
                           capture_output=True, text=True, timeout=600,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError("克隆失败：%s" % r.stderr[-400:])
        size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
        if size > MAX_REPO_BYTES:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError("仓库太大（%.0f MB > %d MB），已删除" % (size / 1e6, MAX_REPO_BYTES >> 20))
    return {"repo": "%s/%s" % (m.group(2), m.group(3)), "local_path": str(dest),
            "license": _license(dest), "designs": designs_in(dest)}
