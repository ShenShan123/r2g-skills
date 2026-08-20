#!/usr/bin/env python3
"""Build versioned benchmark contamination fingerprints from supplied local sources."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


REGISTRY_SCHEMA = "rtl_benchmark_registry_v1"
REQUIRED_BENCHMARKS = {"hdlbits", "verilog_eval", "rtllm", "verilogbench", "internal_reserved"}
RTL_SUFFIXES = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/|--[^\n]*", re.S)
TOKEN_RE = re.compile(r"[A-Za-z_$][\w$]*|\d+'[bdho][0-9a-fxz_]+|\d+|<=|>=|==|!=|&&|\|\||\S", re.I)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text)
    os.replace(temporary, path)


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    clean = COMMENT_RE.sub(" ", text)
    normalized = re.sub(r"\s+", " ", clean).strip().lower()
    tokens = TOKEN_RE.findall(clean.lower())
    identifiers = sorted(set(re.findall(r"(?im)^\s*(?:module|entity)\s+([A-Za-z_$][\w$]*)", clean)))
    semantic_terms = sorted(set(re.findall(r"\b(?:axi|ahb|apb|wishbone|uart|spi|i2c|pcie|usb|ethernet|ddr|fifo|cache|cpu|riscv|aes|sha|fft|dma|noc)\b", clean.lower())))
    return {
        "schema": REGISTRY_SCHEMA, "path": str(path.relative_to(root)),
        "raw_hash": sha(raw), "normalized_hash": sha(normalized.encode()),
        "ast_token_fingerprint": sha("\0".join(tokens).encode()),
        "semantic_fingerprint": sha(json.dumps({"units": identifiers, "terms": semantic_terms}, sort_keys=True).encode()),
        "design_units": identifiers, "semantic_terms": semantic_terms,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work" / "data" / "rtl_corpus")
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-url")
    parser.add_argument("--task-count", type=int)
    parser.add_argument("--pending", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark = re.sub(r"[^a-z0-9_-]+", "_", args.benchmark.lower())
    target = args.corpus_root / "benchmark_registry" / benchmark
    if args.pending:
        rows: list[dict[str, Any]] = []
    elif not args.source_root or not args.source_root.is_dir():
        raise SystemExit(f"source root does not exist: {args.source_root}")
    else:
        rows = [fingerprint(path, args.source_root) for path in sorted(args.source_root.rglob("*")) if path.is_file() and path.suffix.lower() in RTL_SUFFIXES]
    revision = "UNAVAILABLE"
    if args.source_root and (args.source_root / ".git").exists():
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.source_root, text=True, capture_output=True, timeout=10, check=False)
        if result.returncode == 0:
            revision = result.stdout.strip()
    snapshot_hash = sha("\0".join(sorted(row["raw_hash"] for row in rows)).encode()) if rows else None
    atomic_text(target / "fingerprints.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    entry = {
        "schema": REGISTRY_SCHEMA, "benchmark": benchmark,
        "source_root": str(args.source_root) if args.source_root else None,
        "source_url": args.source_url, "source_revision": revision,
        "snapshot_hash": snapshot_hash, "task_count": args.task_count,
        "source_artifact_count": len(rows),
        "fingerprint_count": len(rows) * 4,
        "fingerprint_count_by_type": {kind: len(rows) for kind in ("raw_rtl", "normalized_rtl", "ast", "semantic")},
        "fingerprints": len(rows),
        "status": "PENDING_SOURCE" if args.pending else "ACTIVE" if rows else "EMPTY",
    }
    atomic_text(target / "registry.json", json.dumps(entry, indent=2, sort_keys=True) + "\n")
    catalog_path = args.corpus_root / "benchmark_registry" / "registry_catalog.json"
    lock_path = args.corpus_root / "benchmark_registry" / ".catalog.lock"
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        catalog: dict[str, Any] = {"schema": REGISTRY_SCHEMA, "required_benchmarks": sorted(REQUIRED_BENCHMARKS), "entries": {}}
        if catalog_path.exists():
            try:
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        catalog.setdefault("entries", {})[benchmark] = entry
        catalog["required_benchmarks"] = sorted(REQUIRED_BENCHMARKS)
        catalog["ready"] = all(
            catalog["entries"].get(name, {}).get("status") == "ACTIVE"
            and int(catalog["entries"].get(name, {}).get("fingerprints", 0)) > 0
            for name in REQUIRED_BENCHMARKS
        )
        atomic_text(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"benchmark": benchmark, "fingerprints": len(rows), "path": str(target), "registry_ready": catalog["ready"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
