"""Cold, scope-limited source-lineage adjudication for two R5 TRAIN receipts.

This establishes distinct source authorship/project lineage for the pinned RTL
files, not probabilistic independence of failures or general transfer success.
No Knowledge/Asset status is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import research_r5_lineage_history_audit as history
from . import research_r5_train_axis as axis
from . import research_r5_train_zipcpu as zipcpu


SCHEMA = "tehm-r5-bounded-train-source-lineage-audit-v1"
AXIS_ORIGIN = "74fa9670712d2252493872681a653403689e8fc5"
ZIP_ORIGIN = "ded500c75dba4c528bf642947461227785365cbc"
HISTORY_DIR = (axis.CORPUS / "_qualification" /
               "r5-lineage-history-20260924-QGq6q1")
WORK = axis.CORPUS / "_r5_pilot" / "training"
TRAIN_CASES = (
    ("axis_register", "alexforencich/verilog-axis", "axis-register-skid-train-r1",
     axis, "Alex Forencich", "alexforencich", AXIS_ORIGIN),
    ("zipcpu_skidbuffer", "ZipCPU/wb2axip", "zipcpu-skid-payload-train-r1",
     zipcpu, "ZipCPU", "ZipCPU", ZIP_ORIGIN),
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def _origin(name: str, expected_sha: str, author: str, login: str) -> dict:
    lock = history.CASES[name]
    path = HISTORY_DIR / f"{name}_origin_commit.json"
    detail = json.loads(history.read_pinned(path, lock["origin_commit_sha256"]))
    files = detail.get("files") or []
    matching = [item for item in files if item.get("filename") == lock["path"]]
    _require(detail.get("sha") == expected_sha and len(matching) == 1 and
             matching[0].get("status") == "added" and
             detail["commit"]["author"]["name"] == author and
             (detail.get("author") or {}).get("login") == login,
             name + ":origin_author_or_addition_drift")
    source = history.read_pinned(HISTORY_DIR / f"{name}_origin.v",
                                 lock["origin_source_sha256"])
    _require(history.git_blob(source) == matching[0]["sha"] == lock["origin_blob"],
             name + ":origin_blob_drift")
    return {"sha": expected_sha, "author": author, "github_login": login,
            "date": detail["commit"]["author"]["date"],
            "message": detail["commit"]["message"].splitlines()[0],
            "origin_blob": matching[0]["sha"],
            "origin_detail_sha256": _sha(path.read_bytes()),
            "added_files": sorted(item["filename"] for item in files)}


def _source_closure(repo: str, source_path: str, expected_sha: str) -> dict:
    path = axis.CORPUS / repo / source_path
    _require(path.is_file() and not path.is_symlink() and
             _sha(path.read_bytes()) == expected_sha, "source_file_drift")
    text = path.read_text(encoding="utf-8")
    functional = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    _require(len(re.findall(r"\bmodule\s+[A-Za-z_$][\w$]*\b", functional)) == 1 and
             len(re.findall(r"\bendmodule\b", functional)) == 1 and
             not re.search(r"`\s*include\b", functional),
             "source_closure_requires_single_module_without_include")
    # A conservative local-scope witness, not a general Verilog parser.
    _require("alexforencich" not in text.lower() if repo.startswith("ZipCPU/")
             else "zipcpu" not in text.lower() and "gisselquist" not in text.lower(),
             "cross_project_attribution_in_source")
    if repo.startswith("ZipCPU/"):
        _require("Creator:\tDan Gisselquist" in text and
                 "Project:\tWB2AXIPSP" in text, "zipcpu_creator_header_drift")
    else:
        _require("Copyright (c) 2014-2018 Alex Forencich" in text,
                 "alex_author_header_drift")
    return {"repository": repo, "source_file": source_path,
            "source_sha256": expected_sha,
            "one_module_no_include": True,
            "cross_project_attribution_in_target": False}


def _cold_verify(module, root: Path) -> dict:
    # The frozen axis runner locked its CLI '__main__' code identity.
    repo = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": str(repo / "memory"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-m", module.__name__, "verify", "--work", str(root)],
        cwd=repo, env=env, capture_output=True, check=True, timeout=60)
    return json.loads(result.stdout)


def audit() -> dict:
    checked_history = history.audit(HISTORY_DIR, axis.CORPUS)
    _require(checked_history.get("valid") is True and
             checked_history["overlap_screen"] == {
                 "rtl_file_counts": [31, 63], "exact_duplicates": 0,
                 "comment_whitespace_normalized_duplicates": 0,
                 "substantive_identical_8_line_windows": 0},
             "historical_lineage_or_overlap_audit_failed")
    training = {}
    origin_authors: set[str] = set()
    project_ids: set[str] = set()
    origin_shas: set[str] = set()
    for name, repo, work_name, module, author, login, origin_sha in TRAIN_CASES:
        root = WORK / work_name
        fresh = _cold_verify(module, root)
        saved_bytes = (root / "receipt.json").read_bytes()
        saved = json.loads(saved_bytes)
        _require(fresh.get("valid") is True and fresh == saved and
                 fresh.get("role") == module.ROLE and
                 fresh.get("training_transition") ==
                 "researcher_assisted_not_core_admitted" and
                 fresh.get("transfer_status") == "NOT_RUN" and
                 fresh.get("memory_status") == "M_MINUS_ONLY",
                 name + ":train_replay_or_role_drift")
        prereg = json.loads((root / "preregistration.json").read_bytes())
        _require(prereg.get("source_repository") == repo and
                 prereg.get("source_git_sha") == module.HEAD and
                 prereg.get("role") == module.ROLE and
                 prereg.get("unseen_transfer") is False and
                 prereg.get("production_authority") is False,
                 name + ":train_source_or_role_drift")
        lock = history.CASES[name]
        _require(prereg.get("source_sha256") == "sha256:" + lock["source_sha256"] and
                 lock["head"] == module.HEAD, name + ":history_train_source_mismatch")
        origin = _origin(name, origin_sha, author, login)
        closure = _source_closure(repo, lock["path"], prereg["source_sha256"])
        origin_authors.add(login)
        origin_shas.add(origin_sha)
        project_ids.add(repo)
        training[name] = {
            "repository": repo, "head": module.HEAD,
            "training_receipt_sha256": _sha(saved_bytes),
            "training_receipt_digest": fresh["digest"],
            "preregistration_sha256": _sha((root / "preregistration.json").read_bytes()),
            "origin": origin, "source_closure": closure,
            "role": fresh["role"], "unseen_target": False,
        }
    _require(len(origin_authors) == len(project_ids) == len(origin_shas) == 2,
             "distinct_project_origin_author_required")
    result = {
        "schema": SCHEMA,
        "scope": "only_the_two_pinned_reused_dev_train_source_files",
        "valid": True,
        "source_groups": sorted(project_ids),
        "distinct_source_lineages_for_bounded_pilot": True,
        "statistical_independence_of_failures": "not_established",
        "shared_protocol_concept": "AXI skid buffering is common knowledge, not shared DUT source",
        "copy_or_generator_limit": "hidden_rewrites_or_external_generator_cannot_be_ruled_out_absolutely",
        "history_screen": checked_history["overlap_screen"],
        "training": training,
        "knowledge_or_asset_authority": False,
        "pilot_transfer_started": False,
    }
    result["audit_digest"] = _sha(_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output and args.verify:
        parser.error("--output and --verify are mutually exclusive")
    result = audit()
    if args.output:
        target = args.output.resolve(strict=False)
        if target.parent != axis.CORPUS / "_r5_pilot" / "source-locks" or target.exists():
            raise ValueError("lineage receipt must be new under R5 source-locks")
        target.write_bytes(_json(result))
    elif args.verify:
        saved = args.verify.resolve(strict=True)
        if saved.parent != axis.CORPUS / "_r5_pilot" / "source-locks" or saved.read_bytes() != _json(result):
            raise ValueError("lineage receipt does not cold-replay")
    print(json.dumps({"valid": True, "audit_digest": result["audit_digest"],
                      "source_groups": result["source_groups"],
                      "receipt": str(args.output or args.verify or "stdout")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
