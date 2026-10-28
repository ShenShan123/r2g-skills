"""Frontend repairs: the RTL-side fixes that got designs through synthesis.

Recipes validated dual-clean on the nangate45 repair arm
(2026-10-21/22) before it was moved here. Two run when the project is
created, because they are facts about the file set, not guesses:

  header_not_source   a .v/.sv that another file `include`s is a header; compiling
                      it on its own put its loose statements outside any module
                      ("syntax error, unexpected TOK_INITIAL"). It stays on the
                      include path and leaves the source list.
  readmem_absolute    yosys runs in the ORFS flow directory, so a relative
                      $readmem path -- written as a literal or as a parameter's
                      default -- names nothing there. The data file is copied
                      into the project and the literal rewritten to its absolute
                      path. A data file that is not in the upload is reported,
                      never invented.

The supported synthesis hook runs after a matching failure, then synthesis is
re-run once. It is a yosys script sourced from
SYNTH_CANONICALIZE_TCL (signoff-loop/scripts/flow/orfs_hooks/):

  strip_vendor_attr   Intel's altera_attribute carries an SDC statement that
                      OpenSTA's Verilog reader rejects (STA-0171); it means
                      nothing to an ASIC flow.

  sv_frontend        on a source-parser failure, a separate oss-cad-suite
                     Yosys/slang translates SystemVerilog to Verilog. The
                     pinned ORFS synthesizer and physical flow still run.
                     A failed translation never replaces the source list.

The former aldff_to_dffsr recipe is quarantined: original-RTL simulation
counterexamples on C01169 and C03509 invalidate its blanket equivalence claim.
Physical DRC/LVS clearance alone does not establish behavioral equivalence.

Every applied change is recorded in metadata.json `frontend_fixes` and shown
to the user. Source path/header repairs preserve the original contents; any
compiler transformation still needs behavioral review for its actual design.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from .paths import signoff_scripts

DATA_SUFFIXES = (".hex", ".mem", ".bin", ".txt", ".dat", ".coe", ".mif", ".init", ".rom", ".data")

_INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
_READMEM_RE = re.compile(r'\$readmem[hb]\s*\(\s*("([^"]*)"|[A-Za-z_][\w$]*)')
_PARAM_RE = r'\b(?:parameter|localparam)\b[^;=]*?\b%s\s*=\s*"([^"]*)"'


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


_UNIT_RE = re.compile(r"^\s*(?:module|macromodule|package|interface|primitive|program|class|config)\b", re.M)
_LOOSE_RE = re.compile(r"^\s*(?:always(?:_ff|_comb|_latch)?|initial|assign|reg|wire|input|output|inout)\b", re.M)


def loose_snippets(sources: list[Path]) -> set[Path]:
    """Sources with no design unit but with statements only valid inside a module:
    a paste-in fragment, never compilable alone. A file of only `define/`timescale
    lines is NOT one -- other files may rely on its macros."""
    hit = set()
    for f in sources:
        t = _strip_comments(f.read_text(errors="replace"))
        if not _UNIT_RE.search(t) and _LOOSE_RE.search(t):
            hit.add(f)
    return hit


def included_headers(sources: list[Path]) -> set[Path]:
    """Sources that another source `include`s (matched by path suffix)."""
    names = set()
    for f in sources:
        names.update(_INCLUDE_RE.findall(_strip_comments(f.read_text(errors="replace"))))
    hit = set()
    for f in sources:
        for n in names:
            parts = Path(n).parts
            if f.parts[-len(parts):] == parts:
                hit.add(f)
    return hit


def _resolve_data(ref: str, consumer: Path, root: Path) -> tuple[Path | None, str]:
    """The upload's file a $readmem reference names: the consumer's directory and
    its ancestors up to the upload root first (how the author ran it), then a
    unique basename anywhere in the upload."""
    raw, root = Path(ref), root.resolve()
    if raw.is_absolute():
        return (raw, "absolute") if raw.is_file() else (None, "")
    d = consumer.resolve().parent
    while True:
        c = d / raw
        if c.is_file():
            return c.resolve(), "relative"
        if d == root or root not in d.parents:
            break
        d = d.parent
    same = [p for p in root.rglob(raw.name) if p.is_file()]
    if len(same) == 1:
        return same[0].resolve(), "unique_basename"
    return None, ("ambiguous" if same else "")


def readmem_references(text: str) -> list[tuple[str, str]]:
    """(literal, how) for every path $readmem reads: a literal argument, or the
    string default of the parameter named as the argument."""
    clean = _strip_comments(text)
    out = []
    for m in _READMEM_RE.finditer(clean):
        if m.group(2) is not None:
            out.append((m.group(2), "literal"))
            continue
        p = re.search(_PARAM_RE % re.escape(m.group(1)), clean)
        if p:
            out.append((p.group(1), "parameter %s" % m.group(1)))
    return out


def fix_sources(copied: list[Path], src_root: Path, rtl_root: Path,
                only: set[Path] | None = None) -> tuple[list[Path], list[dict], list[dict]]:
    """Apply the two create-time recipes to the project's copies.

    `copied` are the project copies, laid out under `rtl_root` as the originals
    are under `src_root`; `only`, when given, is the manifest's source list (the
    recipes still apply to it -- a listed file another one includes is still a
    header). Returns (sources to compile, fixes, missing data files)."""
    # a manifest may list a .vh/.svh as a source: compiled as listed (its `defines
    # can be global), unless another file `include`s it -- then it is a header
    exts = (".v", ".sv") if only is None else (".v", ".sv", ".vh", ".svh")
    sources = [p for p in copied if p.suffix.lower() in exts and (only is None or p in only)]
    fixes, missing = [], []
    inc = included_headers(copied)
    for h in sorted((inc | loose_snippets(sources)) & set(sources)):
        sources.remove(h)
        fixes.append({"recipe": "header_not_source", "file": str(h.relative_to(rtl_root)),
                      "detail": "被其他文件 `include 的头文件，不再单独编译" if h in inc
                      else "没有模块声明的代码片段（只能粘贴进模块里用），不单独编译"})
    for f in sources:
        # Source fixes change only path literals. Preserve non-UTF-8 comment
        # bytes and CRLF instead of decoding with replacement/universal lines.
        text = f.read_bytes().decode("utf-8", errors="surrogateescape")
        orig = src_root / f.relative_to(rtl_root)
        new = text
        for ref, how in dict.fromkeys(readmem_references(text)):
            if not ref or Path(ref).is_absolute() and Path(ref).is_file():
                continue
            found, via = _resolve_data(ref, orig, src_root)
            if not found or src_root.resolve() not in found.parents:
                missing.append({"file": str(f.relative_to(rtl_root)), "reference": ref, "via": how,
                                "reason": "上传的文件里有多个同名数据文件" if via == "ambiguous"
                                else "上传的文件里没有这个数据文件"})
                continue
            dst = rtl_root / found.relative_to(src_root.resolve())
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(found.read_bytes())
            new = new.replace('"%s"' % ref, '"%s"' % dst)
            fixes.append({"recipe": "readmem_absolute", "file": str(f.relative_to(rtl_root)),
                          "detail": "$readmem 路径 %s（%s）→ %s（%s）"
                          % (ref, how, dst.relative_to(rtl_root), {"relative": "按相对路径找到",
                             "unique_basename": "按唯一文件名找到"}.get(via, via))})
        if new != text:
            f.write_bytes(new.encode("utf-8", errors="surrogateescape"))
    return sources, fixes, missing


# -- synthesis-failure recipes ------------------------------------------------

SYNTH_RECIPES = (
    {"id": "strip_vendor_attr", "hook": "strip_vendor_attr.tcl",
     "signature": re.compile(r"STA-0171\].*1_2_yosys\.v line \d+, syntax error"),
     "needs": "altera_attribute",
     "label": "去掉 FPGA 厂商属性 altera_attribute"},
)

QUARANTINED_SYNTH_RECIPES = (
    {"id": "aldff_to_dffsr",
     "signature": re.compile(r"unmapped internal cell of type \\?\$_ALDFFE?_"),
     "reason": "异步加载转换已停用：C01169、C03509 的原始 RTL 仿真存在复位行为反例；"
               "需要证明原始功能等价，不能仅凭 DRC/LVS 通过启用"},
)


def semantic_blockers(log_text: str) -> list[dict]:
    return [{"recipe": r["id"], "reason": r["reason"]}
            for r in QUARANTINED_SYNTH_RECIPES if r["signature"].search(log_text)]

SV_FRONTEND_RECIPE = {
    "id": "sv_frontend",
    "label": "用 Slang 前端转换 SystemVerilog，再交给原 ORFS 综合",
    # Yosys source diagnostics, not STA netlist errors or arbitrary flow errors.
    # A malformed source may also match; slang must actually compile it before
    # anything is activated. Never suppress an error or infer a black box.
    "signature": re.compile(
        r"[^\n]+\.(?:sv|v|svh|vh):\d+: ERROR: (?:"
        r"syntax error, unexpected (?:TOK_[A-Z0-9_]+|OP_CAST|'\[')|"
        r"Failed to detect width for parameter )"),
}


def hook_path(recipe_id: str) -> Path:
    for r in QUARANTINED_SYNTH_RECIPES:
        if r["id"] == recipe_id:
            raise ValueError(r["reason"])
    r = next(r for r in SYNTH_RECIPES if r["id"] == recipe_id)
    return signoff_scripts() / "flow" / "orfs_hooks" / r["hook"]


def match_synth_failure(log_text: str, netlist: Path | None, tried: list[str]) -> list[dict]:
    """Recipes whose signature is in the synthesis log, not tried before.

    `needs` is checked in the netlist yosys wrote, so a STA-0171 on some other
    construct never triggers the attribute strip."""
    hits = []
    for r in SYNTH_RECIPES:
        if r["id"] in tried or not r["signature"].search(log_text):
            continue
        if r.get("needs"):
            try:
                if r["needs"] not in netlist.read_text(errors="replace"):
                    continue
            except (OSError, AttributeError):
                continue
        hits.append(r)
    r = SV_FRONTEND_RECIPE
    if r["id"] not in tried and r["signature"].search(log_text):
        hits.append(r)
    return hits


def _tcl_quote(value: str) -> str:
    if any(c in value for c in "\n\r\0"):
        raise ValueError("newline/NUL in synthesis argument")
    for c in ('\\', '"', '$', '[', ']'):
        value = value.replace(c, '\\' + c)
    return '"' + value + '"'


def prepare_slang(project: Path, meta: dict) -> dict:
    """Prepare a bounded translation attempt; the pipeline owns its subprocess.

    The plugin is loaded by its own Yosys, never by the incompatible ORFS one.
    R2G_SLANG_YOSYS can point to another compatible oss-cad-suite installation.
    """
    override = os.environ.get("R2G_SLANG_YOSYS")
    if not override:
        # Load the installer's custom-prefix pin in fresh CLI/web processes.
        # Keep Slang's Yosys separate from the pinned physical-flow synthesizer.
        resolved = subprocess.run(
            ["bash", "-c", 'source "$1" >/dev/null && printf "%s" "${R2G_SLANG_YOSYS:-}"',
             "r2g-slang-pin", str(signoff_scripts() / "flow/_env.sh")],
            capture_output=True, text=True, timeout=30,
        )
        if resolved.returncode == 0:
            override = resolved.stdout.strip()
    candidates = [Path(override)] if override else [
        Path.home() / "r2g_toolchain/oss-cad-suite/bin/yosys",
        Path("/opt/OpenROAD/oss-cad-suite/bin/yosys"),
        Path("/r2g_toolchain/oss-cad-suite/bin/yosys"),
    ]
    binary = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if binary is None:
        raise FileNotFoundError("缺少配套的 Yosys/Slang；设置 R2G_SLANG_YOSYS 指向其 yosys")
    work = project / "frontend"
    work.mkdir(exist_ok=True)
    output = work / "slang.v"
    # Refuse stale output from a previous/partial attempt.
    output.unlink(missing_ok=True)
    args = ["-D", "SYNTHESIS", "--keep-hierarchy", "--compat=vcs",
            "--ignore-assertions", "--top", meta["top"]]
    for inc in meta["include_dirs"]:
        args += ["-I", inc]
    for define in meta.get("defines", []):
        args += ["-D", define]
    args += meta["files"]
    # Tcl passes real argv words to plugins; a .ys script retains quotes in
    # read_slang's argv, making even quoted switches look like filenames.
    script = work / "slang.tcl"
    script.write_text(
        "yosys plugin -i slang\nyosys read_slang " + " ".join(map(_tcl_quote, args)) + "\n"
        + "yosys hierarchy -check -top " + _tcl_quote(meta["top"]) + "\n"
        + "yosys proc\nyosys opt_clean\nyosys write_verilog -noattr " + _tcl_quote(str(output)) + "\n")
    return {
        "command": [str(binary), "-T", "-c", str(script)],
        "output": str(output), "script": str(script), "tool": str(binary),
        "input_sha256": {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted((project / "rtl").rglob("*")) if p.is_file()},
    }


def write_synth_hook(project: Path, recipe_ids: list[str]) -> Path:
    """One SYNTH_CANONICALIZE_TCL that sources each recipe's script in order."""
    p = project / "constraints" / "frontend_fix.tcl"
    p.write_text("".join("source {%s}\n" % hook_path(i) for i in recipe_ids))
    return p
