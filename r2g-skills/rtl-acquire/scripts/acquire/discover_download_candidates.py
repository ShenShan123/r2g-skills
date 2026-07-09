#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    data_path,
    default_downloads_root,
    default_out_root,
    out_root_path,
    seed_root_path,
    workspace_path,
)

DEFAULT_DOWNLOADS = default_downloads_root()
DEFAULT_OUT = workspace_path("candidates/downloads_discovered_candidates.csv")
DEFAULT_SCAN_STATE = workspace_path("scan_state/downloads_scan_state.json")
ORFS_INDEX = seed_root_path("index.csv")
EXT_INDEX = out_root_path("index.csv")
EXT_ROOT = default_out_root()
EXCLUDE_CSV = workspace_path("failures/failed_candidates_exclude.csv")
DISCOVERY_SCHEMA_VERSION = "2026-05-07-deps-v2"

RAM_KEYWORDS = (
    "single_port_ram",
    "dual_port_ram",
    "fakeram",
    "sram",
    "hard_mem",
    "blackbox",
)

BAD_TEMPLATE_MARKERS = ("%%",)
TESTBENCH_MARKERS = ("$display", "$monitor", "$dumpfile", "$dumpvars")
SKIP_DIR_PARTS = {
    ".git",
    "__pycache__",
    "sim",
    "simulation",
    "tb",
    "test",
    "tests",
    "testing",
    "bench",
    "benches",
    "example",
    "examples",
    "doc",
    "docs",
    "script",
    "scripts",
    "formal",
    "verification",
}
PREFERRED_DIR_PARTS = {"rtl", "src", "verilog", "hdl", "core", "cores", "design", "designs", "hw"}
GENERATED_DIR_PARTS = {"build", "generated", "gen", "genrtl", "obj_dir", "out"}
BUILD_MARKER_FILES = {".core", "build.sbt", "fusesoc.conf", "CMakeLists.txt", "pyproject.toml"}
LOW_VALUE_NAME_MARKERS = (
    "_tb",
    "tb_",
    "testbench",
    "_test",
    "test_",
    "_bench",
    "bench_",
    "stim",
    "monitor",
    "checker",
    "_helper",
    "_pkg",
    "_defs",
    "tribuf",
)
CONTROL_KEYWORDS = {
    "controller",
    "control",
    "bridge",
    "peripheral",
    "master",
    "slave",
    "uart",
    "spi",
    "i2c",
    "apb",
    "axi",
    "axil",
    "wishbone",
    "wb",
    "dma",
    "cdc",
    "fifo",
    "soc",
    "scope",
}
MODULE_RE = re.compile(r"\bmodule\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)")
INSTANTIATION_RE = re.compile(
    # Covers both scalar instances and instance arrays:
    #   mux #(32) u_mux (...)
    #   mux #(32) u_mux [31:0] (...)
    r"(?ms)^\s*([A-Za-z_][A-Za-z0-9_$]*)\b\s*(?:#\s*\(.*?\)\s*)?"
    r"([A-Za-z_][A-Za-z0-9_$]*)\b\s*(?:\[[^\]]+\]\s*)*\(",
)
VERILOG_KEYWORDS = {
    "if", "else", "case", "for", "while", "always", "assign", "wire", "reg", "input", "output", "inout",
    "generate", "endmodule", "module", "begin", "end", "parameter", "localparam", "function", "task"
}


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def canonical_source_identity(path_text: str) -> str:
    text = str(Path(path_text).resolve())
    markers = [
        "hdl-benchmarks-min/",
        "vtr-verilog-to-routing-min/",
    ]
    normalized = text.replace("\\", "/")
    for marker in markers:
        if marker in normalized:
            return normalized.split(marker, 1)[1]
    return normalized


