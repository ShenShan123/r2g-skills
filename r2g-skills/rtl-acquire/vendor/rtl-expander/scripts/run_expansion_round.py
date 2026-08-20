#!/usr/bin/env python3
"""Safe, artifact-first local RTL corpus expansion round.

The script never executes repository-owned code.  It statically scans source trees,
recovers module DAG roots, optionally invokes a controlled Yosys process, and writes
versioned corpus ledgers and manifests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from recover_license_evidence import recover as recover_license_evidence_v1
from functional_ontology import classify as classify_function

from frontier import canonical_repository_identity, default_frontier_path
from corpus_state import CorpusState, canonical as state_canonical, digest_bytes as state_digest


SCHEMA = "rtl_corpus_record_v2"
PIPELINE_SCHEMA = "rtl_pipeline_v2"
FAMILY_SCHEMA = "rtl_family_v1"
SPLIT_SCHEMA = "rtl_split_v1"
SPLIT_GROUP_SCHEMA = "rtl_split_group_closure_v1"
SPLIT_ASSIGNMENT_SCHEMA = "rtl_split_assignment_v2"
SPLIT_RECONCILIATION_SCHEMA = "rtl_split_reconciliation_v1"
SPLIT_PROFILE_SCHEMA = "rtl_split_profile_v1"
SPLIT_PROFILE_TRANSITION_SCHEMA = "rtl_split_profile_transition_v1"
EQUIV_SCHEMA = "rtl_equiv_v1"
SYNTH_SCHEMA = "rtl_synth_v1"
FRONTEND_SCHEMA = "rtl_frontend_v1"
TOP_RECOVERY_SCHEMA = "rtl_top_recovery_v1"
DOCUMENTATION_SCHEMA = "rtl_documentation_v1"
SEMANTIC_FACTS_SCHEMA = "rtl_semantic_facts_v1"
REPAIR_SCHEMA = "rtl_repair_v1"
EQ_SCHEMA = "rtl_eq_v1"
TV_SCHEMA = "rtl_tv_v1"
FC_SCHEMA = "rtl_fc_v1"
RTL_SUFFIXES = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}
PARSE_SUFFIXES = {".v", ".sv"}
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "build",
    "dist", "out", "output", "outputs", "target", "third_party_tools",
    "prebuilt_tools", "simworkspace", "__pycache__",
}
TB_PARTS = {"tb", "test", "tests", "testbench", "bench", "sim", "simulation", "formal", "verification", "uvm"}
TOP_BAD = re.compile(r"^(?:tb|test(?:bench|bed))|(^|_)(tb|test|sim|formal|verification)($|_)|_tb$", re.I)
UTILITY_TOP = re.compile(r"^(?:inv(?:ert)?|buf(?:fer)?|and|or|xor|xnor|nand|nor|mux|demux|dff|latch)\d*$", re.I)
MODULE_RE = re.compile(r"(?m)^\s*module\s+(?:automatic\s+)?(?:\\([^\s]+)|([A-Za-z_$][\w$]*))\b")
INCLUDE_RE = re.compile(r"`include\s+[\"<]([^\">]+)[\">]")
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
KEYWORDS = {
    "module", "endmodule", "if", "else", "for", "while", "case", "casex", "casez",
    "always", "always_ff", "always_comb", "assign", "wire", "logic", "reg", "input",
    "output", "inout", "generate", "begin", "end", "function", "task", "assert",
    "property", "sequence", "typedef", "struct", "union", "interface", "package",
}
STANDARD_SOURCE_DIRS = {"rtl", "src", "hdl", "verilog", "vhdl", "hardware", "ip", "design"}
DOC_NAMES = {"readme", "docs", "doc", "spec", "specs", "documentation", "registers", "protocol"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".pdf", ".html", ".adoc"}
PORT_RE = re.compile(r"\b(input|output|inout)\b\s*(?:wire|reg|logic|signed|unsigned|\s)*(\[[^\]]+\])?\s*([A-Za-z_$][\w$]*)", re.I)
PARAM_RE = re.compile(r"\b(?:parameter|localparam)\b(?:\s+\w+)*\s+([A-Za-z_$][\w$]*)", re.I)
VHDL_ENTITY_RE = re.compile(r"(?im)^\s*entity\s+([A-Za-z][\w]*)\s+is\b")
VHDL_ARCH_RE = re.compile(r"(?is)architecture\s+([A-Za-z][\w]*)\s+of\s+([A-Za-z][\w]*)\s+is(.*?)end\s+(?:architecture\s+)?\1\s*;")
TESTBENCH_BODY_RE = re.compile(r"\$(?:finish|stop|monitor|display|dumpfile|dumpvars)\b|\bforever\s*#|#\s*\d+", re.I)
VERILOG_INSTANCE_RE = re.compile(
    r"(?<![\w$])([A-Za-z_$][\w$]*)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    r"[A-Za-z_$][\w$]*(?:\s*\[[^\]]+\])?\s*\("
)
VHDL_ENTITY_INSTANCE_RE = re.compile(r"(?is)\bentity\s+(?:[A-Za-z][\w]*\.)?([A-Za-z][\w]*)\b")
VHDL_COMPONENT_INSTANCE_RE = re.compile(r"(?is):\s*([A-Za-z][\w]*)\s+(?:generic|port)\s+map\b")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def digest(data: bytes, size: int = 64) -> str:
    return hashlib.sha256(data).hexdigest()[:size]


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{digest(chr(0).join(parts).encode(), 20)}"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class FileLock:
    def __init__(self, path: Path, blocking: bool = False):
        self.path = path
        self.blocking = blocking
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(self.handle.fileno(), flags)
        except BlockingIOError as exc:
            self.handle.close()
            raise RuntimeError(f"lock busy: {self.path}") from exc
        return self

    def __exit__(self, *_: Any) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def reconcile_stale_active_states(corpus: Path) -> int:
    """Mark abandoned RUNNING markers without disturbing live lock holders."""
    recovered = 0
    for state_path in sorted((corpus / "state" / "active").glob("*.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") != "RUNNING":
            continue
        run_key = str(state.get("run_key") or state_path.stem)
        try:
            with FileLock(corpus / "locks" / f"repo-{run_key}.lock"):
                cache_exists = (corpus / "state" / "repo_runs" / f"{run_key}.json").exists()
                atomic_write_json(state_path, {
                    **state,
                    "status": "COMPLETE" if cache_exists else "INTERRUPTED_RECOVERABLE",
                    "reconciled_at": utc_now(),
                    "reconcile_reason": "CACHE_COMMITTED" if cache_exists else "WORKER_LOCK_RELEASED_BEFORE_COMMIT",
                })
                recovered += 1
        except RuntimeError:
            # A live worker still owns this exact run key.
            continue
    return recovered


def run_readonly(cmd: list[str], cwd: Path, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=timeout, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git_metadata(repo: Path) -> dict[str, Any]:
    commit = run_readonly(["git", "rev-parse", "HEAD"], repo) or "UNKNOWN"
    branch = run_readonly(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo) or "UNKNOWN"
    remote = run_readonly(["git", "remote", "get-url", "origin"], repo) or "UNKNOWN"
    upstream = run_readonly(["git", "remote", "get-url", "upstream"], repo) or "UNKNOWN"
    root_output = run_readonly(["git", "rev-list", "--max-parents=0", "HEAD"], repo, timeout=30)
    root_commits = sorted(line for line in root_output.splitlines() if re.fullmatch(r"[0-9a-fA-F]{40}", line))
    revision_metadata = repo.resolve().parent / "repository.json"
    if commit == "UNKNOWN" and revision_metadata.is_file():
        try:
            acquired = json.loads(revision_metadata.read_text(encoding="utf-8"))
            commit = acquired.get("commit_sha", commit)
            remote = acquired.get("canonical_url", remote)
            branch = "IMMUTABLE_ARCHIVE"
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return {"commit_sha": commit, "branch": branch, "repository_url": remote, "upstream_url": upstream, "git_root_commits": root_commits}


def immutable_repository_revision(corpus: Path, git: dict[str, Any]) -> dict[str, str] | None:
    """Resolve a published input to its single immutable RepositoryRevision source."""
    repository_url = str(git.get("repository_url") or "")
    commit_sha = str(git.get("commit_sha") or "").lower()
    if repository_url in {"", "UNKNOWN"} or commit_sha in {"", "unknown"}:
        return None
    try:
        repository_key = canonical_repository_identity(repository_url)["repository_key"]
    except ValueError:
        return None
    frontier_path = default_frontier_path(corpus)
    if not frontier_path.is_file():
        return None
    connection = sqlite3.connect(f"file:{frontier_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT rr.repository_revision_key,rr.source_path,r.provider,r.namespace,r.repo_name
               FROM repository_revisions rr JOIN repositories r USING(repository_key)
               WHERE rr.repository_key=? AND lower(rr.commit_sha)=?""",
            (repository_key, commit_sha),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    source_path = Path(row["source_path"])
    try:
        source_path.resolve(strict=True).relative_to((corpus / "repositories").resolve(strict=True))
    except (OSError, ValueError):
        return None
    if not source_path.is_dir():
        return None
    return {key: str(row[key]) for key in row.keys()}


def is_skipped(path: Path, root: Path) -> bool:
    rel_parts = [p.lower() for p in path.relative_to(root).parts[:-1]]
    return any(p in SKIP_DIRS for p in rel_parts)


def source_files(repo: Path, max_files: int) -> list[Path]:
    files: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in RTL_SUFFIXES or is_skipped(path, repo):
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
        except OSError:
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return sorted(files, key=lambda p: str(p.relative_to(repo)))


def discovery_admission_anchor(corpus: Path, immutable_revision: dict[str, str] | None) -> str:
    if not immutable_revision:
        return "UNANCHORED"
    repository_key = str(immutable_revision["repository_revision_key"]).rsplit("@", 1)[0]
    connection = sqlite3.connect(f"file:{default_frontier_path(corpus)}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT metadata_json FROM repositories WHERE repository_key=?", (repository_key,)
        ).fetchone()
    finally:
        connection.close()
    try:
        metadata = json.loads(row[0] or "{}") if row else {}
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    return str((metadata.get("discovery_evidence") or {}).get("admission_anchor") or "UNANCHORED")


def repository_outcome_detail(
    repo: Path, files: list[Path], admission_anchor: str, classification: str,
) -> str:
    """Separate archive-content failures without weakening terminal gates."""
    if not files:
        raw_hdl = any(
            path.is_file() and path.suffix.lower() in RTL_SUFFIXES
            for path in repo.rglob("*")
        )
        if raw_hdl:
            return "HDL_GENERATED_OR_VENDOR_ONLY"
        if admission_anchor not in {"UNANCHORED", "QUERY_ONLY", "ORGANIZATION_ONLY", "GRAPH_ONLY"}:
            return "HDL_METADATA_FALSE_POSITIVE"
        return "NO_HDL_SOURCE"
    if classification == "TESTBENCH_ONLY":
        return "HDL_NON_DESIGN_ONLY"
    return "HDL_PRESENT_CANDIDATE_RECOVERY_PENDING"


def is_probable_netlist(path: Path, repo: Path) -> bool:
    name = path.name.lower()
    parts = {part.lower() for part in path.relative_to(repo).parts[:-1]}
    return bool(
        re.search(r"(?:^|[_\-.])(syn|synth|mapped|netlist|gatelevel|gate_level)(?:[_\-.]|$)", name)
        or parts & {"netlist", "netlists", "mapped", "gatelevel", "gate_level", "synthesis_results"}
    )


def source_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".sv", ".svh"}:
        return "systemverilog"
    if suffix in {".vhd", ".vhdl"}:
        return "vhdl"
    return "verilog"


def license_evidence(repo: Path) -> dict[str, Any]:
    return recover_license_evidence_v1(repo)


def classify_repository(files: list[Path], repo: Path) -> str:
    if not files:
        return "NO_RTL"
    parts = Counter(part.lower() for f in files for part in f.relative_to(repo).parts[:-1])
    rtl_files = [f for f in files if not any(p in TB_PARTS for p in (x.lower() for x in f.relative_to(repo).parts))]
    if not rtl_files:
        return "TESTBENCH_ONLY"
    if parts["fpga"] or any(repo.glob("**/*.xdc")) or any(repo.glob("**/*.qsf")):
        return "FPGA_PROJECT"
    if parts["rtl"] or parts["src"] or parts["hdl"]:
        return "RTL_DESIGN"
    return "MIXED"


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(" ", text)


