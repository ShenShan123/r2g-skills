"""Cold integrity check for the R5 DEV-only public module history snapshot.

This validates the archived GitHub responses and locked local source bytes.  It
does not decide that two source families are independent or admit Memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


CASES = {
    "axis_register": {
        "repo": "alexforencich/verilog-axis",
        "head": "48ff7a7e2ef782cf778d47910cf85835c64b1bce",
        "path": "rtl/axis_register.v",
        "source_sha256": "599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39",
        "history_sha256": "d0c09151cb1540bdaf49e84a00af03a7c15938c0f0e52f8b591987b50443a81f",
        "origin_commit_sha256": "fb0b7560bb6a3a8e9ce2bc74480cc369b97bd6388e74195689ff600b43c59829",
        "origin_source_sha256": "5c10d52312a8a8208ab8fc91197c02821a4527dbc9c7467075dab727df668a4f",
        "origin_blob": "42fa50104afda37c2443c41fe5c2c987c3dd2148",
        "origin": "74fa9670712d2252493872681a653403689e8fc5",
        "count": 17,
    },
    "axis_broadcast": {
        "repo": "alexforencich/verilog-axis",
        "head": "48ff7a7e2ef782cf778d47910cf85835c64b1bce",
        "path": "rtl/axis_broadcast.v",
        "source_sha256": "a644b7542adf7552cb4713ab22856e1b76c58780da970d122351e45bc0bc51bf",
        "history_sha256": "f3481ff5f1c01ddfe151d9f02fdb42158a118511962ee219e482214e06477b73",
        "origin_commit_sha256": "709a6de08c7ad91a28a72d589969437e9545fd06b25fa79d3d5cfc4c55446b44",
        "origin_source_sha256": "619d43b34fea5b249137947b5610220c76d0fbd0398ec3a03d22ce5afe426ec0",
        "origin_blob": "c285ff3e3797d00562f0f93cf1c076f0644548dc",
        "origin": "b60886a0eca31198f9a79874f73e21963c51656e",
        "count": 7,
    },
    "zipcpu_skidbuffer": {
        "repo": "ZipCPU/wb2axip",
        "head": "2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b",
        "path": "rtl/skidbuffer.v",
        "source_sha256": "ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389",
        "history_sha256": "bf0a98b8057d8b8fc1cb2abe536ae023f3c60f90d3f616eb7cb4021c826ae994",
        "origin_commit_sha256": "00c75aad6642f2fae1d88ee8623d1ded65451230e249fb1a48cada0844e0d990",
        "origin_source_sha256": "729469f9dd4919e5c938dabbb518ba12d200aa69db9a537523c1e65b9c3bfba8",
        "origin_blob": "dfd7115f535ea564e88217be7fc39f61191d1087",
        "origin": "ded500c75dba4c528bf642947461227785365cbc",
        "count": 22,
    },
}

REPOSITORY_METADATA = {
    "alexforencich/verilog-axis": (
        "verilog_axis_repo_metadata.json",
        "567e3d3b421e14804f8cc178fb5ade8240e4daa7d687249b56d3a48041d4d8a5",
    ),
    "ZipCPU/wb2axip": (
        "wb2axip_repo_metadata.json",
        "2178eaab6baff40cbbdfe2692e65573fe5221d9ea9073cfdf0c3ee62e68cf9d9",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_pinned(path: Path, digest: str) -> bytes:
    payload = path.read_bytes()
    require(hashlib.sha256(payload).hexdigest() == digest, f"SHA256 mismatch: {path}")
    return payload


def git_blob(payload: bytes) -> str:
    framed = b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
    return hashlib.sha1(framed).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def code_lines(path: Path) -> list[str]:
    """Screening normalization, not a Verilog parser or plagiarism detector."""
    source = path.read_text(errors="replace")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return [re.sub(r"\s+", "", line) for line in source.splitlines() if line.strip()]


def overlap_screen(corpus_root: Path) -> dict:
    roots = [corpus_root / repo / "rtl" for repo in REPOSITORY_METADATA]
    trees = [sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".v", ".sv"}) for root in roots]
    require([len(tree) for tree in trees] == [31, 63], "locked RTL tree size changed")
    raw = [{p: hashlib.sha256(p.read_bytes()).digest() for p in tree} for tree in trees]
    normalized = [{p: code_lines(p) for p in tree} for tree in trees]
    exact = [(a.name, b.name) for a, x in raw[0].items() for b, y in raw[1].items() if x == y]
    normalized_duplicates = [
        (a.name, b.name)
        for a, x in normalized[0].items()
        for b, y in normalized[1].items()
        if x == y
    ]
    windows = set()
    for lines in normalized[0].values():
        for index in range(len(lines) - 7):
            window = tuple(lines[index:index + 8])
            if sum(map(len, window)) >= 160:
                windows.add(window)
    shared_windows = sum(
        tuple(lines[index:index + 8]) in windows
        for lines in normalized[1].values()
        for index in range(len(lines) - 7)
    )
    require(not exact and not normalized_duplicates and not shared_windows,
            "cross-repository RTL overlap screen changed")
    return {"rtl_file_counts": [31, 63], "exact_duplicates": 0,
            "comment_whitespace_normalized_duplicates": 0,
            "substantive_identical_8_line_windows": 0}


def audit(history_dir: Path, corpus_root: Path) -> dict:
    for repo_name, (filename, digest) in REPOSITORY_METADATA.items():
        metadata = json.loads(read_pinned(history_dir / filename, digest))
        require(metadata["full_name"] == repo_name and metadata["fork"] is False,
                f"repository identity or fork status changed: {repo_name}")
    out = {}
    for name, lock in CASES.items():
        repo = corpus_root / lock["repo"]
        require(git(repo, "rev-parse", "HEAD") == lock["head"], f"HEAD mismatch: {repo}")
        require(not git(repo, "status", "--porcelain"), f"dirty checkout: {repo}")
        read_pinned(repo / lock["path"], lock["source_sha256"])

        history = json.loads(read_pinned(history_dir / f"{name}_commits.json", lock["history_sha256"]))
        require(isinstance(history, list) and len(history) == lock["count"], f"history count: {name}")
        shas = [item["sha"] for item in history]
        require(len(shas) == len(set(shas)), f"duplicate commit: {name}")
        require(shas[-1] == lock["origin"], f"oldest commit mismatch: {name}")
        require(all(item["html_url"].startswith(f"https://github.com/{lock['repo']}/commit/") for item in history), f"history repo mismatch: {name}")

        detail = json.loads(read_pinned(history_dir / f"{name}_origin_commit.json", lock["origin_commit_sha256"]))
        require(detail["sha"] == lock["origin"], f"origin detail mismatch: {name}")
        matches = [item for item in detail["files"] if item["filename"] == lock["path"]]
        require(len(matches) == 1 and matches[0]["status"] == "added", f"origin addition missing: {name}")
        payload = read_pinned(history_dir / f"{name}_origin.v", lock["origin_source_sha256"])
        require(git_blob(payload) == matches[0]["sha"] == lock["origin_blob"], f"origin blob mismatch: {name}")
        out[name] = {
            "repository": lock["repo"],
            "locked_head": lock["head"],
            "file_commits": len(shas),
            "first_file_commit": lock["origin"],
            "origin_status": "added",
            "origin_blob_verified": True,
        }
    return {"valid": True, "scope": "DEV source relationship screening only",
            "independent_lineages_verified": False, "repository_fork_flags": "both false",
            "overlap_screen": overlap_screen(corpus_root), "cases": out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.history_dir, args.corpus_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