def infer_repo_dest_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def load_repo_scope(repo_manifest_csv: Path | None) -> set[str]:
    if repo_manifest_csv is None or not repo_manifest_csv.exists():
        return set()
    rows = list(csv.DictReader(repo_manifest_csv.open(newline="", encoding="utf-8")))
    scoped: set[str] = set()
    for row in rows:
        repo_url = (row.get("repo_url") or "").strip()
        dest_name = (row.get("dest_name") or infer_repo_dest_name(repo_url)).strip()
        if dest_name:
            scoped.add(dest_name)
    return scoped


def load_scan_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = payload.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def write_scan_state(path: Path, repos: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": repos}, indent=2, ensure_ascii=False), encoding="utf-8")


def file_sha1(path: Path) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def repo_rtl_file_hashes(downloads_root: Path, repo_paths: list[Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(repo_paths):
        rel = str(path.relative_to(downloads_root))
        hashes[rel] = file_sha1(path)
    return hashes


def aggregate_inventory_signature(file_hashes: dict[str, str]) -> str:
    hasher = hashlib.sha1()
    for rel, digest in sorted(file_hashes.items()):
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def changed_inventory_files(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    changed: list[str] = []
    all_keys = sorted(set(previous) | set(current))
    for rel in all_keys:
        if previous.get(rel) != current.get(rel):
            changed.append(rel)
    return changed


def should_skip_reject(state: dict, signature: str, *, cooldown_days: int = 30) -> bool:
    if state.get("repo_decision") != "reject":
        return False
    reject_at = state.get("reject_at")
    if reject_at:
        try:
            reject_ts = datetime.fromisoformat(reject_at)
        except Exception:
            reject_ts = None
    else:
        reject_ts = None
    if reject_ts:
        delta = datetime.now(timezone.utc).astimezone() - reject_ts
        if delta.days < cooldown_days:
            if signature and signature != state.get("signature"):
                return False
            return True
    return False


def repo_signature(repo_dir: Path) -> str:
    git_dir = repo_dir / ".git"
    if git_dir.exists():
        try:
            head = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=False,
                text=True,
                capture_output=True,
            )
            if head.returncode == 0:
                return f"git:{head.stdout.strip()}"
        except Exception:
            pass
    stat = repo_dir.stat()
    return f"mtime_ns:{stat.st_mtime_ns}"


def git_clean(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == ""


def git_upstream(repo_dir: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_head(repo_dir: Path, rev: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", rev],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_pull_ff(repo_dir: Path) -> tuple[bool, list[str]]:
    upstream = git_upstream(repo_dir)
    if not upstream:
        return False, []
    before = git_head(repo_dir, "HEAD")
    subprocess.run(["git", "-C", str(repo_dir), "fetch"], check=False, text=True)
    result = subprocess.run(["git", "-C", str(repo_dir), "pull", "--ff-only"], check=False, text=True, capture_output=True)
    after = git_head(repo_dir, "HEAD")
    changed_files: list[str] = []
    if before and after and before != after:
        diff = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--name-only", f"{before}..{after}"],
            check=False,
            text=True,
            capture_output=True,
        )
        if diff.returncode == 0:
            changed_files = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    return result.returncode == 0, changed_files


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_existing_names_and_paths() -> tuple[set[str], set[str], set[str]]:
    existing: set[str] = set()
    existing_paths: set[str] = set()
    excluded_paths: set[str] = set()
    # (30pt base-dataset dedup retired 2026-07-09 — the corpus indexes below
    # are the only prior-art surface; netlist signatures dedup at expand time.)
    for path in (ORFS_INDEX, EXT_INDEX):
        if not path.exists():
            continue
        for row in csv.DictReader(path.open()):
            if row.get("status", "success") == "success":
                existing.add(norm_name(row["design"]))
                source_path = (row.get("source_path", "") or "").strip()
                if source_path:
                    existing_paths.add(canonical_source_identity(source_path))
    if EXT_ROOT.exists():
        for ddir in EXT_ROOT.iterdir():
            if not ddir.is_dir() or ddir.name.startswith("_"):
                continue
            existing.add(norm_name(ddir.name))
            meta_path = ddir / "design_meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                continue
            rtl_files = meta.get("rtl_files", [])
            if isinstance(rtl_files, list):
                for source_path in rtl_files:
                    if source_path:
                        existing_paths.add(canonical_source_identity(source_path))
    if EXCLUDE_CSV.exists():
        for row in csv.DictReader(EXCLUDE_CSV.open()):
            source_path = (row.get("source_path", "") or "").strip()
            if source_path:
                excluded_paths.add(canonical_source_identity(source_path))
    return existing, existing_paths, excluded_paths


def choose_priority(rel: Path, *, rtl_text: str, non_empty_line_count: int) -> str:
    text = str(rel)
    lower_text = f"{text.lower()} {rtl_text.lower()}"
    family = rel.parts[0] if rel.parts else ""
    if family in {
        "verilog-ethernet",
        "verilog-axis",
        "verilog-axi",
        "verilog-i2c",
        "verilog-wishbone",
        "wb2axip",
        "qspiflash",
        "core_usb",
        "core_usb_host",
        "usbcorev",
        "ultraembedded-cores",
        "ultraembedded-riscv",
        "picorv32",
        "sha256",
        "aes",
        "sha1",
        "prince",
        "cmac",
        "poly1305",
        "R8051",
        "serv",
        "blake2s",
        "wbuart32",
        "wbuart",
        "sdspi",
        "wbi2c",
    }:
        return "high"
    if "iccad-2015" in text or "iccad-2017" in text:
        return "high"
    if "iscas85" in text or "iscas89" in text:
        return "high"
    if "vtr_flow/benchmarks/verilog" in text or "odin_ii/regression_test/benchmark/verilog" in text:
        return "medium"
    if family in {"openmsp430", "darkriscv", "riscv-simple-sv", "mor1kx", "biriscv", "zipcpu", "riscv-soc-integration", "i2c-eeprom", "i2c-master"}:
        return "medium"
    control_hits = sum(1 for keyword in CONTROL_KEYWORDS if keyword in lower_text)
    if non_empty_line_count >= 400 and control_hits >= 1:
        return "high"
    if non_empty_line_count >= 120 or control_hits >= 2:
        return "medium"
    return "low"


def boost_priority(priority: str) -> str:
    if priority == "low":
        return "medium"
    if priority == "medium":
        return "high"
    return priority


def infer_design_name(downloads_root: Path, path: Path) -> str:
    rel = path.relative_to(downloads_root)
    rel_text = str(rel)
    if rel_text.startswith("hdl-benchmarks-min/iccad-2015/"):
        parts = rel.parts
        unit = parts[2]
        stem = path.stem.replace("in_", "in")
        return f"iccad2015_{unit}_{stem}"
    if rel_text.startswith("hdl-benchmarks-min/iccad-2017/"):
        parts = rel.parts
        unit = parts[2]
        return f"iccad2017_{unit}_{path.stem}"
    if rel_text.startswith("hdl-benchmarks-min/iscas85/"):
        return f"iscas85_{path.stem}"
    if rel_text.startswith("hdl-benchmarks-min/iscas89/"):
        return f"iscas89_{path.stem}"
    if rel_text.startswith("vtr-verilog-to-routing-min/vtr_flow/benchmarks/verilog/"):
        parts = rel.parts
        if "koios" in parts:
            return f"koios_{path.stem}"
        return path.stem
    if rel_text.startswith("vtr-verilog-to-routing-min/odin_ii/regression_test/benchmark/verilog/full/"):
        parts = rel.parts
        if "koios" in parts:
            return f"koios_{path.stem}"
        return path.stem
    parts = list(rel.parts[:-1])
    stem = path.stem
    family = parts[0] if parts else "misc"
    chunks = [family] + [p.replace("-", "_") for p in parts[1:]] + [stem]
    name = "_".join(filter(None, chunks))
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name)
    return name.strip("_")


def path_is_likely_rtl_source(downloads_root: Path, path: Path) -> bool:
    rel = path.relative_to(downloads_root)
    lower_parts = [part.lower() for part in rel.parts[:-1]]
    if any(part in SKIP_DIR_PARTS for part in lower_parts):
        return False
    if rel.parts and rel.parts[0] in {"hdl-benchmarks-min", "vtr-verilog-to-routing-min"}:
        return True
    if len(rel.parts) <= 2:
        return True
    if any(part in GENERATED_DIR_PARTS for part in lower_parts):
        return True
    return any(part in PREFERRED_DIR_PARTS for part in lower_parts)


def repo_build_markers(repo_dir: Path) -> list[str]:
    markers: list[str] = []
    for marker in BUILD_MARKER_FILES:
        if marker.startswith("."):
            if any(repo_dir.rglob(f"*{marker}")):
                markers.append(marker)
        else:
            if any(repo_dir.rglob(marker)):
                markers.append(marker)
    return sorted(set(markers))


def file_is_candidate(path: Path) -> tuple[bool, str]:
    if path.suffix not in {".v", ".sv", ".vhd", ".vhdl"}:
        return False, "non_rtl_suffix"
    text = path.read_text(errors="ignore")
    lower = text.lower()
    stem = path.stem.lower()
    if any(marker in stem for marker in LOW_VALUE_NAME_MARKERS):
        return False, "low_value_name"
    if re.search(r"(^|[^a-z0-9])(tb|test)([^a-z0-9]|$)", stem):
        return False, "testbench_like_name"
    if path.suffix.lower() in {".vhd", ".vhdl"}:
        if "entity" not in lower:
            return False, "no_entity"
    else:
        if not re.search(r"\bmodule\b", lower):
            return False, "no_module"
    if any(marker in text for marker in TESTBENCH_MARKERS):
        return False, "testbench_marker"
    if any(marker in text for marker in BAD_TEMPLATE_MARKERS):
        return False, "template_placeholder"
    if any(keyword in lower for keyword in RAM_KEYWORDS):
        return False, "ram_or_macro_keyword"
    if "`include" in text and text.count("`include") > 8:
        return False, "too_many_includes"
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    if len(non_empty_lines) < 20:
        return False, "too_small"
    return True, ""


MODULE_RE = re.compile(r"\bmodule\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)")


def infer_expected_top(path: Path, rtl_text: str) -> str:
    modules = MODULE_RE.findall(rtl_text)
    if not modules:
        return path.stem
    stem_norm = norm_name(path.stem)
    for module in modules:
        if norm_name(module) == stem_norm:
            return module
    if len(modules) == 1:
        return modules[0]
    return modules[0]


def strip_verilog_comments(rtl_text: str) -> str:
    rtl_text = re.sub(r"/\*.*?\*/", "", rtl_text, flags=re.S)
    rtl_text = re.sub(r"//.*?$", "", rtl_text, flags=re.M)
    return rtl_text


def extract_module_defs(rtl_text: str) -> list[str]:
    return MODULE_RE.findall(strip_verilog_comments(rtl_text))


def duplicate_module_defs(module_defs: list[str]) -> list[str]:
    counts = Counter(module_defs)
    return sorted(name for name, count in counts.items() if count > 1)


def extract_instantiated_modules(rtl_text: str) -> set[str]:
    refs: set[str] = set()
    for match in INSTANTIATION_RE.finditer(strip_verilog_comments(rtl_text)):
        token = match.group(1)
        if token.lower() in VERILOG_KEYWORDS:
            continue
        refs.add(token)
    return refs


def bundle_closure(
    start_path: Path,
    file_infos: dict[Path, dict],
    module_to_path: dict[str, Path],
    *,
    max_files: int = 16,
) -> list[Path]:
    ordered: list[Path] = []
    queue = [start_path]
    seen: set[Path] = set()
    while queue and len(ordered) < max_files:
        path = queue.pop(0)
        if path in seen or path not in file_infos:
            continue
        seen.add(path)
        ordered.append(path)
        info = file_infos[path]
        for ref in sorted(info["local_refs"]):
            dep_path = module_to_path.get(ref)
            if dep_path and dep_path not in seen and dep_path not in queue:
                queue.append(dep_path)
    return ordered


def helper_like_file(info: dict, refcount: Counter[str]) -> bool:
    if info["local_refs"]:
        return False
    exported = info["module_defs"]
    if not exported:
        return False
    referenced_elsewhere = sum(refcount.get(name, 0) for name in exported)
    if referenced_elsewhere == 0:
        return False
    if info["non_empty_line_count"] >= 120:
        return False
    stem = info["path"].stem.lower()
    if any(keyword in stem for keyword in CONTROL_KEYWORDS):
        return False
    return True


def standalone_leaf_low_value(info: dict, refcount: Counter[str]) -> bool:
    if info["local_refs"]:
        return False
    exported = info["module_defs"]
    if not exported:
        return False
    referenced_elsewhere = sum(refcount.get(name, 0) for name in exported)
    if referenced_elsewhere > 0:
        return False
    if info["control_hits"] >= 2:
        return False
    if info["non_empty_line_count"] >= 1200:
        return False
    stem = info["path"].stem.lower()
    if any(token in stem for token in ("top", "soc", "ctrl", "control", "dma", "scope", "master", "slave", "bridge", "peripheral")):
        return False
    return True


def rank_candidate(info: dict) -> tuple[int, int, int, int, str]:
    control_hits = info["control_hits"]
    local_ref_count = len(info["local_refs"])
    line_count = info["non_empty_line_count"]
    topness = 1 if local_ref_count > 0 else 0
    return (-topness, -control_hits, -line_count, -len(info["bundle_paths"]), str(info["rel"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover likely suitable nangate45 graph-expansion candidates from local downloads.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repo-manifest-csv", type=Path, default=None)
    parser.add_argument("--scan-state-json", type=Path, default=DEFAULT_SCAN_STATE)
    parser.add_argument("--force-rescan", action="store_true")
    parser.add_argument("--sync-upstream", action="store_true", default=True, help="If repo is clean, attempt git pull --ff-only before scanning.")
    parser.add_argument("--no-sync-upstream", action="store_false", dest="sync_upstream", help="Disable git pull sync before scanning.")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--max-candidates-per-repo", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    existing, existing_paths, excluded_paths = load_existing_names_and_paths()
    repo_scope = load_repo_scope(args.repo_manifest_csv)
    scan_state = load_scan_state(args.scan_state_json)
    rows: list[dict[str, str]] = []
    skipped_repo_dirs: set[str] = set()
    processed_repo_dirs: set[str] = set()
    scanned_repo_count = 0
    skipped_repo_count = 0
    skipped_rejected_repo_count = 0
    repo_candidate_counts: dict[str, int] = {}

    seen_designs: set[str] = set()
    seen_paths: set[str] = set()
    repo_to_paths: dict[str, list[Path]] = {}
    all_paths = sorted(
        list(args.downloads_root.rglob("*.v"))
        + list(args.downloads_root.rglob("*.sv"))
        + list(args.downloads_root.rglob("*.vhd"))
        + list(args.downloads_root.rglob("*.vhdl"))
    )
    for path in all_paths:
        rel = path.relative_to(args.downloads_root)
        if repo_scope and (not rel.parts or rel.parts[0] not in repo_scope):
            continue
        if not rel.parts:
            continue
        repo_name = rel.parts[0]
        if path_is_likely_rtl_source(args.downloads_root, path):
            repo_to_paths.setdefault(repo_name, []).append(path)

    for repo_name in sorted(repo_to_paths):
        repo_dir = args.downloads_root / repo_name
        state = dict(scan_state.get(repo_name, {}))
        signature = repo_signature(repo_dir)
        if args.sync_upstream and (repo_dir / ".git").exists() and git_clean(repo_dir):
            ok, changed = git_pull_ff(repo_dir)
            state["last_sync_at"] = now_iso()
            state["last_sync_status"] = "ok" if ok else "failed"
            if changed:
                state["last_sync_changed_files"] = changed[:200]
            signature = repo_signature(repo_dir)
        if not args.include_rejected and should_skip_reject(state, signature) and not args.force_rescan:
            skipped_repo_dirs.add(repo_name)
            skipped_rejected_repo_count += 1
            state["skip_reject_count"] = int(state.get("skip_reject_count", 0)) + 1
            scan_state[repo_name] = state
            continue
        if not args.include_rejected and state.get("repo_decision") == "reject":
            skipped_repo_dirs.add(repo_name)
            skipped_rejected_repo_count += 1
            continue

        rtl_file_hashes = repo_rtl_file_hashes(args.downloads_root, repo_to_paths[repo_name])
        inventory_signature = aggregate_inventory_signature(rtl_file_hashes)
        previous_hashes = state.get("rtl_file_hashes", {})
        if not isinstance(previous_hashes, dict):
            previous_hashes = {}
        inventory_changed = changed_inventory_files(
            {str(k): str(v) for k, v in previous_hashes.items()},
            rtl_file_hashes,
        )
        if (
            not args.force_rescan
            and state.get("signature") == signature
            and state.get("rtl_inventory_signature") == inventory_signature
            and state.get("discovery_schema_version") == DISCOVERY_SCHEMA_VERSION
            and state.get("status") == "scanned"
        ):
            skipped_repo_count += 1
            skipped_repo_dirs.add(repo_name)
            state["skip_unchanged_count"] = int(state.get("skip_unchanged_count", 0)) + 1
            state["last_skipped_at"] = now_iso()
            state["rtl_file_count"] = len(rtl_file_hashes)
            state["rtl_inventory_signature"] = inventory_signature
            state["discovery_schema_version"] = DISCOVERY_SCHEMA_VERSION
            state["rtl_last_changed_files"] = []
            scan_state[repo_name] = state
            continue

        state["signature"] = signature
        state["status"] = "scanning"
        state["rtl_file_count"] = len(rtl_file_hashes)
        state["rtl_inventory_signature"] = inventory_signature
        state["discovery_schema_version"] = DISCOVERY_SCHEMA_VERSION
        state["rtl_last_changed_files"] = inventory_changed[:200]
        state["rtl_file_hashes"] = rtl_file_hashes
        scan_state[repo_name] = state
        repo_candidate_counts.setdefault(repo_name, 0)
        scanned_repo_count += 1
        processed_repo_dirs.add(repo_name)
        build_markers = repo_build_markers(repo_dir)
        file_infos: dict[Path, dict] = {}
        module_to_path: dict[str, Path] = {}
        refcount: Counter[str] = Counter()
        for path in repo_to_paths[repo_name]:
            ok, reason = file_is_candidate(path)
            rtl_text = path.read_text(errors="ignore")
            module_defs = extract_module_defs(rtl_text)
            duplicate_modules = duplicate_module_defs(module_defs)
            if duplicate_modules:
                ok = False
                reason = "duplicate_module_defs"
            instantiated = extract_instantiated_modules(rtl_text)
            if not ok and not module_defs:
                continue
            rel = path.relative_to(args.downloads_root)
            info = {
                "path": path,
                "rel": rel,
                "rtl_text": rtl_text,
                "module_defs": module_defs,
                "duplicate_module_defs": duplicate_modules,
                "instantiated": instantiated,
                "candidate_ok": ok,
                "candidate_reason": reason,
                "non_empty_line_count": sum(1 for line in rtl_text.splitlines() if line.strip()),
                "control_hits": sum(1 for keyword in CONTROL_KEYWORDS if keyword in f"{str(rel).lower()} {rtl_text.lower()}"),
            }
            file_infos[path] = info
            if duplicate_modules:
                continue
            for module_name in module_defs:
                module_to_path.setdefault(module_name, path)

        for info in file_infos.values():
            local_refs = {name for name in info["instantiated"] if name in module_to_path and module_to_path[name] != info["path"]}
            info["local_refs"] = local_refs
            for ref in local_refs:
                refcount[ref] += 1

        candidates: list[dict] = []
        for path, info in file_infos.items():
            if not info["candidate_ok"]:
                continue
            if helper_like_file(info, refcount):
                continue
            if standalone_leaf_low_value(info, refcount):
                continue
            bundle_paths = bundle_closure(path, file_infos, module_to_path)
            if len(bundle_paths) == 1 and info["non_empty_line_count"] < 40:
                continue
            info["bundle_paths"] = bundle_paths
            candidates.append(info)

        candidates.sort(key=rank_candidate)
        kept_for_repo = 0
        for info in candidates:
            if args.max_candidates_per_repo > 0 and kept_for_repo >= args.max_candidates_per_repo:
                break
            path = info["path"]
            rel = info["rel"]
            path_key = canonical_source_identity(str(path))
            if path_key in existing_paths or path_key in excluded_paths or path_key in seen_paths:
                continue
            design = infer_design_name(args.downloads_root, path)
            design_norm = norm_name(design)
            if design_norm in existing or design_norm in seen_designs:
                continue
            seen_designs.add(design_norm)
            seen_paths.add(path_key)
            bundle_paths = info["bundle_paths"]
            include_dirs = sorted({str(p.parent.resolve()) for p in bundle_paths})
            base_priority = choose_priority(rel, rtl_text=info["rtl_text"], non_empty_line_count=info["non_empty_line_count"])
            generated_hit = any(part in GENERATED_DIR_PARTS for part in (p.lower() for p in rel.parts))
            priority = boost_priority(base_priority) if (build_markers and generated_hit) else base_priority
            rows.append(
                {
                    "source": "downloads",
                    "design": design,
                    "priority": priority,
                    "expected_top": infer_expected_top(path, info["rtl_text"]),
                    "source_path": str(path),
                    "rtl_files": ";".join(str(p) for p in bundle_paths),
                    "include_dirs": ";".join(include_dirs),
                    "notes": (
                        f"auto-discovered from _downloads; bundle_aware candidate from {repo_name}; "
                        f"non_empty_lines={info['non_empty_line_count']}; bundle_files={len(bundle_paths)}; local_refs={len(info['local_refs'])}; "
                        f"build_markers={'+'.join(build_markers) if build_markers else 'none'}; "
                        f"generated_rtl={'yes' if generated_hit else 'no'}"
                    ),
                }
            )
            repo_candidate_counts[repo_name] = repo_candidate_counts.get(repo_name, 0) + 1
            kept_for_repo += 1
            if args.limit and len(rows) >= args.limit:
                break
        if args.limit and len(rows) >= args.limit:
            break

    timestamp = now_iso()
    for repo_name in sorted(processed_repo_dirs):
        state = dict(scan_state.get(repo_name, {}))
        state["status"] = "scanned"
        state["last_scanned_at"] = timestamp
        state["scan_count"] = int(state.get("scan_count", 0)) + 1
        state["last_discovered_candidate_count"] = int(repo_candidate_counts.get(repo_name, 0))
        scan_state[repo_name] = state

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "design", "priority", "expected_top", "source_path", "rtl_files", "include_dirs", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)
    write_scan_state(args.scan_state_json, scan_state)

    print(f"wrote {args.out_csv}")
    print(f"candidate_count {len(rows)}")
    print(f"scanned_repo_count {scanned_repo_count}")
    print(f"skipped_unchanged_repo_count {skipped_repo_count}")
    print(f"skipped_rejected_repo_count {skipped_rejected_repo_count}")


if __name__ == "__main__":
    main()