def project_groups(repo: Path, files: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        rel = path.relative_to(repo)
        if len(rel.parts) == 1 or rel.parts[0].lower() in STANDARD_SOURCE_DIRS:
            key = "__repo__"
        else:
            key = rel.parts[0]
        groups[key].append(path)
    if len(groups) == 2 and "__repo__" in groups and len(groups["__repo__"]) <= 2:
        other = next(key for key in groups if key != "__repo__")
        groups[other].extend(groups.pop("__repo__"))
    return {key: sorted(value, key=lambda p: str(p.relative_to(repo))) for key, value in sorted(groups.items())}


def parse_design_units(files: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], dict[str, list[str]], set[str]]:
    definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    includes: dict[str, list[str]] = defaultdict(list)
    for path in files:
        language = source_language(path)
        raw = read_text(path)
        text = strip_comments(raw)
        if language in {"verilog", "systemverilog"}:
            file_includes = INCLUDE_RE.findall(text)
            matches = list(MODULE_RE.finditer(text))
            for match in matches:
                name = match.group(1) or match.group(2)
                if not name:
                    continue
                end_match = re.search(r"\bendmodule\b", text[match.end():], re.I)
                end = match.end() + end_match.end() if end_match else len(text)
                unit_text = text[match.start():end]
                header_end = unit_text.find(";")
                header = unit_text[:header_end + 1] if header_end >= 0 else unit_text[:500]
                ports = [m.group(3) for m in PORT_RE.finditer(header)]
                if not ports:
                    paren = re.search(r"\((.*?)\)\s*;", header, re.S)
                    ports = [p.strip() for p in paren.group(1).split(",") if p.strip()] if paren else []
                definitions[name].append({
                    "name": name, "path": path, "body": unit_text, "language": language,
                    "ports": ports, "testbench_constructs": bool(TESTBENCH_BODY_RE.search(unit_text)),
                })
                includes[name].extend(file_includes)
        else:
            for match in VHDL_ENTITY_RE.finditer(text):
                name = match.group(1)
                end_match = re.search(rf"(?im)^\s*end\s+(?:entity\s+)?(?:{re.escape(name)}\s*)?;", text[match.end():])
                end = match.end() + end_match.end() if end_match else len(text)
                unit_text = text[match.start():end]
                definitions[name].append({
                    "name": name, "path": path, "body": unit_text, "language": language,
                    "ports": ["vhdl_port"] if re.search(r"(?i)\bport\s*\(", unit_text) else [],
                    "testbench_constructs": bool(re.search(r"(?i)\bassert\b.*\breport\b|\bwait\s+for\b", unit_text)),
                })
    for path in files:
        if source_language(path) != "vhdl":
            continue
        text = strip_comments(read_text(path))
        for architecture in VHDL_ARCH_RE.finditer(text):
            entity_name = architecture.group(2)
            for definition in definitions.get(entity_name, []):
                definition["body"] += "\n" + architecture.group(0)
    duplicates = {name for name, values in definitions.items() if len({v["path"] for v in values}) > 1}
    units = {
        name: values[0]
        for name, values in definitions.items()
        if name not in duplicates or all(TOP_BAD.search(name) or value["testbench_constructs"] for value in values)
    }
    names = set(units)
    edges: dict[str, set[str]] = {name: set() for name in names}
    for parent, info in units.items():
        body = info["body"]
        if info["language"] == "vhdl":
            referenced = {match.group(1) for match in VHDL_ENTITY_INSTANCE_RE.finditer(body)}
            referenced.update(match.group(1) for match in VHDL_COMPONENT_INSTANCE_RE.finditer(body))
            name_by_lower = {name.lower(): name for name in names}
            for value in referenced:
                child = name_by_lower.get(value.lower())
                if child and child != parent:
                    edges[parent].add(child)
        else:
            for match in VERILOG_INSTANCE_RE.finditer(body):
                child = match.group(1)
                if child in names and child != parent and child not in KEYWORDS:
                    edges[parent].add(child)
    return units, edges, includes, duplicates


def explicit_top_hints(repo: Path, project_root: Path, unit_names: set[str]) -> dict[str, str]:
    hints: dict[str, str] = {}
    metadata_patterns = ["*.core", "Bender.yml", "bender.yml", "*.f", "*.flist", "*.ys", "*.tcl", "*.qsf"]
    candidates: list[Path] = []
    for pattern in metadata_patterns:
        candidates.extend(project_root.glob(f"**/{pattern}"))
    for path in candidates[:200]:
        if is_skipped(path, repo) or path.stat().st_size > 2 * 1024 * 1024:
            continue
        text = read_text(path)
        filelist_texts: list[str] = []
        if path.suffix.lower() in {".f", ".flist"}:
            for token in re.findall(r"([^\s\"'\\]+\.(?:v|sv|vhd|vhdl))", text, re.I):
                if TOP_BAD.search(Path(token).stem):
                    continue
                candidate_path = path.parent / token
                if not candidate_path.exists():
                    candidate_path = project_root / token
                if candidate_path.exists():
                    filelist_texts.append(read_text(candidate_path))
        for name in unit_names:
            if re.search(rf"(?im)(top|toplevel|top_module|hierarchy\s+-top)\s*[:= ]+\s*{re.escape(name)}\b", text):
                hints[name] = "STATIC_MANIFEST"
            elif any(re.search(rf"(?im)^\s*module\s+{re.escape(name)}\b|^\s*entity\s+{re.escape(name)}\s+is\b", source_text) for source_text in filelist_texts):
                hints.setdefault(name, "STATIC_FILELIST")
    return hints


def candidate_tops(repo: Path, project_key: str, units: dict[str, dict[str, Any]], edges: dict[str, set[str]], limit: int) -> list[dict[str, Any]]:
    def excluded_harness(name: str) -> bool:
        info = units[name]
        rel_parts = {part.lower() for part in info["path"].relative_to(repo).parts}
        return bool(TOP_BAD.search(name) or rel_parts & TB_PARTS or info["testbench_constructs"] or (not info["ports"] and name not in explicit_names))

    project_root = repo if project_key == "__repo__" else repo / project_key
    hints = explicit_top_hints(repo, project_root, set(units))
    explicit_names = set(hints)
    harnesses = {name for name in units if excluded_harness(name)}
    harness_parents: dict[str, set[str]] = defaultdict(set)
    for harness in harnesses:
        for child in edges.get(harness, set()):
            harness_parents[child].add(harness)
    instantiated = {child for parent, children in edges.items() if parent not in harnesses for child in children}
    roots = set(units) - instantiated

    def score(name: str) -> tuple[int, list[str]]:
        info = units[name]
        rel_parts = {p.lower() for p in info["path"].relative_to(repo).parts}
        evidence: list[str] = []
        value = 0
        if name in hints:
            value += 1000 if hints[name] == "STATIC_MANIFEST" else 250
            evidence.append(hints[name])
        if name in roots:
            value += 100
            evidence.append("INSTANTIATION_DAG_ROOT")
        closure_size = len(dependency_closure(name, edges))
        value += min(200, closure_size * 12)
        if re.search(r"top|core|cpu|soc|processor|controller|engine|accel|mult|div|bridge|router|cache|dma|aes|sha|fft", name, re.I):
            value += 35
            evidence.append("SEMANTIC_TOP_NAME")
        tokenized_project = re.sub(r"[^a-z0-9]", "", project_key.lower())
        tokenized_name = re.sub(r"[^a-z0-9]", "", name.lower())
        if project_key != "__repo__" and tokenized_name and (tokenized_name in tokenized_project or tokenized_project in tokenized_name):
            value += 30
            evidence.append("PROJECT_NAME_MATCH")
        if len(units) == 1:
            value += 30
            evidence.append("SINGLE_UNIT_PROJECT")
        direct_dut = any(len(edges.get(harness, set())) == 1 for harness in harness_parents.get(name, set()))
        harness_composite_child = bool(harness_parents.get(name)) and not direct_dut
        if direct_dut:
            value += 300
            evidence.append("DIRECT_DUT_UNDER_TEST")
        elif harness_composite_child:
            evidence.append("COMPOSITE_HARNESS_CHILD")
        if info["ports"]:
            value += 20
            evidence.append("HAS_IO")
        if TOP_BAD.search(name) or UTILITY_TOP.match(name) or rel_parts & TB_PARTS:
            value -= 2000
            evidence.append("EXCLUDED_NAME_OR_PATH")
        if info["testbench_constructs"]:
            value -= 2000
            evidence.append("TESTBENCH_CONSTRUCTS")
        if not info["ports"] and name not in hints:
            value -= 500
            evidence.append("NO_IO")
        strong_design_evidence = bool(name in hints or direct_dut or (closure_size > 1 and not harness_composite_child) or "SEMANTIC_TOP_NAME" in evidence or "PROJECT_NAME_MATCH" in evidence or "SINGLE_UNIT_PROJECT" in evidence)
        if not strong_design_evidence:
            value -= 500
            evidence.append("MODULE_ONLY_WEAK_TOP")
        return value, evidence

    names = set(hints) | roots
    ranked = []
    for name in names:
        value, evidence = score(name)
        if value > 0:
            ranked.append({"top": name, "score": value, "evidence": evidence, "project_key": project_key})
    ranked.sort(key=lambda item: (-item["score"], item["top"].lower()))
    return ranked[:limit]


def dependency_closure(top: str, edges: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    todo = [top]
    while todo:
        node = todo.pop()
        if node in found:
            continue
        found.add(node)
        todo.extend(sorted(edges.get(node, ())))
    return found


def normalized_hash(paths: list[Path], repo: Path) -> tuple[str, str]:
    exact = hashlib.sha256()
    normalized = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p.relative_to(repo))):
        rel = str(path.relative_to(repo)).encode()
        raw = path.read_bytes()
        exact.update(rel + b"\0" + raw + b"\0")
        text = re.sub(r"\s+", "", strip_comments(raw.decode("utf-8", errors="replace")))
        normalized.update(text.encode() + b"\0")
    return exact.hexdigest(), normalized.hexdigest()


def copy_sources(paths: list[Path], repo: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    for path in paths:
        rel = path.relative_to(repo)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(rel))
    return copied


def source_unit_records(paths: list[Path], repo: Path) -> list[dict[str, str]]:
    return [{"path": str(path.relative_to(repo)), "language": source_language(path), "sha256": digest(path.read_bytes())} for path in paths]


def documentation_candidates(repo: Path) -> list[Path]:
    found: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file() or is_skipped(path, repo):
            continue
        rel = path.relative_to(repo)
        parts = {part.lower() for part in rel.parts[:-1]}
        stem = path.stem.lower()
        if path.suffix.lower() in DOC_SUFFIXES and (parts & DOC_NAMES or stem.startswith("readme") or any(term in stem for term in ["spec", "register", "protocol"])):
            try:
                if path.stat().st_size <= 16 * 1024 * 1024:
                    found.append(path)
            except OSError:
                continue
        if len(found) >= 500:
            break
    return sorted(found, key=lambda p: str(p.relative_to(repo)))


def recover_documentation(repo: Path, corpus: Path, repo_id: str) -> dict[str, Any]:
    destination = corpus / "documentation" / "repositories" / repo_id
    copied: list[str] = []
    for path in documentation_candidates(repo):
        rel = path.relative_to(repo)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or digest(target.read_bytes()) != digest(path.read_bytes()):
            shutil.copy2(path, target)
        copied.append(str(rel))
    return {"root": str(destination), "paths": copied, "document_count": len(copied)}


def extract_semantic_facts(top: str, closure: set[str], units: dict[str, dict[str, Any]], edges: dict[str, set[str]], paths: list[Path]) -> dict[str, Any]:
    text = "\n".join(strip_comments(read_text(path)) for path in paths)
    ports = []
    for direction, width, name in PORT_RE.findall(text):
        ports.append({"name": name, "direction": direction.lower(), "width_expression": width or "scalar"})
    parameter_names = sorted(set(PARAM_RE.findall(text)))
    port_names = {port["name"] for port in ports}
    clocks = sorted(name for name in port_names if re.search(r"(^|_)(clk|clock)($|_)", name, re.I))
    resets = sorted(name for name in port_names if re.search(r"(^|_)(rst|reset|resetn|rstn)($|_)", name, re.I))
    memory_names = sorted(set(re.findall(r"(?im)\b(?:reg|logic)\s*(?:\[[^\]]+\]\s*)?([A-Za-z_$][\w$]*)\s*\[[^\]]+\]", text)))
    interfaces = sorted(set(re.findall(r"(?i)\b(axi(?:4|_lite)?|ahb|apb|wishbone|wb|spi|i2c|i2s|uart|pcie|usb|ethernet|ddr)\b", text)))
    arithmetic_ops = {
        "add": len(re.findall(r"(?<!\+)\+(?!\+)", text)),
        "subtract": len(re.findall(r"(?<!-)-(?!-)", text)),
        "multiply": len(re.findall(r"\*", text)),
        "divide": len(re.findall(r"/", text)),
        "shift": len(re.findall(r"<<|>>", text)),
    }
    facts = {
        "schema": "rtl_semantic_facts_v1",
        "top_module": top,
        "ports": ports,
        "parameters": parameter_names,
        "clocks": clocks,
        "resets": resets,
        "memories": memory_names,
        "interfaces": interfaces,
        "child_modules": sorted(edges.get(top, set())),
        "dependency_modules": sorted(closure),
        "fsm_candidates": len(re.findall(r"(?i)\bcase\s*\(|\btypedef\s+enum\b", text)),
        "arithmetic_ops": arithmetic_ops,
        "module_count": len(closure),
        "hierarchy_edge_count": sum(len(edges.get(name, set())) for name in closure),
        "rtl_loc": sum(read_text(path).count("\n") + 1 for path in paths),
    }
    return facts


def resource_class(facts: dict[str, Any]) -> str:
    loc = int(facts["rtl_loc"])
    modules = int(facts["module_count"])
    if loc < 300 and modules <= 3:
        return "TINY"
    if loc < 2_000 and modules <= 15:
        return "SMALL"
    if loc < 10_000 and modules <= 75:
        return "MEDIUM"
    if loc < 50_000 and modules <= 300:
        return "LARGE"
    return "XLARGE"


def yosys_quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yosys_option_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(" ", "\\ ")


