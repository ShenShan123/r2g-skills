"""Batch mode: one folder of design folders -> one job.

scan(root) lists every immediate subfolder that holds HDL as one candidate
design, with its top module (design.json, else project.infer_top). A subfolder
whose top cannot be decided is listed with the reason and left out of the job
-- never guessed -- so a 2000-design folder is submitted in one call and the
user sees exactly which ones were skipped and why. A root with HDL but no such
subfolders is one design.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import project

MAX_DESIGNS = 5000


def _candidate(d: Path, name: str) -> dict:
    files = project.find_hdl(d)
    c = {"name": name, "path": str(d), "files": len(files), "top": None, "top_source": None, "problem": None}
    meta = d / "design.json"
    try:
        if meta.is_file() and json.loads(meta.read_text()).get("top"):
            c["top"], c["top_source"] = json.loads(meta.read_text())["top"], "design.json"
        else:
            c["top"], c["top_source"] = project.infer_top(files, hint=d.name)
    except (ValueError, OSError) as e:
        c["problem"] = "无法确定顶层模块：%s" % e
    return c


def _root(path) -> Path:
    """A folder named by the user: required, absolute (a relative one would be
    resolved against the server's own working directory), an existing directory."""
    text = str(path or "").strip()
    if not text:
        raise ValueError("请填写服务器上的目录路径")
    p = Path(text).expanduser()
    if not p.is_absolute():
        raise ValueError("请填写绝对路径（以 / 开头）：%s" % text)
    p = p.resolve()
    if not p.exists():
        raise ValueError("目录不存在：%s" % p)
    if not p.is_dir():
        raise ValueError("不是一个目录：%s" % p)
    return p


def inspect(path) -> dict:
    """One folder (or file) as ONE design: its HDL files, the top module r2g
    would pick, and -- when it cannot pick -- the candidates the user chooses from."""
    text = str(path or "").strip()
    p = Path(text).expanduser()
    if not text or not p.is_absolute():
        raise ValueError("请填写绝对路径（以 / 开头）：%s" % text)
    p = p.resolve()
    if not p.exists():
        raise ValueError("路径不存在：%s" % p)
    if not project.probe_hdl(p):
        raise ValueError("没有找到 .v / .sv 文件：%s" % p)
    files = project.find_hdl(p)
    tops, defined = project.module_candidates(files)
    c = _candidate(p, p.name if p.is_dir() else p.stem)
    c.update(candidates=tops, modules=len(defined),
             file_names=[str(f.relative_to(p)) if p.is_dir() else f.name for f in files][:50])
    if not defined:
        c["problem"] = "没有找到任何 module 定义（文件为空或不是 Verilog）"
    return c


def scan(root: Path) -> dict:
    root = _root(root)
    project.probe_hdl(root, max_entries=2_000_000)     # refuse "/" before walking it per subfolder
    subs = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))
    designs = [_candidate(d, d.name) for d in subs if project.find_hdl(d)]
    if not designs and project.find_hdl(root):
        designs = [_candidate(root, root.name)]
    if len(designs) > MAX_DESIGNS:
        raise ValueError("一次最多 %d 个设计，这个目录有 %d 个" % (MAX_DESIGNS, len(designs)))
    ok = [d for d in designs if not d["problem"]]
    return {"root": str(root), "total": len(designs), "ok": len(ok),
            "skipped": len(designs) - len(ok), "designs": designs}


def sources(scanned: dict) -> list[dict]:
    """The submit-ready list: only the designs whose top was decided."""
    return [{"name": d["name"], "path": d["path"], "top": d["top"]}
            for d in scanned["designs"] if not d["problem"]]
