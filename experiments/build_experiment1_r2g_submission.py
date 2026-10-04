#!/usr/bin/env python3
"""Convert one completed R2G acquisition batch into the common Experiment 1 schema."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.run_experiment1_rtl_acquisition import (  # noqa: E402
    R2G_METHOD_IDS,
    TASK_SPEC,
    ExperimentError,
    infer_spdx_identifier,
    read_json,
    validate_submission,
    write_json_atomic,
)


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def git_text(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ExperimentError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def repository_root(path: Path) -> Path:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ExperimentError(f"source is not inside a Git checkout: {path}")


def manifest_paths(entries: Any) -> list[Path]:
    paths: list[Path] = []
    for entry in entries or []:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if value:
            paths.append(Path(str(value)).resolve())
    return paths


def relative_paths(paths: list[Path], root: Path, label: str) -> list[str]:
    values: list[str] = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ExperimentError(f"{label} escapes repository {root}: {path}") from exc
        if relative not in values:
            values.append(relative)
    return values


def license_record(
    meta: dict[str, Any],
    root: Path,
    source_paths: list[Path],
) -> dict[str, Any]:
    evidence = str(meta.get("license_evidence") or "")
    candidates: list[Path] = []
    for token in re.split(r"[\s,:;]+", evidence):
        if token and (root / token).is_file():
            candidates.append(root / token)
            break
    if not candidates:
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
            match = next((path for path in sorted(root.glob(pattern)) if path.is_file()), None)
            if match:
                candidates.append(match)
                break
    candidates.extend(path for path in source_paths if path.is_file())
    observations: list[tuple[Path, str]] = []
    for path in dict.fromkeys(path.resolve() for path in candidates):
        spdx = infer_spdx_identifier(
            path.read_text(encoding="utf-8", errors="ignore")
        )
        if spdx:
            observations.append((path, spdx))
    identifiers = {spdx for _, spdx in observations}
    if len(identifiers) == 1:
        evidence_path, spdx = observations[0]
        path_value = evidence_path.relative_to(root.resolve()).as_posix()
    else:
        spdx = None
        path_value = "LICENSE_NOT_FOUND"
    return {
        "spdx_id": spdx,
        "repository_path": path_value,
        "note": (
            f"normalized_from:{path_value}"
            if spdx
            else evidence or str(meta.get("license_status") or "No license evidence recorded")
        ),
    }


def category_for(meta: dict[str, Any], repo_url: str) -> str:
    text = " ".join(
        [
            str(meta.get("design", "")),
            str(meta.get("top", "")),
            str(meta.get("notes", "")),
            repo_url,
        ]
    ).lower()
    rules = (
        ("UART", ("uart",)),
        ("I2C", ("i2c",)),
        ("SPI", ("spi",)),
        ("Ethernet", ("ethernet", "ethmac", "enet")),
        ("cryptography", ("aes", "sha", "crypto", "chacha", "cipher")),
        ("interconnect", ("axi", "wishbone", "crossbar", "interconnect", "bus")),
        ("DSP", ("dsp", "fft", "fir", "iir", "cordic")),
        ("memory controller", ("memory", "sdram", "ddr", "cache")),
        ("processor", ("cpu", "risc", "mips", "processor", "core")),
        ("accelerator", ("accelerator", "matrix", "vector")),
        ("communication", ("usb", "can", "communication")),
    )
    return next((name for name, tokens in rules if any(token in text for token in tokens)), "controller")


def defines_for(compile_manifest: dict[str, Any]) -> list[str]:
    value = compile_manifest.get("defines") or {}
    if isinstance(value, dict):
        return [
            str(key) if item in (None, "", True) else f"{key}={item}"
            for key, item in value.items()
        ]
    if isinstance(value, list):
        return [str(item).removeprefix("-D") for item in value]
    return []


def candidate_from_meta(meta_path: Path) -> dict[str, Any]:
    meta = read_json(meta_path)
    compile_manifest = meta.get("compile_manifest") or {}
    source_paths = manifest_paths(meta.get("source_manifest"))
    if not source_paths:
        source_paths = [Path(item).resolve() for item in meta.get("rtl_files", [])]
    if not source_paths:
        raise ExperimentError(f"{meta_path}: no source manifest")
    root = repository_root(source_paths[0])
    repo_url = git_text(root, "config", "--get", "remote.origin.url")
    if repo_url.startswith("git@"):
        repo_url = "https://" + repo_url[4:].replace(":", "/", 1)
    if not repo_url.startswith("https://"):
        raise ExperimentError(f"{meta_path}: repository origin is not HTTPS: {repo_url}")
    commit = str(meta.get("source_commit") or git_text(root, "rev-parse", "HEAD")).lower()
    if len(commit) != 40:
        commit = git_text(root, "rev-parse", commit).lower()

    header_paths = manifest_paths(compile_manifest.get("header_manifest"))
    collateral_paths = manifest_paths(compile_manifest.get("collateral_manifest"))
    header_set = {path.resolve() for path in header_paths}
    collateral_set = {path.resolve() for path in collateral_paths}
    rtl_paths = [
        path for path in source_paths
        if path.resolve() not in header_set and path.resolve() not in collateral_set
    ]
    include_paths = [
        Path(item).resolve()
        for item in compile_manifest.get("include_dirs", [])
    ]
    language = "systemverilog" if any(path.suffix.lower() == ".sv" for path in rtl_paths) else "verilog"
    return {
        "candidate_id": re.sub(r"[^A-Za-z0-9_.-]", "_", str(meta["design"]))[:80],
        "repo_url": repo_url,
        "commit": commit,
        "top_module": meta["top"],
        "rtl_files": relative_paths(rtl_paths, root, "RTL input"),
        "header_files": relative_paths(header_paths, root, "header input"),
        "include_dirs": relative_paths(include_paths, root, "include directory"),
        "defines": defines_for(compile_manifest),
        "top_parameters": compile_manifest.get("top_parameters") or {},
        "readmem_files": relative_paths(collateral_paths, root, "readmem collateral"),
        "language": language,
        "license_evidence": license_record(meta, root, source_paths),
        "category": category_for(meta, repo_url),
        "discovery_method": "frozen-r2g-search-expand-pipeline",
        "selection_reason": str(meta.get("notes") or "R2G synth-only success"),
        "confidence": 1.0,
    }


def candidate_from_expander_record(
    row: dict[str, Any], method_id: str
) -> dict[str, Any]:
    """Map one digest-verified bridge row into the common submission contract."""
    root = Path(str(row["source_root"])).resolve()

    def relative(path: str | Path) -> str:
        return Path(path).resolve().relative_to(root).as_posix() or "."

    compile_files = [Path(item).resolve() for item in row["compile_source_files"]]
    source_files = [Path(item["path"]).resolve() for item in row["source_manifest"]]
    headers = [path for path in source_files if path.suffix.lower() in {".vh", ".svh"}]
    compile_set = set(compile_files)
    header_set = set(headers)
    collateral = [
        path for path in source_files if path not in compile_set and path not in header_set
    ]
    evidence = row.get("license_evidence") or {}
    identifiers = list(evidence.get("identifiers") or [])
    license_files = list(evidence.get("license_files") or [])
    parameters = row.get("top_parameters") or {}
    if isinstance(parameters, str):
        parameters = json.loads(parameters) if parameters.strip() else {}
    return {
        "candidate_id": str(row["design"]),
        "repo_url": str(row["repository_url"]),
        "commit": str(row["commit_sha"]).lower(),
        "top_module": str(row["top_module"]),
        "rtl_files": [relative(path) for path in compile_files],
        "header_files": [relative(path) for path in headers],
        "include_dirs": list(
            dict.fromkeys(relative(item) for item in row.get("include_dirs", []))
        ),
        "defines": [],
        "top_parameters": parameters,
        "readmem_files": [relative(path) for path in collateral],
        "language": (
            "systemverilog"
            if any(path.suffix.lower() == ".sv" for path in compile_files)
            else "verilog"
        ),
        "license_evidence": {
            "spdx_id": identifiers[0] if len(identifiers) == 1 else None,
            "repository_path": license_files[0] if license_files else "LICENSE_NOT_FOUND",
            "note": "Imported from certified rtl-expander license evidence",
        },
        "category": str(row.get("function_category") or category_for(row, str(row["repository_url"]))),
        "discovery_method": f"frozen-{method_id}",
        "selection_reason": "Certified Expander candidate passing the public Sky130HD precheck",
        "confidence": 1.0,
    }


def query_records(search_work: Path) -> list[dict[str, Any]]:
    query_log = search_work / "search_queries.jsonl"
    if query_log.is_file():
        records: list[dict[str, Any]] = []
        with query_log.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                payload = json.loads(line)
                records.append(
                    {
                        "query": payload.get("query", ""),
                        "backend": payload.get("backend", ""),
                        "page": int(payload.get("page", 1) or 1),
                        "timestamp": payload.get("timestamp"),
                    }
                )
        return records
    records: list[dict[str, Any]] = []
    for path in sorted(search_work.glob("search_expand_round*.json")):
        payload = read_json(path)
        timestamp = payload.get("generated_at")
        for keyword in payload.get("keywords", []):
            records.append(
                {
                    "query": f"{keyword} language:Verilog",
                    "backend": "github",
                    "page": 1,
                    "timestamp": timestamp,
                }
            )
    return records


def count_clones(downloads: Path) -> int:
    return sum((path / ".git").exists() for path in downloads.iterdir() if path.is_dir())


def count_synth_attempts(workspace: Path) -> int:
    synth_projects = workspace / "synth_projects"
    return sum(
        1
        for path in synth_projects.glob("*/backend/RUN_*")
        if path.is_dir()
    )


def normalized_submission_stop_reason(
    stop_reason: str,
    *,
    candidate_count: int,
    formal_target: int,
    non_scoring_canary: bool,
) -> str:
    if non_scoring_canary and stop_reason == "target_reached" and candidate_count:
        return "submitted_early"
    if candidate_count < formal_target and stop_reason == "target_reached":
        return "provider_failure"
    return stop_reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--method-id", choices=sorted(R2G_METHOD_IDS), required=True)
    parser.add_argument("--batch-id", type=int, choices=(1,), default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    campaign_manifest = read_json(campaign / "execution_manifest.json")
    method_root = campaign / "method_runs" / f"{args.method_id}.batch{args.batch_id}.formal"
    end = read_json(method_root / "method_end.json")
    target = int(read_json(TASK_SPEC)["batch_policy"]["target_candidates_per_batch"])
    prequalified = method_root / "prequalified_candidates.json"
    if prequalified.is_file():
        candidates = read_json(prequalified)
        if not isinstance(candidates, list):
            raise ExperimentError("prequalified candidate artifact must be a JSON array")
        candidates = candidates[:target]
        conversion_errors: list[str] = []
        queries = list(end.get("queries") or [])
        stop_reason = str(end["stop_reason"])
    else:
        # Legacy R2G-Pipeline compatibility path retained for archived Pilot replay.
        acquire = method_root / "acquire"
        corpus = acquire / "corpus"
        rows = [row for row in csv_rows(corpus / "index.csv") if row.get("status") == "success"]
        candidates = []
        keys: set[tuple[str, str, str]] = set()
        conversion_errors = []
        for row in rows:
            if len(candidates) >= target:
                break
            try:
                candidate = candidate_from_meta(corpus / row["design"] / "design_meta.json")
            except ExperimentError as exc:
                conversion_errors.append(f"{row['design']}: {exc}")
                continue
            key = (candidate["repo_url"].rstrip("/"), candidate["commit"], candidate["top_module"])
            if key in keys:
                continue
            keys.add(key)
            candidates.append(candidate)
        queries = query_records(acquire / "workspace" / "search")
        stop_reason = end["stop_reason"]
    if conversion_errors:
        (method_root / "submission_conversion_errors.txt").write_text(
            "\n".join(conversion_errors) + "\n",
            encoding="utf-8",
        )

    stop_reason = normalized_submission_stop_reason(
        stop_reason,
        candidate_count=len(candidates),
        formal_target=target,
        non_scoring_canary=(
            campaign_manifest.get("campaign_mode") == "non_scoring_canary"
            and end.get("non_scoring_target") is not None
        ),
    )
    submission = {
        "schema_version": "1.0",
        "experiment_id": end["experiment_id"],
        "method_id": args.method_id,
        "batch_id": args.batch_id,
        "model_route": {
            "requested_model": None,
            "actual_model": None,
            "api_provider": None,
            "endpoint_kind": "none",
            "reasoning_effort": None,
            "provider_fingerprint": None,
        },
        "started_at": end["started_at"],
        "ended_at": end["ended_at"],
        "stop_reason": stop_reason,
        "resource_usage": {
            "method_runtime_seconds": end["method_runtime_seconds"],
            "llm_turns": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0,
            "search_requests": len(queries),
            "clone_count": int(end.get("clone_count", 0)),
            "self_synth_attempts": int(end.get("self_synth_attempts", 0)),
            "format_repairs": 0,
            "human_interventions": 0,
        },
        "queries": queries,
        "candidates": candidates,
        "out_of_scope": [],
    }
    output = args.output or method_root / "submission.json"
    write_json_atomic(output, submission)
    _, errors = validate_submission(output, campaign_root=campaign)
    if errors:
        raise ExperimentError("generated R2G submission is invalid:\n" + "\n".join(errors))
    print(
        f"Wrote {len(candidates)} R2G candidates to {output}; "
        f"conversion_errors={len(conversion_errors)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