def synthesize_design(top: str, top_language: str, vhdl_entities: list[str], paths: list[Path], include_dirs: list[Path], out_dir: Path, yosys: str, timeout: int) -> dict[str, Any]:
    verilog = [p for p in paths if source_language(p) in {"verilog", "systemverilog"}]
    vhdl = [p for p in paths if source_language(p) == "vhdl"]
    if not verilog and not vhdl:
        return {"status": "SYNTH_FAIL", "generic_pass": False, "reason": "UNSUPPORTED_SOURCE", "runtime_seconds": 0.0}
    out_dir.mkdir(parents=True, exist_ok=True)
    netlist = out_dir / "generic.v"
    stats = out_dir / "stats.json"
    log = out_dir / "yosys.log"
    script = out_dir / "synth.ys"
    commands: list[str] = []
    if vhdl:
        frontend_dir = out_dir / "vhdl_frontend"
        frontend_dir.mkdir(parents=True, exist_ok=True)
        safe_vhdl: list[Path] = []
        for index, path in enumerate(vhdl):
            safe_path = frontend_dir / f"{index:04d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', path.name)}"
            shutil.copy2(path, safe_path)
            safe_vhdl.append(safe_path)
        targets = [top] if top_language == "vhdl" else sorted(set(vhdl_entities))
        for entity in targets:
            commands.append("ghdl --std=08 " + " ".join(str(path) for path in safe_vhdl) + f" -e {entity}")
    if verilog:
        include_flags = " ".join("-I" + yosys_option_path(path) for path in include_dirs)
        commands.append("read_verilog -sv " + include_flags + " " + " ".join(yosys_quote(path) for path in verilog))
    commands.extend([
        f"hierarchy -check -top {top}", "proc", "fsm", "memory", "opt", "check",
        f"stat -json {yosys_quote(stats)}", f"write_verilog -noattr {yosys_quote(netlist)}",
    ])
    atomic_write_text(script, "\n".join(commands) + "\n")
    start = time.monotonic()
    try:
        command = [yosys]
        if vhdl:
            command.extend(["-m", "ghdl"])
        command.extend(["-q", "-l", str(log), "-s", str(script)])
        process = subprocess.Popen(
            command, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
            return {"status": "SYNTH_FAIL", "generic_pass": False, "reason": "TIMEOUT", "runtime_seconds": round(time.monotonic() - start, 3), "tool": yosys, "synth_schema": SYNTH_SCHEMA, "log_path": str(log)}
        runtime = round(time.monotonic() - start, 3)
        if stdout or stderr:
            with log.open("a", encoding="utf-8") as handle:
                handle.write("\n[stdout]\n" + stdout + "\n[stderr]\n" + stderr)
        passed = process.returncode == 0 and netlist.exists() and netlist.stat().st_size > 0
        combined = stdout + "\n" + stderr + "\n" + read_text(log)
        if passed:
            reason = "PASS"
        elif "Re-definition of module" in combined:
            reason = "DUPLICATE_MODULE_DEFINITION"
        elif "not found" in combined and "Module" in combined:
            reason = "UNRESOLVED_CHILD"
        elif "syntax error" in combined.lower():
            reason = "PARSE_FAIL"
        elif top_language == "vhdl" and verilog:
            reason = "MIXED_LANGUAGE_FRONTEND_FAIL"
        else:
            reason = "GENERIC_SYNTH_FAIL"
        return {
            "status": "SYNTH_GENERIC_ONLY" if passed else "SYNTH_FAIL",
            "generic_pass": passed,
            "mapping_pass": False,
            "reason": reason,
            "exit_code": process.returncode,
            "runtime_seconds": runtime,
            "generic_netlist": str(netlist) if passed else None,
            "generic_netlist_hash": digest(netlist.read_bytes()) if passed else None,
            "tool": yosys,
            "synth_schema": SYNTH_SCHEMA,
            "frontend": "mixed_language" if verilog and vhdl else top_language,
            "mixed_language_frontend_schema": "rtl_mixed_frontend_v1" if verilog and vhdl else None,
            "source_languages": sorted({source_language(path) for path in paths}),
            "log_path": str(log),
            "log_tail": combined[-4000:] if not passed else "",
        }
    except OSError as exc:
        return {"status": "SYNTH_FAIL", "generic_pass": False, "reason": "TOOL_UNAVAILABLE", "detail": str(exc), "runtime_seconds": 0.0}


def functional_assets(repo: Path, closure_paths: list[Path]) -> tuple[str, list[str]]:
    found: list[str] = []
    patterns = ["*tb*.v", "*tb*.sv", "*.sva", "*cocotb*", "*formal*", "*golden*", "*reference*"]
    scopes = sorted({path.parent for path in closure_paths}) or [repo]
    for pattern in patterns:
        for scope in scopes:
            for path in scope.glob(f"**/{pattern}"):
                if path.is_file() and not is_skipped(path, repo):
                    found.append(str(path.relative_to(repo)))
                    if len(found) >= 50:
                        break
            if len(found) >= 50:
                break
        if len(found) >= 50:
            break
    for path in closure_paths:
        text = strip_comments(read_text(path))
        if re.search(r"(?im)^\s*module\s+(?:tb\b|[A-Za-z_$][\w$]*(?:_tb|_test)\b)", text):
            found.append(str(path.relative_to(repo)) + "#embedded_testbench")
    return ("F1" if found else "F0"), sorted(set(found))


def load_benchmark_hashes(registry: Path) -> tuple[set[str], bool]:
    hashes: set[str] = set()
    catalog_path = registry / "registry_catalog.json"
    if not catalog_path.is_file():
        return hashes, False
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return hashes, False
    active_names: set[str] | None = None
    profile_ready = False
    profile_id = catalog.get("active_profile")
    if profile_id:
        profile_path = registry / "profiles" / f"{profile_id}.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            active_names = set(profile.get("active_benchmarks", []))
            profile_ready = bool(profile.get("ready"))
        except (OSError, json.JSONDecodeError):
            active_names = set()
    for name, entry in catalog.get("entries", {}).items():
        if active_names is not None and name not in active_names:
            continue
        if entry.get("status") != "ACTIVE" or not entry.get("fingerprints"):
            continue
        benchmark = re.sub(r"[^a-z0-9_-]+", "_", str(entry.get("benchmark", "")).lower())
        path = registry / benchmark / "fingerprints.jsonl"
        if path.is_file():
            text = read_text(path)
            hashes.update(re.findall(r'"(?:raw_hash|normalized_hash|ast_token_fingerprint)"\s*:\s*"([0-9a-fA-F]{64})"', text))
    ready = profile_ready if active_names is not None else bool(catalog.get("ready"))
    return {h.lower() for h in hashes}, ready and bool(hashes)


def canonical_repository_url(url: str) -> str:
    value = url.strip().lower().replace("git@github.com:", "https://github.com/")
    return re.sub(r"\.git$", "", value).rstrip("/")


def repository_organization(url: str, fallback: str) -> str:
    canonical = canonical_repository_url(url)
    match = re.search(r"(?:https?://)?[^/]+/([^/]+)/", canonical)
    return match.group(1) if match else fallback.lower()


def canonical_top_name(name: str) -> str:
    return re.sub(r"(?:_top|_wrapper|_rtl)$", "", name.lower())


def family_signatures(record: dict[str, Any]) -> list[tuple[str, str]]:
    top = canonical_top_name(record["build"]["top_module"])
    dedup = record["dedup"]
    repo_url = canonical_repository_url(record["provenance"]["repository_url"])
    signatures = [("repository_lineage", digest(f"{repo_url}\0{top}".encode()))]
    upstream_url = canonical_repository_url(record["provenance"].get("upstream_url", "UNKNOWN"))
    if upstream_url not in {"", "unknown"}:
        signatures.append(("upstream_project_lineage", digest(f"{upstream_url}\0{top}".encode())))
    for root_commit in record["provenance"].get("git_root_commits", []):
        signatures.append(("fork_lineage", digest(f"{root_commit}\0{top}".encode())))
    if dedup.get("source_hash"):
        signatures.append(("exact_source_similarity", digest(f"{dedup['source_hash']}\0{top}".encode())))
    if dedup.get("normalized_hash"):
        signatures.append(("normalized_rtl_similarity", digest(f"{dedup['normalized_hash']}\0{top}".encode())))
    if dedup.get("generic_netlist_hash"):
        structural = f"{dedup['generic_netlist_hash']}\0{dedup.get('hierarchy_hash', '')}"
        signatures.append(("generic_netlist_similarity", digest(structural.encode())))
    return signatures


def deterministic_split(group_id: str, seed: str, train_percent: int, val_percent: int) -> str:
    bucket = int(digest(f"{SPLIT_SCHEMA}\0{seed}\0{group_id}".encode(), 8), 16) % 100
    if bucket < train_percent:
        return "train"
    if bucket < train_percent + val_percent:
        return "val"
    return "test"


def split_revision_scope(record: dict[str, Any]) -> str:
    provenance = record.get("provenance", {})
    return str(
        record.get("revision_id")
        or provenance.get("repo_id")
        or digest(
            f"{canonical_repository_url(provenance.get('repository_url', 'UNKNOWN'))}\0"
            f"{provenance.get('commit_sha', 'UNKNOWN')}".encode()
        )
    )


def split_source_members(record: dict[str, Any]) -> set[str]:
    members: set[str] = set()
    for unit in record.get("source", {}).get("source_units", []):
        source_hash = str(unit.get("sha256", "")).lower()
        if re.fullmatch(r"[0-9a-f]{64}", source_hash):
            members.add(f"source_closure:{source_hash}")
    return members


def split_project_member(record: dict[str, Any]) -> str:
    project_key = str(record.get("identity", {}).get("project_key", "__repo__"))
    scoped_project = chr(0).join((split_revision_scope(record), project_key))
    return f"project_target:{digest(scoped_project.encode())}"


def split_hierarchy_top_member(record: dict[str, Any], module_name: str) -> str:
    scoped_module = f"{split_revision_scope(record)}\0{canonical_top_name(module_name)}"
    return f"hierarchy_top:{digest(scoped_module.encode())}"


def recorded_artifact_digest(path: Path, *, schema: str, object_id: str) -> str:
    """Resolve an immutable object's admission digest without re-reading its bytes."""
    receipt_candidates = (
        path.with_suffix(".admission.json"),
        path.with_name(f"{path.name}.admission.json"),
    )
    receipt_path = next((candidate for candidate in receipt_candidates if candidate.is_file()), None)
    if receipt_path is None:
        raise ValueError(f"REHASH_REQUIRED: missing admission receipt for {object_id}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    recorded = str(receipt.get("sha256") or "").lower()
    if (
        receipt.get("schema") != schema
        or receipt.get("object_id") != object_id
        or not re.fullmatch(r"[0-9a-f]{64}", recorded)
        or int(receipt.get("size", -1)) < 0
        or receipt.get("rehash_required") is not False
    ):
        raise ValueError(f"invalid admission receipt for {object_id}")
    return recorded


def load_split_reconciliation_plan(corpus: Path, args: argparse.Namespace) -> dict[str, Any] | None:
    path = getattr(args, "split_reconciliation_plan", None)
    if not path:
        return None
    path = Path(path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") not in {SPLIT_RECONCILIATION_SCHEMA, SPLIT_PROFILE_TRANSITION_SCHEMA}:
        raise ValueError("unsupported split reconciliation plan schema")
    allowed_policy = (
        "TRAIN_VAL_COMPONENT_TO_VAL" if plan.get("schema") == SPLIT_RECONCILIATION_SCHEMA
        else "CONSERVATIVE_SPLIT_PROMOTION_V1"
    )
    if plan.get("policy") != allowed_policy:
        raise ValueError("unsupported split reconciliation policy")
    if not plan.get("split_epoch") or not plan.get("round_id"):
        raise ValueError("split reconciliation plan lacks epoch or round identity")
    components = list(plan.get("components", []))
    component_groups: list[set[str]] = []
    for component in components:
        groups = set(map(str, component.get("old_split_groups", [])))
        splits = set(map(str, component.get("old_splits", [])))
        target = str(component.get("target_split", ""))
        component_hash = hashlib.sha256(
            (("\n".join(sorted(groups))) + "\n").encode()
        ).hexdigest()
        authorization = {
            "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
            "canonical_component_members": sorted(groups),
            "input_splits": sorted(splits),
            "target_split": target,
        }
        authorization_hash = hashlib.sha256(
            json.dumps(authorization, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            not groups
            or component.get("authorization_scope") != "FULL_TRANSITIVE_COMPONENT"
            or component.get("canonical_component_hash") != component_hash
            or component.get("component_id") != component_hash
            or component.get("authorized_component_hash") != authorization_hash
            or component.get("component_boundary_identity_edges") != 0
            or component.get("component_member_loss") != 0
            or component.get("component_split_set_exactly_known") is not True
            or sorted(map(str, component.get("canonical_component_members", []))) != sorted(groups)
            or sorted(map(str, component.get("input_splits", []))) != sorted(splits)
        ):
            raise ValueError("split reconciliation component authorization is not canonical")
        component_groups.append(groups)
    overlap_count = sum(
        bool(left & right)
        for index, left in enumerate(component_groups)
        for right in component_groups[index + 1:]
    )
    if (
        not components
        or overlap_count != 0
        or plan.get("reconciliation_plan_components_pairwise_disjoint") is not True
        or plan.get("component_overlap_count") != 0
        or plan.get("component_boundary_identity_edges") != 0
        or plan.get("component_member_loss") != 0
    ):
        raise ValueError("split reconciliation plan components are not disjoint and complete")
    expected_plan_hash = str(plan.get("plan_sha256") or "")
    plan_material = dict(plan)
    plan_material.pop("plan_sha256", None)
    actual_plan_hash = hashlib.sha256(
        json.dumps(plan_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected_plan_hash != actual_plan_hash:
        raise ValueError("split reconciliation plan hash mismatch")
    cohort_path = corpus / "quality/phase2/rounds" / str(plan["round_id"]) / "cohort_lock.json"
    if not cohort_path.is_file():
        raise ValueError("split reconciliation plan lacks its immutable cohort lock")
    cohort_hash = recorded_artifact_digest(
        cohort_path, schema="rtl_immutable_artifact_admission_v1",
        object_id="cohort_lock.json",
    )
    if cohort_hash != plan.get("cohort_lock_sha256"):
        raise ValueError("split reconciliation cohort-lock hash mismatch")
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    if int(cohort.get("acquired_revision_count", -1)) != int(plan.get("cohort_revision_count", -2)):
        raise ValueError("split reconciliation cohort size mismatch")
    if plan.get("schema") == SPLIT_PROFILE_TRANSITION_SCHEMA:
        old_profile = plan.get("old_profile", {})
        new_profile = plan.get("new_profile", {})
        profile_index = load_jsonl(corpus / "manifests/split_profiles.jsonl", "profile_id")
        current_profiles = [row for row in profile_index.values() if row.get("status") == "CURRENT"]
        if len(current_profiles) != 1:
            raise ValueError("split profile transition requires exactly one CURRENT profile")
        current_profile = current_profiles[0]
        old_match = re.fullmatch(r"rtl_split_profile_v(\d+)", str(old_profile.get("profile_id", "")))
        new_match = re.fullmatch(r"rtl_split_profile_v(\d+)", str(new_profile.get("profile_id", "")))
        old_schema_match = re.fullmatch(r"rtl_split_v(\d+)", str(old_profile.get("split_schema", "")))
        new_schema_match = re.fullmatch(r"rtl_split_v(\d+)", str(new_profile.get("split_schema", "")))
        if (
            old_profile.get("profile_id") != current_profile.get("profile_id")
            or old_profile.get("split_schema") != current_profile.get("split_schema")
            or old_profile.get("status_after") != "SUPERSEDED"
            or new_profile.get("status") != "CURRENT"
            or not old_match or not new_match or not old_schema_match or not new_schema_match
            or int(new_match.group(1)) != int(old_match.group(1)) + 1
            or int(new_schema_match.group(1)) != int(old_schema_match.group(1)) + 1
        ):
            raise ValueError("invalid split profile transition identity")
        audit = plan.get("consumption_audit", {})
        audit_path = Path(str(audit.get("path", "")))
        if (
            not audit_path.is_file()
            or recorded_artifact_digest(
                audit_path, schema="rtl_immutable_artifact_admission_v1",
                object_id="split_profile_consumption_audit.json",
            ) != audit.get("sha256")
        ):
            raise ValueError("split profile transition consumption audit is missing or changed")
        audit_record = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit_record.get("status") != "NO_RECORDED_CONSUMPTION":
            raise ValueError("old split profile has recorded downstream consumption")
    return plan


def reconciliation_for_component(
    plan: dict[str, Any] | None, existing_groups: set[str], existing_splits: set[str],
) -> dict[str, Any]:
    transition = bool(plan and plan.get("schema") == SPLIT_PROFILE_TRANSITION_SCHEMA)
    if "test" in existing_splits and not transition:
        raise RuntimeError(
            "frozen split conflict involving test: closure/hierarchy/project component would merge "
            f"groups {sorted(existing_groups)} across splits {sorted(existing_splits)}; "
            "requires a new benchmark/split profile or quarantine"
        )
    allowed_splits = {"train", "val"} if not transition else {"train", "val", "test"}
    if plan is None or not existing_splits.issubset(allowed_splits) or len(existing_splits) < 2:
        raise RuntimeError(
            "frozen split conflict: closure/hierarchy/project component would merge "
            f"groups {sorted(existing_groups)} across splits {sorted(existing_splits)}"
        )
    component = next(
        (
            row for row in plan.get("components", [])
            if set(map(str, row.get("old_split_groups", []))) == existing_groups
        ),
        None,
    )
    if component is None or set(map(str, component.get("old_splits", []))) != existing_splits:
        if transition:
            return {
                "_unauthorized": True,
                "old_split_groups": sorted(existing_groups),
                "old_splits": sorted(existing_splits),
                "target_split": "test" if "test" in existing_splits else "val",
            }
        raise RuntimeError(
            "split reconciliation plan does not authorize this exact component: "
            f"groups={sorted(existing_groups)} splits={sorted(existing_splits)}"
        )
    expected_target = "test" if "test" in existing_splits else "val"
    if component.get("target_split", "val") != expected_target:
        raise RuntimeError("split reconciliation plan does not use conservative split promotion")
    component_hash = hashlib.sha256(
        (("\n".join(sorted(existing_groups))) + "\n").encode()
    ).hexdigest()
    authorization = {
        "authorization_scope": "FULL_TRANSITIVE_COMPONENT",
        "canonical_component_members": sorted(existing_groups),
        "input_splits": sorted(existing_splits),
        "target_split": expected_target,
    }
    authorization_hash = hashlib.sha256(
        json.dumps(authorization, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        component.get("component_id") != component_hash
        or component.get("canonical_component_hash") != component_hash
        or component.get("authorized_component_hash") != authorization_hash
        or component.get("authorization_scope") != "FULL_TRANSITIVE_COMPONENT"
    ):
        raise RuntimeError("split reconciliation authorization does not match full runtime component")
    component.setdefault("target_split", expected_target)
    return component


def write_split_indexes(corpus: Path, state: dict[str, dict[str, dict[str, Any]]]) -> None:
    write_jsonl(
        corpus / "manifests/family_signature_index.jsonl",
        sorted(state["signature_index"].values(), key=lambda row: row["signature"]),
    )
    write_jsonl(
        corpus / "manifests/split_assignments.jsonl",
        sorted(state["split_assignments"].values(), key=lambda row: row["split_group_id"]),
    )
    write_jsonl(
        corpus / "manifests/split_membership_index.jsonl",
        sorted(state["membership_index"].values(), key=lambda row: row["member"]),
    )
    write_jsonl(
        corpus / "manifests/split_reconciliations.jsonl",
        sorted(state["reconciliation_index"].values(), key=lambda row: row["reconciliation_id"]),
    )
    write_jsonl(
        corpus / "manifests/split_profiles.jsonl",
        sorted(state["profile_index"].values(), key=lambda row: row["profile_id"]),
    )


def assign_families_and_splits(
    designs: dict[str, dict[str, Any]], corpus: Path, args: argparse.Namespace,
    *, publish_indexes: bool = True,
) -> dict[str, dict[str, dict[str, Any]]]:
    signature_path = corpus / "manifests" / "family_signature_index.jsonl"
    signature_index = load_jsonl(signature_path, "signature")
    split_path = corpus / "manifests" / "split_assignments.jsonl"
    split_assignments = load_jsonl(split_path, "split_group_id")
    membership_path = corpus / "manifests" / "split_membership_index.jsonl"
    membership_index = load_jsonl(membership_path, "member")
    reconciliation_path = corpus / "manifests" / "split_reconciliations.jsonl"
    reconciliation_index = load_jsonl(reconciliation_path, "reconciliation_id")
    profile_path = corpus / "manifests" / "split_profiles.jsonl"
    profile_index = load_jsonl(profile_path, "profile_id")
    reconciliation_plan = load_split_reconciliation_plan(corpus, args)
    profile_transition = bool(
        reconciliation_plan
        and reconciliation_plan.get("schema") == SPLIT_PROFILE_TRANSITION_SCHEMA
    )
    current_profiles = [row for row in profile_index.values() if row.get("status") == "CURRENT"]
    if len(current_profiles) > 1:
        raise RuntimeError("multiple CURRENT split profiles")
    current_profile = current_profiles[0] if current_profiles else {
        "profile_id": "rtl_split_profile_v1", "split_schema": SPLIT_SCHEMA,
    }
    active_split_schema = str(
        reconciliation_plan["new_profile"]["split_schema"]
        if profile_transition else current_profile["split_schema"]
    )
    active_profile_id = str(
        reconciliation_plan["new_profile"]["profile_id"]
        if profile_transition else current_profile["profile_id"]
    )
    unauthorized_profile_components: list[dict[str, Any]] = []
    for record in sorted(designs.values(), key=lambda row: row["design_id"]):
        signatures = family_signatures(record)
        existing = {signature_index[value]["family_id"] for _, value in signatures if value in signature_index}
        conflict = len(existing) > 1
        if existing:
            family_id = sorted(existing)[0]
        else:
            preferred = signatures[0][1]
            family_id = stable_id("f", FAMILY_SCHEMA, preferred)
        evidence = [kind for kind, _ in signatures]
        if record["dedup"].get("hierarchy_hash"):
            evidence.append("hierarchy_similarity")
        if "exact_source_similarity" in evidence and "generic_netlist_similarity" in evidence:
            confidence = "EXACT"
        elif "generic_netlist_similarity" in evidence or "normalized_rtl_similarity" in evidence:
            confidence = "HIGH"
        elif "repository_lineage" in evidence:
            confidence = "PROBABLE"
        else:
            confidence = "UNRESOLVED"
        for kind, value in signatures:
            signature_index[value] = {"signature": value, "evidence_type": kind, "family_id": family_id, "family_schema": FAMILY_SCHEMA}
        record["family_id"] = family_id
        record["family"] = {
            "family_id": family_id,
            "family_confidence": confidence,
            "family_evidence": evidence,
            "family_schema": FAMILY_SCHEMA,
            "merge_conflict": conflict,
        }
        provenance = record["provenance"]
        record["_split_organization"] = repository_organization(provenance["repository_url"], record["identity"]["repository_name"])
        if conflict:
            record["quality"].setdefault("quality_flags", []).append("FAMILY_MERGE_CONFLICT")
            record["quality"]["training_tier"] = "TRAINING_EXCLUDED"

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    current_hierarchy_tops: set[str] = set()
    for record in designs.values():
        family_member = f"family:{record['family_id']}"
        find(family_member)
        project_member = split_project_member(record)
        union(family_member, project_member)
        for source_member in split_source_members(record):
            union(family_member, source_member)
        hierarchy_top = split_hierarchy_top_member(record, record["build"]["top_module"])
        current_hierarchy_tops.add(hierarchy_top)
        union(family_member, hierarchy_top)
        if args.organization_aware_split:
            union(family_member, f"organization:{record['_split_organization']}")
    known_hierarchy_tops = current_hierarchy_tops | {
        member for member in membership_index if member.startswith("hierarchy_top:")
    }
    for record in designs.values():
        family_member = f"family:{record['family_id']}"
        for dependency in record.get("build", {}).get("dependency_modules", []):
            hierarchy_member = split_hierarchy_top_member(record, str(dependency))
            if hierarchy_member in known_hierarchy_tops:
                union(family_member, hierarchy_member)
    components: dict[str, set[str]] = defaultdict(set)
    for member in list(parent):
        components[find(member)].add(member)
    member_to_group: dict[str, tuple[str, bool]] = {}
    for members in components.values():
        existing_groups = {membership_index[member]["split_group_id"] for member in members if member in membership_index}
        if existing_groups:
            # Incremental identity maintenance may initially contain only a new
            # bridge edge plus the directly touched families.  Expand through
            # every persisted member of the touched historical SplitGroups so
            # a merge never loses old families or leaves stale member pointers.
            members.update(
                member for member, membership in membership_index.items()
                if membership.get("split_group_id") in existing_groups
            )
        family_ids = sorted(member.removeprefix("family:") for member in members if member.startswith("family:"))
        if not family_ids:
            continue
        existing_splits = {
            split_assignments[group_id]["split"]
            for group_id in existing_groups
            if group_id in split_assignments
        }
        reconciliation: dict[str, Any] | None = None
        if len(existing_splits) > 1:
            reconciliation = reconciliation_for_component(
                reconciliation_plan, existing_groups, existing_splits,
            )
            if reconciliation.get("_unauthorized"):
                unauthorized_profile_components.append(reconciliation)
        merge_conflict = False
        if reconciliation is not None:
            split_group_id = stable_id(
                "sg", SPLIT_GROUP_SCHEMA, active_profile_id, str(reconciliation_plan["split_epoch"]),
                *sorted(existing_groups),
            )
        elif existing_groups:
            split_group_id = sorted(existing_groups)[0]
        else:
            split_group_id = stable_id("sg", SPLIT_SCHEMA, SPLIT_GROUP_SCHEMA, min(family_ids))
        assignment = split_assignments.get(split_group_id)
        if assignment is None:
            assignment = {
                "split_group_id": split_group_id,
                "split": str(reconciliation["target_split"]) if reconciliation is not None else deterministic_split(split_group_id, args.split_seed, args.train_percent, args.val_percent),
                "split_schema": active_split_schema,
                "split_profile_id": active_profile_id,
                "split_assignment_schema": SPLIT_ASSIGNMENT_SCHEMA if reconciliation is not None else "rtl_split_assignment_v1",
                "split_epoch": str(reconciliation_plan["split_epoch"]) if reconciliation is not None else "initial_frozen_v1",
                "split_seed": args.split_seed,
                "assignment_frozen_at": utc_now(),
                "group_kind": "organization_closure_hierarchy_project_component" if args.organization_aware_split else "closure_hierarchy_project_component",
                "split_group_schema": SPLIT_GROUP_SCHEMA,
                "family_ids": family_ids,
                "group_members": [f"family:{family_id}" for family_id in family_ids],
                "grouping_evidence": sorted({member.split(":", 1)[0] for member in members if not member.startswith("family:")}),
            }
            split_assignments[split_group_id] = assignment
        else:
            assignment["split_group_schema"] = SPLIT_GROUP_SCHEMA
            if not assignment.get("superseded_by"):
                assignment["split_schema"] = active_split_schema
                assignment["split_profile_id"] = active_profile_id
            assignment["group_kind"] = "organization_closure_hierarchy_project_component" if args.organization_aware_split else "closure_hierarchy_project_component"
            assignment["family_ids"] = sorted(set(assignment.get("family_ids", [])) | set(family_ids))
            assignment["group_members"] = [f"family:{family_id}" for family_id in assignment["family_ids"]]
            assignment["grouping_evidence"] = sorted(
                set(assignment.get("grouping_evidence", []))
                | {member.split(":", 1)[0] for member in members if not member.startswith("family:")}
            )
        merged_from = sorted(existing_groups - {split_group_id})
        if merged_from:
            assignment["merged_from"] = sorted(set(assignment.get("merged_from", [])) | set(merged_from))
            for old_group in merged_from:
                if old_group in split_assignments:
                    split_assignments[old_group]["superseded_by"] = split_group_id
        if reconciliation is not None:
            evidence_map = {
                "source_closure": "shared_source",
                "hierarchy_top": "hierarchy",
                "project_target": "project_closure",
                "organization": "organization_closure",
            }
            evidence_types = sorted({
                evidence_map.get(member.split(":", 1)[0], member.split(":", 1)[0])
                for member in members if not member.startswith("family:")
            })
            closure_evidence = [
                {
                    "member": member,
                    "evidence_type": evidence_map.get(
                        member.split(":", 1)[0], member.split(":", 1)[0]
                    ),
                }
                for member in sorted(members)
                if not member.startswith("family:")
            ]
            affected_family_ids = sorted(family_ids)
            affected_design_ids = sorted(
                record["design_id"] for record in designs.values()
                if record["family_id"] in set(affected_family_ids)
            )
            historical_ancestor_groups: set[str] = set(existing_groups)
            pending_ancestors = list(existing_groups)
            while pending_ancestors:
                target = pending_ancestors.pop()
                for candidate_id, candidate in split_assignments.items():
                    if (
                        str(candidate.get("superseded_by", "")) == target
                        and candidate_id not in historical_ancestor_groups
                    ):
                        historical_ancestor_groups.add(candidate_id)
                        pending_ancestors.append(candidate_id)
            reconciliation_id = stable_id(
                "sr", str(reconciliation_plan["schema"]), active_profile_id,
                str(reconciliation_plan["split_epoch"]),
                *sorted(existing_groups),
            )
            lineage = {
                "reconciliation_id": reconciliation_id,
                "reason": "NEW_CLOSURE_EVIDENCE",
                "round_id": reconciliation_plan["round_id"],
                "split_epoch": reconciliation_plan["split_epoch"],
            }
            for old_group in sorted(existing_groups):
                split_assignments[old_group]["superseded_by"] = split_group_id
                split_assignments[old_group]["supersession_lineage"] = lineage
            assignment.update({
                "split": reconciliation["target_split"],
                "split_schema": active_split_schema,
                "split_profile_id": active_profile_id,
                "split_assignment_schema": SPLIT_ASSIGNMENT_SCHEMA,
                "split_epoch": reconciliation_plan["split_epoch"],
                "merged_from": sorted(existing_groups),
                "reconciliation_id": reconciliation_id,
            })
            reconciliation_index[reconciliation_id] = {
                "schema": SPLIT_RECONCILIATION_SCHEMA,
                "reconciliation_id": reconciliation_id,
                "reason": "NEW_CLOSURE_EVIDENCE",
                "old_split_groups": sorted(existing_groups),
                "historical_ancestor_split_groups": sorted(historical_ancestor_groups),
                "old_splits": sorted(existing_splits),
                "old_split_assignments": [
                    {
                        "split_group_id": group_id,
                        "split": split_assignments[group_id]["split"],
                    }
                    for group_id in sorted(existing_groups)
                ],
                "new_split_group": split_group_id,
                "new_canonical_split_group_id": split_group_id,
                "new_split": reconciliation["target_split"],
                "split_profile_id": active_profile_id,
                "profile_transition_schema": reconciliation_plan.get("schema") if profile_transition else None,
                "evidence_types": evidence_types,
                "closure_evidence": closure_evidence,
                "affected_family_ids": affected_family_ids,
                "affected_design_ids": affected_design_ids,
                "superseded_by_lineage": {
                    group_id: split_group_id for group_id in sorted(existing_groups)
                },
                "round_id": reconciliation_plan["round_id"],
                "split_epoch": reconciliation_plan["split_epoch"],
                "cohort_lock_sha256": reconciliation_plan["cohort_lock_sha256"],
                "reconciled_at": utc_now(),
            }
        for member in members:
            membership_index[member] = {
                "member": member, "split_group_id": split_group_id,
                "split_schema": active_split_schema, "split_profile_id": active_profile_id,
            }
            member_to_group[member] = (split_group_id, merge_conflict)
    for record in designs.values():
        split_group_id, merge_conflict = member_to_group[f"family:{record['family_id']}"]
        assignment = split_assignments[split_group_id]
        record["split"] = assignment["split"]
        record["split_group_id"] = split_group_id
        record["split_schema"] = active_split_schema
        record["split_profile_id"] = active_profile_id
        record["split_group_schema"] = SPLIT_GROUP_SCHEMA
        record["split_assignment_schema"] = assignment.get("split_assignment_schema", "rtl_split_assignment_v1")
        record["split_epoch"] = assignment.get("split_epoch", "initial_frozen_v1")
        record["split_group_evidence"] = assignment.get("grouping_evidence", [])
        record.pop("_split_organization", None)
        if merge_conflict:
            record["quality"].setdefault("quality_flags", []).append("SPLIT_GROUP_MERGE_CONFLICT")
            record["quality"]["training_tier"] = "TRAINING_EXCLUDED"
    # Older closure merges predate versioned reconciliation lineage.  Their
    # target assignment already records ``merged_from``, so the missing edge
    # metadata is deterministic and can be backfilled without changing either
    # membership or the frozen split.
    for old_group_id, old_assignment in split_assignments.items():
        target_group_id = old_assignment.get("superseded_by")
        if not target_group_id or old_assignment.get("supersession_lineage"):
            continue
        target_assignment = split_assignments.get(str(target_group_id), {})
        if old_group_id not in set(map(str, target_assignment.get("merged_from", []))):
            continue
        old_assignment["supersession_lineage"] = {
            "schema": "rtl_split_group_lineage_v1",
            "reason": "LEGACY_CLOSURE_MERGE_LINEAGE_BACKFILL",
            "old_split_group": old_group_id,
            "new_split_group": str(target_group_id),
            "old_split": old_assignment.get("split"),
            "new_split": target_assignment.get("split"),
            "evidence_types": target_assignment.get("grouping_evidence", []),
            "split_epoch": target_assignment.get("split_epoch", "legacy_frozen_v1"),
        }
    if unauthorized_profile_components:
        raise RuntimeError(
            "split profile transition plan is incomplete; unauthorized_components="
            + json.dumps(unauthorized_profile_components, sort_keys=True)
        )
    if not profile_index:
        profile_index["rtl_split_profile_v1"] = {
            "schema": SPLIT_PROFILE_SCHEMA, "profile_id": "rtl_split_profile_v1",
            "split_schema": SPLIT_SCHEMA, "status": "CURRENT",
            "split_epoch": "initial_frozen_v1",
        }
    if profile_transition:
        old_id = str(reconciliation_plan["old_profile"]["profile_id"])
        old = profile_index.setdefault(old_id, {
            "schema": SPLIT_PROFILE_SCHEMA, "profile_id": old_id,
            "split_schema": SPLIT_SCHEMA,
        })
        old.update({
            "status": "SUPERSEDED",
            "superseded_by": active_profile_id,
            "supersession_reason": reconciliation_plan["reason"],
        })
        profile_index[active_profile_id] = {
            "schema": SPLIT_PROFILE_SCHEMA,
            "profile_id": active_profile_id,
            "split_schema": active_split_schema,
            "split_epoch": reconciliation_plan["split_epoch"],
            "status": "CURRENT",
            "supersedes": old_id,
            "reason": reconciliation_plan["reason"],
            "round_id": reconciliation_plan["round_id"],
            "cohort_lock_sha256": reconciliation_plan["cohort_lock_sha256"],
            "consumption_audit_sha256": reconciliation_plan["consumption_audit"]["sha256"],
        }
        for assignment in split_assignments.values():
            if not assignment.get("superseded_by"):
                assignment["split_schema"] = active_split_schema
                assignment["split_profile_id"] = active_profile_id
        for membership in membership_index.values():
            membership["split_schema"] = active_split_schema
            membership["split_profile_id"] = active_profile_id
    state = {
        "signature_index": signature_index,
        "split_assignments": split_assignments,
        "membership_index": membership_index,
        "reconciliation_index": reconciliation_index,
        "profile_index": profile_index,
    }
    if publish_indexes:
        write_split_indexes(corpus, state)
    return state


def quality_scores(record: dict[str, Any]) -> tuple[int, str, int, str, dict[str, int], dict[str, int]]:
    prov = record["provenance"]
    validation = record["validation"]
    synth = record["synthesis"]
    complete = not record["build"]["unresolved_dependencies"]
    provenance_score = 15 if prov["repository_url"] != "UNKNOWN" and prov["commit_sha"] != "UNKNOWN" else 10
    completeness = 20 if complete else 8
    transform = 15
    rtl_validation = (5 if validation["static_scan"] else 0) + (5 if validation["parse"] else 0) + (5 if validation["elaborate"] else 0)
    synth_score = 20 if synth.get("generic_pass") else 0
    reproducibility = 10 if prov["commit_sha"] != "UNKNOWN" else 5
    eq_components = {"provenance": provenance_score, "design_completeness": completeness, "transformation_integrity": transform, "rtl_validation": rtl_validation, "synthesis_quality": synth_score, "reproducibility": reproducibility}
    eq = min(100, sum(eq_components.values()))
    grade = "A+" if eq >= 95 else "A" if eq >= 90 else "B+" if eq >= 85 else "B" if eq >= 80 else "C" if eq >= 65 else "D" if eq >= 50 else "F"
    facts = record["rtl_semantics"]
    novelty = 12
    rarity_terms = re.compile(r"pcie|noc|ddr|cache|cpu|risc|crypto|aes|sha|fft|dsp|dma", re.I)
    rarity = 17 if rarity_terms.search(record["identity"]["repository_name"] + " " + record["build"]["top_module"]) else 8
    richness = min(20, 4 + facts["module_count"] * 2)
    scale = min(15, 3 + facts["rtl_loc"] // 500)
    docs = 8 if record["provenance"]["documentation_present"] else 2
    verification = 8 if record["verification"]["functional_confidence"] != "F0" else 0
    readability = 5
    tv_components = {"novelty": novelty, "functional_rarity": rarity, "structural_richness": richness, "scale_hierarchy": scale, "documentation_quality": docs, "verification_assets": verification, "source_readability": readability}
    tv = min(100, sum(tv_components.values()))
    audit = record["contamination"]["audit_status"]
    if not validation["parse"]:
        tier = "TRAINING_EXCLUDED"
    elif synth.get("generic_pass") and complete and audit == "PASS" and eq >= 90:
        tier = "TRAINING_GOLD"
    elif synth.get("generic_pass") and eq >= 65:
        tier = "TRAINING_SILVER"
    else:
        tier = "TRAINING_AUXILIARY"
    if record["contamination"]["benchmark_contaminated"]:
        tier = "TRAINING_EXCLUDED"
    return eq, grade, tv, tier, eq_components, tv_components


def load_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            rows[str(row[key])] = row
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    atomic_write_text(path, text)
    payload = text.encode()
    atomic_write_json(path.with_name(path.name + ".admission.json"), {
        "schema": "rtl_materialized_view_admission_v1",
        "object_id": path.name,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "producer": "write_jsonl",
        "recorded_at": utc_now(),
        "rehash_required": False,
    })


def recorded_jsonl_digest(path: Path) -> str:
    receipt_path = path.with_name(path.name + ".admission.json")
    if not receipt_path.is_file():
        return "MISSING_ADMISSION_DIGEST"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema") != "rtl_materialized_view_admission_v1"
        or receipt.get("object_id") != path.name
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or ""))
        or int(receipt.get("size", -1)) < 0
        or receipt.get("rehash_required") is not False
    ):
        return "INVALID_ADMISSION_DIGEST"
    return str(receipt["sha256"])


def write_manifests(corpus: Path, designs: dict[str, dict[str, Any]]) -> None:
    manifests = corpus / "manifests"
    rows = sorted(designs.values(), key=lambda r: r["design_id"])
    provenance_complete_rows = [
        row for row in rows
        if row.get("source", {}).get("repository_revision_key")
        and row.get("provenance", {}).get("repository_url") not in {None, "", "UNKNOWN"}
        and row.get("provenance", {}).get("commit_sha") not in {None, "", "UNKNOWN"}
    ]
    write_jsonl(manifests / "all_designs.jsonl", rows)
    write_jsonl(manifests / "provenance_complete_designs.jsonl", provenance_complete_rows)
    families: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = row["family_id"]
        families.setdefault(family, {
            "family_id": family, "family_schema": FAMILY_SCHEMA,
            "family_confidence": row.get("family", {}).get("family_confidence", "UNRESOLVED"),
            "family_evidence": row.get("family", {}).get("family_evidence", []),
            "design_ids": [], "gold_eligible_design_ids": [], "normalized_hashes": [], "generic_netlist_hashes": [], "splits": [],
        })
        families[family]["design_ids"].append(row["design_id"])
        if row.get("quality", {}).get("training_tier") == "TRAINING_GOLD":
            families[family]["gold_eligible_design_ids"].append(row["design_id"])
        families[family]["normalized_hashes"].append(row["dedup"]["normalized_hash"])
        families[family]["splits"].append(row.get("split"))
        if row["dedup"].get("generic_netlist_hash"):
            families[family]["generic_netlist_hashes"].append(row["dedup"]["generic_netlist_hash"])
    for family in families.values():
        family["splits"] = sorted(set(family["splits"]))
        family["split_invariant_valid"] = len(family["splits"]) == 1
        family["gold_family_eligible"] = bool(family["gold_eligible_design_ids"])
        family["gold_family_definition"] = "CONTAINS_AT_LEAST_ONE_TRAINING_GOLD_DESIGN_INSTANCE"
        family["gold_variant_selection_policy"] = "GOLD_ELIGIBLE_DESIGN_INSTANCES_ONLY"
    write_jsonl(manifests / "families.jsonl", sorted(families.values(), key=lambda r: r["family_id"]))
    write_jsonl(manifests / "training_gold_families.jsonl", [
        {"schema": "rtl_gold_family_view_v1", "family_id": row["family_id"],
         "eligible_design_ids": row["gold_eligible_design_ids"],
         "variant_selection_policy": "GOLD_ELIGIBLE_DESIGN_INSTANCES_ONLY"}
        for row in sorted(families.values(), key=lambda r: r["family_id"]) if row["gold_family_eligible"]
    ])
    for status in ["SYNTH_COMPLETE", "SYNTH_MACRO_PRESERVED", "SYNTH_GENERIC_ONLY"]:
        write_jsonl(manifests / f"{status.lower()}.jsonl", [r for r in rows if r["synthesis"]["status"] == status])
    for tier in ["TRAINING_PREMIUM", "TRAINING_GOLD", "TRAINING_SILVER", "TRAINING_AUXILIARY", "TRAINING_EXCLUDED"]:
        write_jsonl(manifests / f"{tier.lower()}.jsonl", [r for r in rows if r["quality"]["training_tier"] == tier])
    for split in ["train", "val", "test"]:
        write_jsonl(manifests / f"split_{split}.jsonl", [r for r in rows if r.get("split") == split])
    uncontaminated_rows = [
        row for row in provenance_complete_rows
        if not row.get("contamination", {}).get("benchmark_contaminated", False)
    ]
    write_jsonl(manifests / "public_export_allowed.jsonl", [
        r for r in uncontaminated_rows
        if r.get("release", {}).get("release_policy") == "PUBLIC_EXPORT_ALLOWED"
    ])
    write_jsonl(manifests / "internal_training_only.jsonl", [
        r for r in uncontaminated_rows
        if r.get("release", {}).get("release_policy")
        in {"PUBLIC_EXPORT_ALLOWED", "INTERNAL_TRAINING_ONLY"}
    ])
    gold_path = manifests / "training_gold.jsonl"
    gold_meta = {
        "schema": "rtl_gold_manifest_meta_v1",
        "manifest_sha256": recorded_jsonl_digest(gold_path),
        "input_identity": hashlib.sha256(json.dumps({
            "designs": [(r["design_id"], r.get("family_id"), r.get("split"), r.get("release", {}), r.get("quality", {}).get("training_tier"), r.get("contamination", {})) for r in rows],
            "split_assignments_sha256": recorded_jsonl_digest(
                manifests / "split_assignments.jsonl"
            ),
        }, sort_keys=True).encode()).hexdigest(),
        "design_count": sum(r.get("quality", {}).get("training_tier") == "TRAINING_GOLD" for r in provenance_complete_rows),
        "family_count": len({r["family_id"] for r in provenance_complete_rows if r.get("quality", {}).get("training_tier") == "TRAINING_GOLD"}),
        "provenance_complete_only": True,
        "generated_at": utc_now(),
    }
    atomic_write_json(manifests / "training_gold.meta.json", gold_meta)


def validate_publish_invariants(
    corpus: Path, designs: dict[str, dict[str, Any]],
    split_state: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> None:
    violations: list[dict[str, Any]] = []
    profile_index = (
        split_state["profile_index"] if split_state is not None
        else load_jsonl(corpus / "manifests/split_profiles.jsonl", "profile_id")
    )
    current_profiles = [row for row in profile_index.values() if row.get("status") == "CURRENT"]
    active_split_schema = (
        str(current_profiles[0].get("split_schema")) if len(current_profiles) == 1 else None
    )
    active_split_profile_id = (
        str(current_profiles[0].get("profile_id")) if len(current_profiles) == 1 else None
    )
    family_splits: dict[str, set[str]] = defaultdict(set)
    split_group_splits: dict[str, set[str]] = defaultdict(set)
    evidence_groups: dict[str, set[str]] = defaultdict(set)
    hierarchy_top_groups: dict[str, set[str]] = defaultdict(set)
    gold_by_family: dict[str, set[str]] = defaultdict(set)
    for record in designs.values():
        if record.get("quality", {}).get("training_tier") == "TRAINING_GOLD":
            gold_by_family[record["family_id"]].add(record["design_id"])
        family_splits[record["family_id"]].add(record.get("split", "UNASSIGNED"))
        split_group_id = record.get("split_group_id", "UNASSIGNED")
        split_group_splits[split_group_id].add(record.get("split", "UNASSIGNED"))
        evidence_members = split_source_members(record) | {split_project_member(record)}
        hierarchy_top = split_hierarchy_top_member(record, record.get("build", {}).get("top_module", ""))
        evidence_members.add(hierarchy_top)
        hierarchy_top_groups[hierarchy_top].add(split_group_id)
        for member in evidence_members:
            evidence_groups[member].add(split_group_id)
        if record.get("schema") == SCHEMA:
            if not record.get("validation", {}).get("elaborate"):
                violations.append({"design_id": record["design_id"], "type": "UNELABORATED_DESIGNINSTANCE"})
            if record.get("family", {}).get("family_schema") != FAMILY_SCHEMA:
                violations.append({"design_id": record["design_id"], "type": "MISSING_FAMILY_SCHEMA"})
            if (
                active_split_schema is None
                or record.get("split_schema") != active_split_schema
                or not re.fullmatch(r"rtl_split_v\d+", active_split_schema)
            ):
                violations.append({"design_id": record["design_id"], "type": "MISSING_SPLIT_SCHEMA"})
            if (
                active_split_profile_id is None
                or record.get("split_profile_id") != active_split_profile_id
                or not re.fullmatch(r"rtl_split_profile_v\d+", active_split_profile_id)
            ):
                violations.append({"design_id": record["design_id"], "type": "MISSING_SPLIT_PROFILE"})
            if record.get("split_group_schema") != SPLIT_GROUP_SCHEMA:
                violations.append({"design_id": record["design_id"], "type": "MISSING_SPLIT_GROUP_SCHEMA"})
            if not record.get("source", {}).get("source_units"):
                violations.append({"design_id": record["design_id"], "type": "MISSING_SOURCE_UNITS"})
            if not record.get("semantic_facts_path"):
                violations.append({"design_id": record["design_id"], "type": "MISSING_SEMANTIC_FACTS"})
            if not record.get("release", {}).get("release_policy"):
                violations.append({"design_id": record["design_id"], "type": "MISSING_RELEASE_POLICY"})
            if record.get("conversion_equivalence", {}).get("equivalence_schema") != EQUIV_SCHEMA:
                violations.append({"design_id": record["design_id"], "type": "MISSING_CONVERSION_EQUIVALENCE_SCHEMA"})
            if record.get("repair", {}).get("equivalence", {}).get("equivalence_schema") != EQUIV_SCHEMA:
                violations.append({"design_id": record["design_id"], "type": "MISSING_REPAIR_EQUIVALENCE_SCHEMA"})
            if not record.get("quality", {}).get("engineering_quality_components"):
                violations.append({"design_id": record["design_id"], "type": "MISSING_ENGINEERING_QUALITY_COMPONENTS"})
            if not record.get("quality", {}).get("training_value_components"):
                violations.append({"design_id": record["design_id"], "type": "MISSING_TRAINING_VALUE_COMPONENTS"})
    for family_id, splits in family_splits.items():
        if len(splits) != 1 or "UNASSIGNED" in splits:
            violations.append({"family_id": family_id, "type": "FAMILY_SPLIT_VIOLATION", "splits": sorted(splits)})
    for split_group_id, splits in split_group_splits.items():
        if len(splits) != 1 or "UNASSIGNED" in splits:
            violations.append({"split_group_id": split_group_id, "type": "SPLIT_GROUP_SPLIT_VIOLATION", "splits": sorted(splits)})
    for member, groups in evidence_groups.items():
        if len(groups) > 1:
            violations.append({"evidence_member": member, "type": "SPLIT_EVIDENCE_GROUP_VIOLATION", "split_group_ids": sorted(groups)})
    for record in designs.values():
        record_group = record.get("split_group_id", "UNASSIGNED")
        for dependency in record.get("build", {}).get("dependency_modules", []):
            hierarchy_member = split_hierarchy_top_member(record, str(dependency))
            ancestor_groups = hierarchy_top_groups.get(hierarchy_member, set())
            if ancestor_groups and ancestor_groups != {record_group}:
                violations.append({
                    "design_id": record["design_id"], "dependency": dependency,
                    "type": "HIERARCHY_SPLIT_GROUP_VIOLATION",
                    "design_split_group_id": record_group,
                    "ancestor_split_group_ids": sorted(ancestor_groups),
                })
    for family_id, eligible in gold_by_family.items():
        non_gold = sorted(design_id for design_id in eligible if designs[design_id].get("quality", {}).get("training_tier") != "TRAINING_GOLD")
        if non_gold:
            violations.append({"family_id": family_id, "type": "GOLD_FAMILY_NON_GOLD_VARIANT_ELIGIBLE", "design_ids": non_gold})
    split_assignments = (
        split_state["split_assignments"] if split_state is not None
        else load_jsonl(corpus / "manifests/split_assignments.jsonl", "split_group_id")
    )
    membership_index = (
        split_state["membership_index"] if split_state is not None
        else load_jsonl(corpus / "manifests/split_membership_index.jsonl", "member")
    )
    reconciliations = (
        split_state["reconciliation_index"] if split_state is not None
        else load_jsonl(corpus / "manifests/split_reconciliations.jsonl", "reconciliation_id")
    )
    superseded_without_lineage = 0
    member_loss = 0
    multi_split_assignment = 0
    lineage_cycles = 0
    nonunique_terminal_targets = 0
    for group_id, assignment in split_assignments.items():
        if assignment.get("superseded_by") and not assignment.get("supersession_lineage"):
            superseded_without_lineage += 1
        if not assignment.get("superseded_by"):
            continue
        current = group_id
        seen: set[str] = set()
        terminal_targets: set[str] = set()
        while current in split_assignments and split_assignments[current].get("superseded_by"):
            if current in seen:
                lineage_cycles += 1
                break
            seen.add(current)
            current = str(split_assignments[current]["superseded_by"])
        else:
            if current in split_assignments and not split_assignments[current].get("superseded_by"):
                terminal_targets.add(current)
        if len(terminal_targets) != 1:
            nonunique_terminal_targets += 1
    for reconciliation in reconciliations.values():
        # Reconciliation history is append-only.  A canonical group created by
        # an older reconciliation may itself be superseded when later closure
        # evidence joins another component.  Audit the terminal canonical
        # group instead of treating that legitimate versioned lineage as
        # member loss or a multi-split assignment.
        new_group = str(reconciliation.get("new_split_group"))
        lineage_seen: set[str] = set()
        while split_assignments.get(new_group, {}).get("superseded_by"):
            if new_group in lineage_seen:
                member_loss += 1
                break
            lineage_seen.add(new_group)
            new_group = str(split_assignments[new_group]["superseded_by"])
        new_assignment = split_assignments.get(new_group, {})
        old_groups = [split_assignments.get(str(group), {}) for group in reconciliation.get("old_split_groups", [])]
        expected_families = {
            str(family) for old in old_groups for family in old.get("family_ids", [])
        }
        actual_families = set(map(str, new_assignment.get("family_ids", [])))
        expected_members = {
            str(member) for old in old_groups for member in old.get("group_members", [])
        }
        if not expected_families.issubset(actual_families) or any(
            membership_index.get(member, {}).get("split_group_id") != new_group
            for member in expected_members
        ):
            member_loss += 1
        assigned_splits = {
            str(record.get("split")) for record in designs.values()
            if record.get("split_group_id") == new_group
        }
        if (
            new_assignment.get("split") != reconciliation.get("new_split")
            or assigned_splits != {str(reconciliation.get("new_split"))}
        ):
            multi_split_assignment += 1
    reconciliation_invariants = {
        "cross_split_closure_components": sum(
            violation["type"] in {"SPLIT_EVIDENCE_GROUP_VIOLATION", "HIERARCHY_SPLIT_GROUP_VIOLATION"}
            for violation in violations
        ),
        "superseded_split_groups_without_lineage": superseded_without_lineage,
        "merged_component_member_loss": member_loss,
        "merged_component_multi_split_assignment": multi_split_assignment,
        "split_lineage_cycles": lineage_cycles,
        "nonunique_terminal_canonical_targets": nonunique_terminal_targets,
    }
    for name, count in reconciliation_invariants.items():
        if count:
            violations.append({"type": name.upper(), "count": count})
    report = {
        "schema": "rtl_publish_invariants_v1", "valid": not violations,
        "violations": violations, "checked_at": utc_now(),
        "split_reconciliation_invariants": reconciliation_invariants,
    }
    atomic_write_json(corpus / "quality" / "publish_invariants.json", report)
    if violations:
        raise RuntimeError(f"publish invariant failure: {len(violations)} violation(s)")


def summarize(corpus: Path, designs: dict[str, dict[str, Any]], repos: dict[str, dict[str, Any]], run_id: str) -> dict[str, Any]:
    rows = list(designs.values())
    active_split_schemas = sorted({str(row.get("split_schema", "UNASSIGNED")) for row in rows})
    active_split_profiles = sorted({str(row.get("split_profile_id", "rtl_split_profile_v1")) for row in rows})
    split_group_families: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split_group_families[row.get("split_group_id", "UNASSIGNED")].add(row["family_id"])
    summary = {
        "schema": "rtl_corpus_snapshot_v1",
        "run_id": run_id,
        "timestamp": utc_now(),
        "repositories_scanned": len(repos),
        "design_instances": len(rows),
        "unique_design_families": len({r["family_id"] for r in rows}),
        "parse_valid": sum(bool(r["validation"]["parse"]) for r in rows),
        "elaboration_valid": sum(bool(r["validation"]["elaborate"]) for r in rows),
        "generic_synthesis_valid": sum(bool(r["synthesis"].get("generic_pass")) for r in rows),
        "functional_confidence": dict(Counter(r["verification"]["functional_confidence"] for r in rows)),
        "training_tiers": dict(Counter(r["quality"]["training_tier"] for r in rows)),
        "synthesis_classes": dict(Counter(r["synthesis"]["status"] for r in rows)),
        "languages": dict(Counter(lang for r in rows for lang in r["source"]["languages"])),
        "dataset_splits": dict(Counter(r.get("split", "UNASSIGNED") for r in rows)),
        "split_groups": len({r.get("split_group_id") for r in rows}),
        "split_group_schema": SPLIT_GROUP_SCHEMA,
        "multi_family_split_groups": sum(len(families) > 1 for families in split_group_families.values()),
        "max_families_per_split_group": max((len(families) for families in split_group_families.values()), default=0),
        "split_invariant_violations": sum(len({d.get("split") for d in rows if d["family_id"] == family}) > 1 for family in {r["family_id"] for r in rows}),
        "release_policy": dict(Counter(r.get("release", {}).get("release_policy", "UNKNOWN") for r in rows)),
        "benchmark_registry_version": "UNAVAILABLE" if not any(p.is_file() for p in (corpus / "benchmark_registry").rglob("*")) else digest("".join(sorted(str(p) for p in (corpus / "benchmark_registry").rglob("*") if p.is_file())).encode()),
        "split_profile_id": active_split_profiles[0] if len(active_split_profiles) == 1 else "MIXED",
        "schemas": {"pipeline": PIPELINE_SCHEMA, "family": FAMILY_SCHEMA, "split": active_split_schemas[0] if len(active_split_schemas) == 1 else "MIXED", "split_group": SPLIT_GROUP_SCHEMA, "equivalence": EQUIV_SCHEMA, "synthesis": SYNTH_SCHEMA, "engineering": EQ_SCHEMA, "functional": FC_SCHEMA, "training_value": TV_SCHEMA},
    }
    snapshots = corpus / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    atomic_write_json(snapshots / f"{run_id}.json", summary)
    atomic_write_json(snapshots / "latest.json", summary)
    return summary


def yosys_version(yosys: str) -> str:
    try:
        result = subprocess.run(
            [yosys, "-V"], text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=10, check=False,
        )
        return (result.stdout or result.stderr).strip() or "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"


def compute_run_key(repo: Path, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    git = git_metadata(repo)
    config = {
        "repository_url": git["repository_url"], "commit_sha": git["commit_sha"],
        "pipeline_schema": PIPELINE_SCHEMA, "synth_schema": SYNTH_SCHEMA,
        "converter_versions": {
            "frontend": FRONTEND_SCHEMA,
            "top_recovery": TOP_RECOVERY_SCHEMA,
            "documentation": DOCUMENTATION_SCHEMA,
            "semantic_facts": SEMANTIC_FACTS_SCHEMA,
            "family": FAMILY_SCHEMA,
            "split": SPLIT_SCHEMA,
            "split_group": SPLIT_GROUP_SCHEMA,
            "repair": REPAIR_SCHEMA,
        },
        "yosys_version": args.yosys_version, "synthesize": str(args.synthesize),
        "max_tops_per_repo": str(args.max_tops_per_repo), "max_source_files": str(args.max_source_files),
    }
    if args.max_repo_seconds > 0:
        config["max_repo_seconds"] = str(args.max_repo_seconds)
    return digest(json.dumps(config, sort_keys=True).encode()), git


def process_repo_cached(repo: Path, corpus: Path, args: argparse.Namespace, benchmark_hashes: set[str], benchmark_ready: bool) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    run_key, _ = compute_run_key(repo, args)
    cache_path = corpus / "state" / "repo_runs" / f"{run_key}.json"
    lock_path = corpus / "locks" / f"repo-{run_key}.lock"
    state_path = corpus / "state" / "active" / f"{run_key}.json"
    with FileLock(lock_path):
        if cache_path.exists() and not args.include_scanned:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return cached["repository"], cached["designs"], True
        atomic_write_json(state_path, {"run_key": run_key, "repo": str(repo), "pipeline_schema": PIPELINE_SCHEMA, "status": "RUNNING", "started_at": utc_now()})
        try:
            repo_record, designs = process_repo(repo, corpus, args, benchmark_hashes, benchmark_ready)
            repo_record["run_key"] = run_key
            repo_record["cache_status"] = "MISS"
            payload = {"run_key": run_key, "repository": repo_record, "designs": designs, "completed_at": utc_now()}
            atomic_write_json(cache_path, payload)
            atomic_write_json(state_path, {"run_key": run_key, "repo": str(repo), "pipeline_schema": PIPELINE_SCHEMA, "status": "COMPLETE", "completed_at": utc_now()})
            return repo_record, designs, False
        except Exception as exc:
            atomic_write_json(state_path, {"run_key": run_key, "repo": str(repo), "pipeline_schema": PIPELINE_SCHEMA, "status": "FAILED", "failure": type(exc).__name__, "detail": str(exc), "failed_at": utc_now()})
            raise


def process_repo(repo: Path, corpus: Path, args: argparse.Namespace, benchmark_hashes: set[str], benchmark_ready: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamp = utc_now()
    repo_started = time.monotonic()
    git = git_metadata(repo)
    immutable_revision = immutable_repository_revision(corpus, git)
    repository_name = immutable_revision["repo_name"] if immutable_revision else repo.name
    repo_id = stable_id("r", git["repository_url"], git["commit_sha"] if git["commit_sha"] != "UNKNOWN" else str(repo.resolve()))
    files = source_files(repo, args.max_source_files)
    classification = classify_repository(files, repo)
    precision_reporting = bool(getattr(args, "discovery_precision_policy", False))
    admission_anchor = discovery_admission_anchor(corpus, immutable_revision) if precision_reporting else "UNAVAILABLE_LEGACY"
    license_info = license_evidence(repo)
    documentation = recover_documentation(repo, corpus, repo_id)
    repo_record: dict[str, Any] = {
        "repo_id": repo_id,
        "repository_name": repository_name,
        "local_path": str(repo),
        **git,
        **license_info,
        "documentation": documentation,
        "classification": classification,
        "repository_outcome_detail": repository_outcome_detail(repo, files, admission_anchor, classification) if precision_reporting else "LEGACY_UNCLASSIFIED",
        "rtl_file_count": len(files),
        "scan_timestamp": timestamp,
        "pipeline_schema": PIPELINE_SCHEMA,
        "stage_status": {"ACQUIRED": "DONE", "RECOVERED": "PENDING", "FRONTEND_DONE": "PENDING", "VALIDATED": "PENDING", "REPAIRED": "NOT_REQUIRED", "SYNTH_DONE": "PENDING", "DEDUP_DONE": "PENDING", "SCORED": "PENDING", "PUBLISHED": "PENDING"},
        "state": "NO_RTL" if not files else "SCANNED",
    }
    if not files:
        return repo_record, []
    recovery_files = [path for path in files if not is_probable_netlist(path, repo)]
    repo_record["netlist_files_excluded_from_rtl_recovery"] = len(files) - len(recovery_files)
    groups = project_groups(repo, recovery_files)
    group_data: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for project_key, project_files in groups.items():
        units, edges, includes, duplicates = parse_design_units(project_files)
        group_data[project_key] = {"units": units, "edges": edges, "includes": includes, "duplicates": duplicates, "files": project_files}
        candidates.extend(candidate_tops(repo, project_key, units, edges, max(4, args.max_tops_per_repo * 4)))
    candidates.sort(key=lambda item: (-item["score"], item["project_key"].lower(), item["top"].lower()))
    if not candidates:
        repo_record["state"] = "NO_DESIGN"
        if precision_reporting:
            repo_record["repository_outcome_detail"] = (
                "HDL_GENERATED_OR_VENDOR_ONLY" if not recovery_files
                else "HDL_NON_DESIGN_ONLY" if classification == "TESTBENCH_ONLY"
                else "HDL_UNSUPPORTED_OR_UNUSABLE"
            )
        repo_record["stage_status"]["RECOVERED"] = "NO_CANDIDATE"
        return repo_record, []
    designs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempts = 0
    for candidate in candidates:
        if len(designs) >= args.max_tops_per_repo or attempts >= max(8, args.max_tops_per_repo * 6):
            break
        if args.max_repo_seconds > 0 and time.monotonic() - repo_started >= args.max_repo_seconds:
            failures.append({"failure_schema": "rtl_top_failure_v1", "repo_id": repo_id, "repository_name": repo.name, "failure_type": "REPO_BUDGET_EXHAUSTED", "timestamp": timestamp})
            break
        attempts += 1
        top = candidate["top"]
        project_key = candidate["project_key"]
        data = group_data[project_key]
        units: dict[str, dict[str, Any]] = data["units"]
        edges: dict[str, set[str]] = data["edges"]
        includes: dict[str, list[str]] = data["includes"]
        project_files: list[Path] = data["files"]
        closure_modules = dependency_closure(top, edges)
        closure_paths = {units[name]["path"] for name in closure_modules if name in units}
        requested_includes = {inc for name in closure_modules for inc in includes.get(name, [])}
        included_paths: set[Path] = set()
        for include in requested_includes:
            match = next((path for path in project_files if path.name == Path(include).name), None)
            if match:
                closure_paths.add(match)
                included_paths.add(match)
        package_paths = {path for path in project_files if re.search(r"(?im)^\s*package\s+[A-Za-z_$][\w$]*\b", read_text(path))}
        closure_paths.update(package_paths)
        if any(units.get(name, {}).get("language") == "vhdl" for name in closure_modules):
            closure_paths.update(path for path in project_files if source_language(path) == "vhdl")
        ordered_paths = sorted(closure_paths, key=lambda path: (0 if path in package_paths else 1, str(path.relative_to(repo))))
        compile_paths = [path for path in ordered_paths if path not in included_paths]
        include_dirs_set: set[Path] = {repo}
        for path in ordered_paths:
            parent = path.parent
            while parent == repo or repo in parent.parents:
                include_dirs_set.add(parent)
                if parent == repo:
                    break
                parent = parent.parent
        include_dirs = sorted(include_dirs_set, key=str)
        if not ordered_paths:
            continue
        fc, evidence = functional_assets(repo, closure_paths)
        exact_hash, norm_hash = normalized_hash(ordered_paths, repo)
        design_id = stable_id("d", PIPELINE_SCHEMA, repo_id, project_key, top, norm_hash)
        if immutable_revision:
            original_dir = Path(immutable_revision["source_path"])
            copied = [str(path.relative_to(repo)) for path in ordered_paths]
        else:
            original_dir = corpus / "original_rtl" / design_id
            copied = copy_sources(ordered_paths, repo, original_dir)
        unresolved_includes = sorted(include for include in requested_includes if not any(path.name == Path(include).name for path in project_files))
        facts = extract_semantic_facts(top, closure_modules, units, edges, ordered_paths)
        size_class = resource_class(facts)
        timeout_by_class = {"TINY": 30, "SMALL": 60, "MEDIUM": 180, "LARGE": 600, "XLARGE": 1800}
        synth_timeout = args.timeout if args.timeout > 0 else timeout_by_class[size_class]
        if args.max_repo_seconds > 0:
            remaining = max(1, int(args.max_repo_seconds - (time.monotonic() - repo_started)))
            synth_timeout = min(synth_timeout, remaining)
        synth = {"status": "SYNTH_FAIL", "generic_pass": False, "mapping_pass": False, "reason": "NOT_RUN", "runtime_seconds": 0.0}
        if args.synthesize:
            top_language = units[top]["language"]
            vhdl_entities = [name for name in closure_modules if units.get(name, {}).get("language") == "vhdl"]
            synth = synthesize_design(top, top_language, vhdl_entities, compile_paths, include_dirs, corpus / "synthesis" / "generic" / design_id, args.yosys, synth_timeout)
        q1_parse = bool(units) and synth.get("reason") not in {"PARSE_FAIL", "DUPLICATE_MODULE_DEFINITION"}
        if not synth.get("generic_pass"):
            failures.append({
                "failure_schema": "rtl_top_failure_v1", "repo_id": repo_id, "repository_name": repo.name,
                "project_key": project_key, "top_candidate": top, "top_score": candidate["score"],
                "top_evidence": candidate["evidence"], "source_units": source_unit_records(ordered_paths, repo),
                "duplicate_unit_names": sorted(data["duplicates"]), "failure_type": synth.get("reason", "ELABORATION_FAIL"),
                "synthesis": synth, "runtime_seconds": float(synth.get("runtime_seconds", 0.0)), "timestamp": timestamp,
            })
            continue
        generic_hash = synth.get("generic_netlist_hash")
        hierarchy_hash = digest(json.dumps({name: sorted(edges.get(name, set())) for name in sorted(closure_modules)}, sort_keys=True).encode())
        provisional_family_id = stable_id("f", FAMILY_SCHEMA, generic_hash or hierarchy_hash or norm_hash)
        contaminated = norm_hash.lower() in benchmark_hashes or exact_hash.lower() in benchmark_hashes or (generic_hash or "").lower() in benchmark_hashes
        audit_status = "FAIL" if contaminated else "PASS" if benchmark_ready else "NOT_RUN"
        source_units = source_unit_records(ordered_paths, repo)
        source_languages = sorted({unit["language"] for unit in source_units})
        facts_path = corpus / "recovered_designs" / design_id / "semantic_facts.json"
        atomic_write_json(facts_path, facts)
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "design_id": design_id,
            "family_id": provisional_family_id,
            "variant_id": stable_id("v", design_id, "default"),
            "revision_id": stable_id("rev", repo_id, git["commit_sha"]),
            "identity": {"repository_name": repository_name, "project_key": project_key},
            "provenance": {**git, "repo_id": repo_id, "source_provider": "git" if git["repository_url"] != "UNKNOWN" else "local", "license_files": license_info["license_files"], "acquisition_timestamp": timestamp, "documentation_present": documentation["document_count"] > 0},
            "release": {"license_status": license_info["license_status"], "release_policy": license_info["release_policy"], "license_files": license_info["license_files"], "license_evidence": license_info},
            "build": {"top_module": top, "top_evidence": candidate["evidence"], "top_score": candidate["score"], "source_files": copied, "compile_source_files": [str(path.relative_to(repo)) for path in compile_paths], "include_dirs": [str(path.relative_to(repo)) if path != repo else "." for path in include_dirs], "defines": [], "packages": [str(path.relative_to(repo)) for path in package_paths], "parameters": {}, "dependency_modules": sorted(closure_modules), "unresolved_dependencies": unresolved_includes},
            "source": {"source_languages": source_languages, "languages": source_languages, "source_units": source_units, "mixed_language": len(source_languages) > 1, "original_root": str(original_dir), "source_storage": "IMMUTABLE_REPOSITORY_REVISION" if immutable_revision else "LEGACY_PER_DESIGN_COPY", "canonical_elaboration_representation": synth.get("generic_netlist"), "canonical_synthesis_view": synth.get("generic_netlist"), "transformation": "T1_DETERMINISTIC_FRONTEND" if "vhdl" in source_languages else "T0_NATIVE", **({"repository_revision_key": immutable_revision["repository_revision_key"]} if immutable_revision else {})},
            "documentation": documentation,
            "semantic_facts_path": str(facts_path),
            "rtl_semantics": facts,
            "resource": {"class": size_class, "timeout_seconds": synth_timeout},
            "validation": {"static_scan": True, "parse": q1_parse, "elaborate": bool(synth.get("generic_pass")) if args.synthesize else False, "structural_check": bool(synth.get("generic_pass")) if args.synthesize else False, "warnings": unresolved_includes},
            "repair": {"required": False, "level": "R0", "rules": [], "equivalence": {"result": "UNAVAILABLE", "equivalence_schema": EQUIV_SCHEMA, "mode": "NONE", "parameter_assumptions": {}, "blackbox_matching": "EXACT_INTERFACE", "macro_abstraction": "NONE", "reset_assumptions": "NONE"}},
            "conversion_equivalence": {"result": "UNAVAILABLE", "equivalence_schema": EQUIV_SCHEMA, "mode": "SEQUENTIAL" if facts["clocks"] else "COMBINATIONAL", "parameter_assumptions": {}, "blackbox_matching": "EXACT_INTERFACE", "macro_abstraction": "DECLARED_MACROS", "reset_assumptions": "RECORDED_ONLY"},
            "synthesis": synth,
            "verification": {"functional_confidence": fc, "evidence_paths": evidence, "executed": False},
            "dedup": {"source_hash": exact_hash, "normalized_hash": norm_hash, "hierarchy_hash": hierarchy_hash, "generic_netlist_hash": generic_hash, "family_cluster_method": "rtl_family_v1_multi_evidence"},
            "contamination": {"audit_status": audit_status, "benchmark_contaminated": contaminated, "benchmark_name": None},
            "quality": {},
            "created_at": timestamp,
        }
        record["functional_ontology"] = classify_function(record)
        eq, grade, tv, tier, eq_components, tv_components = quality_scores(record)
        record["quality"] = {"engineering_quality": eq, "engineering_grade": grade, "engineering_quality_components": eq_components, "engineering_quality_schema": EQ_SCHEMA, "functional_schema": FC_SCHEMA, "training_value": tv, "training_value_components": tv_components, "training_value_schema": TV_SCHEMA, "training_tier": tier, "quality_flags": (["BENCHMARK_AUDIT_NOT_RUN"] if audit_status == "NOT_RUN" else []) + (["UNRESOLVED_DEPENDENCY"] if unresolved_includes else [])}
        designs.append(record)
    failure_path = corpus / "failures" / "top_candidates" / f"{repo_id}.jsonl"
    write_jsonl(failure_path, failures)
    repo_record["top_candidate_attempts"] = attempts
    repo_record["top_candidates_total"] = len(candidates)
    repo_record["top_candidates_accepted"] = len(designs)
    repo_record["top_candidates_rejected"] = sum(bool(row.get("top_candidate")) for row in failures)
    repo_record["design_instances_emitted"] = len(designs)
    repo_record["top_candidate_failures"] = len(failures)
    repo_record["stage_status"].update({"RECOVERED": "DONE", "FRONTEND_DONE": "DONE", "VALIDATED": "DONE", "SYNTH_DONE": "DONE" if args.synthesize else "NOT_RUN", "DEDUP_DONE": "DONE", "SCORED": "DONE"})
    repo_record["state"] = "SYNTH_VALID" if any(d["synthesis"].get("generic_pass") for d in designs) else "DESIGN_RECOVERED"
    repo_record["design_ids"] = [d["design_id"] for d in designs]
    return repo_record, designs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.home() / "work" / "_downloads")
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--max-repos", type=int, default=25)
    parser.add_argument("--max-tops-per-repo", type=int, default=4)
    parser.add_argument("--max-source-files", type=int, default=2000)
    parser.add_argument("--repo", action="append", default=[], help="Process only a named repository; repeatable")
    parser.add_argument("--exclude-repo", action="append", default=[], help="Exclude a named repository; repeatable")
    parser.add_argument("--include-scanned", action="store_true")
    parser.add_argument("--synthesize", action="store_true")
    parser.add_argument("--yosys", default=shutil.which("yosys") or "/opt/OpenROAD/oss-cad-suite/bin/yosys")
    parser.add_argument("--timeout", type=int, default=0, help="Override resource-class timeout; 0 selects the class default")
    parser.add_argument("--max-repo-seconds", type=int, default=0, help="Optional total repository budget; 0 preserves uncapped pilot behavior")
    parser.add_argument("--split-seed", default="rtl-corpus-v1")
    parser.add_argument("--train-percent", type=int, default=90)
    parser.add_argument("--val-percent", type=int, default=5)
    parser.add_argument("--organization-aware-split", action="store_true")
    parser.add_argument("--split-reconciliation-plan", type=Path)
    parser.add_argument(
        "--stage-only", action="store_true",
        help="Produce only revision-local, run-keyed processing artifacts; never publish global corpus state",
    )
    parser.add_argument(
        "--staging-receipt", type=Path,
        help="Optional atomic receipt for a --stage-only invocation",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--discovery-precision-policy", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.train_percent < 0 or args.val_percent < 0 or args.train_percent + args.val_percent > 100:
        print("invalid split percentages", file=sys.stderr)
        return 2
    args.yosys_version = yosys_version(args.yosys)
    if not args.source_root.is_dir():
        print(f"source root does not exist: {args.source_root}", file=sys.stderr)
        return 2
    corpus = args.corpus_root.resolve()
    corpus.mkdir(parents=True, exist_ok=True)
    for name in ["repositories", "recovered_designs", "original_rtl", "canonical_rtl", "repaired_rtl", "synthesis/generic", "synthesis/mapped", "functional_assets", "documentation", "training_views", "manifests", "benchmark_registry", "quarantine", "rejected", "snapshots", "runs", "failures/top_candidates", "state/repo_runs", "state/active", "locks", "quality"]:
        (corpus / name).mkdir(parents=True, exist_ok=True)
    reconciled_states = reconcile_stale_active_states(corpus)
    repo_ledger_path = corpus / "manifests" / "repositories.jsonl"
    design_ledger_path = corpus / "manifests" / "all_designs.jsonl"
    candidates = [p for p in args.source_root.iterdir() if p.is_dir() and (not args.repo or p.name in set(args.repo)) and p.name not in set(args.exclude_repo)]
    candidates.sort(key=lambda p: p.name.lower())
    if not args.include_scanned:
        published_run_keys = {
            str(row.get("run_key"))
            for row in load_jsonl(repo_ledger_path, "repo_id").values()
            if row.get("run_key")
        }
        candidates = [p for p in candidates if compute_run_key(p, args)[0] not in published_run_keys]
    candidates = candidates[: max(0, args.max_repos)]
    if args.dry_run:
        print(json.dumps({"pipeline_schema": PIPELINE_SCHEMA, "reconciled_stale_states": reconciled_states, "candidate_count": len(candidates), "candidates": [{"path": str(p), "run_key": compute_run_key(p, args)[0]} for p in candidates]}, indent=2))
        return 0
    benchmark_hashes, benchmark_ready = load_benchmark_hashes(corpus / "benchmark_registry")
    run_id = "rtl-corpus-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_rows: list[dict[str, Any]] = []
    processed_repos: dict[str, dict[str, Any]] = {}
    processed_designs: dict[str, dict[str, Any]] = {}
    for repo in candidates:
        print(f"[scan] {repo.name}", flush=True)
        try:
            repo_record, new_designs, cached = process_repo_cached(repo, corpus, args, benchmark_hashes, benchmark_ready)
            processed_repos[repo_record["repo_id"]] = repo_record
            for design in new_designs:
                processed_designs[design["design_id"]] = design
            run_rows.append({"repo_id": repo_record["repo_id"], "repository_name": repo.name, "state": repo_record["state"], "design_count": len(new_designs), "cache": "HIT" if cached else "MISS"})
        except Exception as exc:
            run_rows.append({"repository_name": repo.name, "state": "FAILED", "failure": type(exc).__name__, "detail": str(exc)})
    if args.stage_only:
        staged = []
        for repo_id, repo_record in sorted(processed_repos.items()):
            staged.append({
                "repo_id": repo_id,
                "repository_name": repo_record.get("repository_name"),
                "repository_revision_key": (
                    repo_record.get("repository_revision_key")
                    or next((
                        design.get("source", {}).get("repository_revision_key")
                        for design in processed_designs.values()
                        if design.get("provenance", {}).get("repo_id") == repo_id
                    ), None)
                ),
                "run_key": repo_record.get("run_key"),
                "terminal_state": repo_record.get("state"),
                "design_count": len([
                    design for design in processed_designs.values()
                    if design.get("provenance", {}).get("repo_id") == repo_id
                ]),
                "publication_status": "STAGED_NOT_PUBLISHED",
            })
        receipt = {
            "schema": "rtl_revision_processing_staging_v1",
            "run_id": run_id,
            "pipeline_schema": PIPELINE_SCHEMA,
            "stage_only": True,
            "publication_performed": False,
            "repositories": staged,
            "run_rows": run_rows,
            "completed_at": utc_now(),
        }
        staging_runs = corpus / "runs" / "staging"
        staging_runs.mkdir(parents=True, exist_ok=True)
        receipt_path = args.staging_receipt or staging_runs / f"{run_id}-{os.getpid()}.json"
        atomic_write_json(receipt_path, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if staged and all(row.get("terminal_state") in {
            "NO_RTL", "NO_DESIGN", "SYNTH_VALID", "DESIGN_RECOVERED"
        } for row in staged) else 1
    with FileLock(corpus / "locks" / "manifest.lock", blocking=True):
        corpus_state = CorpusState(corpus)
        if not corpus_state.populated():
            corpus_state.sync_materialized_views()
        repos = {row["repo_id"]: row for row in corpus_state.repository_payloads()}
        designs = {row["design_id"]: row for row in corpus_state.payloads()}
        prior_hashes = {key: state_digest(state_canonical(value)) for key, value in designs.items()}
        superseded_path = corpus / "quarantine" / "superseded_designs.jsonl"
        superseded = load_jsonl(superseded_path, "design_id")
        retired_design_ids: list[str] = []
        for repo_id, repo_record in processed_repos.items():
            for design_id, old in list(designs.items()):
                if old.get("provenance", {}).get("repo_id") == repo_id and design_id not in processed_designs:
                    old = dict(old)
                    old["superseded_at"] = utc_now()
                    old["superseded_reason"] = "PIPELINE_RESCAN"
                    superseded[design_id] = old
                    designs.pop(design_id)
                    retired_design_ids.append(design_id)
            repo_record["stage_status"]["PUBLISHED"] = "DONE"
            repos[repo_id] = repo_record
        designs.update(processed_designs)
        split_state = assign_families_and_splits(
            designs, corpus, args, publish_indexes=False,
        )
        validate_publish_invariants(corpus, designs, split_state)
        affected_designs = [
            row for design_id, row in designs.items()
            if prior_hashes.get(design_id) != state_digest(state_canonical(row))
        ]
        corpus_state.apply_incremental(
            repositories=processed_repos.values(), designs=affected_designs,
            retired_design_ids=retired_design_ids, retirement_reason="PIPELINE_RESCAN",
        )
        write_split_indexes(corpus, split_state)
        write_jsonl(superseded_path, sorted(superseded.values(), key=lambda r: r["design_id"]))
        write_jsonl(repo_ledger_path, sorted(repos.values(), key=lambda r: r["repo_id"]))
        write_manifests(corpus, designs)
        summary = summarize(corpus, designs, repos, run_id)
        corpus_state.close()
    run_arguments = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
    }
    run_manifest = {"run_id": run_id, "started_and_completed_at": utc_now(), "arguments": run_arguments, "repositories": run_rows, "summary": summary}
    run_path = corpus / "runs" / f"{run_id}.json"
    atomic_write_json(run_path, run_manifest)
    atomic_write_json(corpus / "runs" / "latest.json", run_manifest)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
