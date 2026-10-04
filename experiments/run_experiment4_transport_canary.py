#!/usr/bin/env python3
"""Probe all Experiment 4 routes and the v2 source-envelope transport."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import py_compile
import tempfile

import run_experiment4_llm_converter as llm


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument('--models', nargs='+', choices=('gpt', 'claude', 'qwen'),
                        default=('gpt', 'claude', 'qwen'))
    parser.add_argument('--merge-existing', action='store_true')
    args = parser.parse_args()
    llm.load_env_file(args.env_file)
    system = (
        "Return exactly the delimiter envelope requested below. Do not use Markdown or JSON. "
        "Write a non-empty summary of at least twenty words, then provide Python source that is "
        "exactly: print('EXP4_TRANSPORT_OK')"
    )
    user = (
        "Use this exact response structure and replace only the summary text:\n"
        "===SUMMARY_BEGIN===\n"
        "Explain that this is a deterministic transport and source-envelope validation probe "
        "without changing the requested Python source.\n"
        "===SUMMARY_END===\n"
        "===PYTHON_SOURCE_BEGIN===\nprint('EXP4_TRANSPORT_OK')\n===PYTHON_SOURCE_END==="
    )
    prior = {}
    if args.merge_existing and args.output.is_file():
        prior = {row['model_key']: row for row in llm.read_json(args.output).get('results') or []}
    results_by_model = dict(prior)
    for model_key in args.models:
        route = llm.route_by_key(args.routes, model_key)
        raw = None
        usage = {}
        try:
            raw, usage = llm.call_model(route, system, user, args.max_tokens, 2)
            parsed = llm.parse_response(raw)
            source_ok = parsed["python_source"].strip() == "print('EXP4_TRANSPORT_OK')"
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "probe.py"
                source.write_text(parsed["python_source"], encoding="utf-8")
                py_compile.compile(str(source), doraise=True)
            status = "passed" if source_ok else "failed"
            current = {
                    "model_key": model_key,
                    "model_id": route["model_id"],
                    "status": status,
                    "source_exact": source_ok,
                    "charged_tokens": llm.token_count(usage, args.max_tokens),
                    "usage": usage,
                    'raw_response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                    'raw_response_bytes': len(raw.encode()),
                }
        except Exception as exc:
            current = {"model_key": model_key, "model_id": route["model_id"],
                       "status": "failed", "error": repr(exc), 'usage': usage}
            if raw is not None:
                current.update(raw_response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                               raw_response_bytes=len(raw.encode()))
        previous = prior.get(model_key)
        history = list((previous or {}).get('attempt_history') or [])
        if previous:
            history.append({key: value for key, value in previous.items() if key != 'attempt_history'})
        if history:
            current['attempt_history'] = history
        results_by_model[model_key] = current
    results = [results_by_model[key] for key in ('gpt', 'claude', 'qwen') if key in results_by_model]
    payload = {
        "schema_version": "experiment4-transport-canary-2.0",
        "created_at": utc_now(),
        "status": "passed" if len(results) == 3 and all(row["status"] == "passed" for row in results) else "failed",
        "secret_values_persisted": False,
        "results": results,
    }
    llm.write_json(args.output, payload)
    print(json.dumps({"status": payload["status"], "results": results}, sort_keys=True))
    if payload["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
