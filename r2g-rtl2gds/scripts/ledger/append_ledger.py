"""``append_ledger.py`` — append one record to ``run_ledger.jsonl``.

This is called by every flow / label script at the end of its run, NOT by
agents directly. See TEACHING_POLICY.md §12.1.

Usage as CLI (from flow scripts):

    python3 scripts/ledger/append_ledger.py \\
        --teaching-root /path/to/teaching_root \\
        --design        usb_cdc_top \\
        --stage         stage2 \\
        --step          orfs_backend \\
        --command       "scripts/flow/run_orfs.sh ..." \\
        --inputs-glob   "design_cases/usb_cdc_top/rtl/*.v,..." \\
        --outputs-glob  "design_cases/usb_cdc_top/backend/RUN_*/results/*" \\
        --start-ts      2026-05-29T08:12:03Z \\
        --end-ts        2026-05-29T08:13:47Z \\
        --exit-code     0 \\
        --triggered-by  flow_script

Usage as library (in pytest):

    from scripts.ledger.append_ledger import append_record
    record = append_record(teaching_root=..., design=..., ...)

Concurrency: multiple flow scripts may finish at the same time (e.g. lint and
simulation of different designs running in parallel). We use a POSIX advisory
lock on a side-car ``.lock`` file plus an atomic-append pattern to keep the
chain consistent even under concurrent callers.

Failure model: this script writes the ledger truthfully or it dies trying. It
never silently produces a partial record. If anything is missing or wrong, the
flow script will see a non-zero exit code; the agent / human can then decide
what to do.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import glob as _glob
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Allow running as a script (no package context) or as a module.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from canonical import compute_record_hash         # type: ignore
    from metrics_parsers import get_parser            # type: ignore
else:
    from .canonical import compute_record_hash
    from .metrics_parsers import get_parser

log = logging.getLogger("append_ledger")

LEDGER_FILENAME = "run_ledger.jsonl"
LOCK_SUFFIX = ".lock"
RECORD_VERSION = "1.0"
GENESIS_HASH = "GENESIS"

# Tool version commands per step. None means "no specific tool" (skip).
TOOL_VERSION_CMDS: dict[str, list[list[str]]] = {
    "lint":             [["verilator", "--version"]],
    "simulation":       [["iverilog", "-V"]],
    "synthesis":        [["yosys", "-V"]],
    "orfs_backend":     [["openroad", "-version"], ["yosys", "-V"]],
    "timing_check":     [["openroad", "-version"]],
    "drc_klayout":      [["klayout", "-v"]],
    "lvs_klayout":      [["klayout", "-v"]],
    "rcx_openrcx":      [["openroad", "-version"]],
    "label_wirelength": [["python3", "--version"]],
    "label_congestion": [["python3", "--version"]],
    "label_timing":     [["openroad", "-version"]],
    "label_irdrop":     [["openroad", "-version"]],
}

# Anyone calling us must say who they are. "agent_direct" is rejected.
ALLOWED_TRIGGERS = {
    "flow_script",
    "label_script",
    "timing_check",
    "report_generator",
    "test",            # for unit tests only; autograder treats as failure
}
FORBIDDEN_TRIGGER = "agent_direct"


# ─── path & io helpers ───────────────────────────────────────────────────────

def _sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file, hex digest. Raises OSError on read fail."""
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _normalize_path(abs_path: Path, anchors: list[tuple[str, Path]]) -> str:
    """Express ``abs_path`` relative to the closest anchor, returning a
    string of the form ``"<placeholder>/sub/path"``.

    ``anchors`` is ordered most-specific-first; first match wins. If nothing
    matches, the absolute path is returned verbatim (caller can decide how to
    handle that).
    """
    try:
        abs_path = abs_path.resolve()
    except OSError:
        return str(abs_path)
    for placeholder, root in anchors:
        try:
            rel = abs_path.relative_to(root.resolve())
            return f"{placeholder}/{rel.as_posix()}"
        except (ValueError, OSError):
            continue
    return abs_path.as_posix()


def _expand_globs(spec: str, base: Path) -> list[Path]:
    """Expand a comma-separated list of glob patterns into existing file paths.

    Patterns are resolved relative to ``base`` if not absolute. Directories
    are skipped; only regular files are returned. Order is deterministic.
    """
    if not spec:
        return []
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in spec.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        if not os.path.isabs(pattern):
            pattern = str(base / pattern)
        for hit in sorted(_glob.glob(pattern, recursive=True)):
            p = Path(hit).resolve()
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            out.append(p)
    return out


def _hash_dict_for_files(
    files: Sequence[Path], anchors: list[tuple[str, Path]]
) -> dict[str, str]:
    """Produce {normalized_path: sha256} for a list of files. Sorted by path."""
    out = {}
    for p in files:
        try:
            digest = _sha256_file(p)
        except OSError as e:
            log.warning("cannot hash %s: %s", p, e)
            continue
        out[_normalize_path(p, anchors)] = digest
    return dict(sorted(out.items()))


def _collect_tool_versions(step: str) -> dict[str, str]:
    """Best-effort tool-version banners. Missing tools record as ``"missing"``;
    callers / verifiers treat empty banner as a red flag."""
    cmds = TOOL_VERSION_CMDS.get(step, [])
    out: dict[str, str] = {}
    for cmd in cmds:
        name = cmd[0]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            banner = (res.stdout + res.stderr).strip().splitlines()
            out[name] = banner[0] if banner else ""
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("cannot get version of %s: %s", name, e)
            out[name] = "missing"
    return out


def _git_short_hash(repo_root: Path) -> str:
    """Return git short hash of ``repo_root``, or ``"untracked"`` if N/A."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip() or "untracked"
    except (OSError, subprocess.SubprocessError):
        pass
    return "untracked"


def _iso_diff_seconds(start_ts: str, end_ts: str) -> float:
    """ISO-8601 duration in seconds, 3 decimal places."""
    def parse(ts: str) -> _dt.datetime:
        ts = ts.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(ts)
    delta = (parse(end_ts) - parse(start_ts)).total_seconds()
    return round(delta, 3)


def _read_last_record(ledger_path: Path) -> Optional[dict]:
    """Return the last record in the ledger, or None if file is empty/missing.

    Reads from the end of the file, growing the window until it contains the
    whole final line (i.e. an internal newline delimiting the last record, or
    the entire file). A single record can legitimately exceed 64 KiB when its
    inputs/outputs hash many files, so a fixed tail window must NOT be assumed.
    """
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        return None
    with ledger_path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        block = 65536
        while True:
            block = min(block, size)
            f.seek(size - block, os.SEEK_SET)
            tail = f.read(block)
            stripped = tail[:-1] if tail.endswith(b"\n") else tail
            # We have the full last line if either an internal newline is
            # present (so the last line is fully inside `tail`) or we've read
            # the entire file (the last line is the whole content).
            if b"\n" in stripped or block >= size:
                last_line = stripped.rsplit(b"\n", 1)[-1]
                break
            block *= 4  # last line longer than window — grow and retry
    try:
        return json.loads(last_line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"ledger {ledger_path} is corrupt at tail: {e}"
        ) from e


def _atomic_append_line(ledger_path: Path, line: str) -> None:
    """fsync-protected append of a single JSONL line.

    We open in 'a' mode (POSIX guarantees atomicity for writes <= PIPE_BUF, but
    a single record may exceed that — so we hold the file lock for the whole
    write and fsync before releasing).
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(line)
        if not line.endswith("\n"):
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())


# ─── main append ─────────────────────────────────────────────────────────────

def append_record(
    *,
    teaching_root: Path,
    repo_root: Path,
    design: str,
    stage: str,
    step: str,
    command: str,
    inputs_files: Sequence[Path],
    outputs_files: Sequence[Path],
    start_ts: str,
    end_ts: str,
    exit_code: int,
    triggered_by: str,
    agent_backend: Optional[str] = None,
    working_directory: Optional[Path] = None,
    env_overrides_path: Optional[Path] = None,
    notes: str = "",
) -> dict[str, Any]:
    """Append one record. Returns the record dict (with ``record_hash`` filled).

    Raises on any precondition failure — we never write a partial record.
    """
    if triggered_by == FORBIDDEN_TRIGGER:
        raise PermissionError(
            "triggered_by='agent_direct' is forbidden by policy §12.1; "
            "the ledger must only be written by flow/label scripts"
        )
    if triggered_by not in ALLOWED_TRIGGERS:
        raise ValueError(
            f"triggered_by={triggered_by!r} not in allowed set "
            f"{sorted(ALLOWED_TRIGGERS)}"
        )
    if stage not in {"stage1", "stage2", "stage3", "stage4"}:
        raise ValueError(f"invalid stage: {stage!r}")

    # parser lookup is strict: unknown step → loud KeyError
    parser = get_parser(step)

    teaching_root = teaching_root.resolve()
    repo_root = repo_root.resolve()
    anchors = [
        ("<teaching_root>", teaching_root),
        ("<repo>",          repo_root),
    ]

    ledger_path = teaching_root / LEDGER_FILENAME
    lock_path = ledger_path.with_suffix(ledger_path.suffix + LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Hash inputs and outputs BEFORE acquiring the lock — they may be slow on
    # multi-MB GDS files, and we don't want to block other writers needlessly.
    inputs_dict = _hash_dict_for_files(list(inputs_files), anchors)
    outputs_dict = _hash_dict_for_files(list(outputs_files), anchors)

    # outputs[name → abs_path] for the metrics parser (it expects abs paths)
    outputs_map_for_parser: dict[str, Path] = {}
    by_norm = {_normalize_path(p.resolve(), anchors): p.resolve()
               for p in outputs_files if p.is_file()}
    outputs_map_for_parser.update(by_norm)
    try:
        key_metrics = parser(outputs_map_for_parser)
    except Exception as e:
        # A buggy parser is a developer error, but we still record what we
        # have rather than crash the entire flow — the empty metrics will be
        # visible to the verifier.
        log.error("parser for step %r raised: %s", step, e)
        key_metrics = {}

    # env overrides fingerprint
    env_overrides_hash = "NONE"
    if env_overrides_path and env_overrides_path.is_file():
        try:
            env_overrides_hash = _sha256_file(env_overrides_path)
        except OSError as e:
            log.warning("cannot hash env override file: %s", e)

    tool_versions = _collect_tool_versions(step)
    r2g_commit = _git_short_hash(repo_root)
    if agent_backend is None:
        agent_backend = os.environ.get("AGENT_BACKEND", "unknown")
    if working_directory is None:
        working_directory = Path.cwd()

    # ── critical section: lock, read tail, build, append ──────────────────
    with lock_path.open("a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            try:
                last = _read_last_record(ledger_path)
            except RuntimeError:
                # Corrupted tail. Refuse to write — preserves invariant that
                # every record is verifiable. Caller (flow script) will fail.
                raise

            if last is None:
                prev_hash = GENESIS_HASH
                run_seq = 0
            else:
                prev_hash = last["record_hash"]
                run_seq = int(last["run_seq"]) + 1

            record: dict[str, Any] = {
                "record_version": RECORD_VERSION,
                "prev_hash": prev_hash,
                # record_hash filled below
                "run_id": str(uuid.uuid4()),
                "run_seq": run_seq,
                "design": design,
                "stage": stage,
                "step": step,
                "command": command,
                "working_directory": _normalize_path(working_directory, anchors),
                "tool_versions": tool_versions,
                "inputs": inputs_dict,
                "outputs": outputs_dict,
                "key_metrics": key_metrics,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "duration_s": _iso_diff_seconds(start_ts, end_ts),
                "exit_code": int(exit_code),
                "agent_backend": agent_backend,
                "r2g_commit": r2g_commit,
                "env_overrides_hash": env_overrides_hash,
                "triggered_by": triggered_by,
                "notes": notes,
            }
            record["record_hash"] = compute_record_hash(record)

            line = json.dumps(record, ensure_ascii=False)
            _atomic_append_line(ledger_path, line)
            return record
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


# ─── CLI wrapper ─────────────────────────────────────────────────────────────

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Append a record to run_ledger.jsonl. "
                    "Called by flow/label scripts; never by agents directly.",
    )
    p.add_argument("--teaching-root", required=True, type=Path)
    p.add_argument("--repo-root", required=False, type=Path, default=None,
                   help="default: parent of the directory containing this script")
    p.add_argument("--design", required=True)
    p.add_argument("--stage", required=True,
                   choices=["stage1", "stage2", "stage3", "stage4"])
    p.add_argument("--step", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--inputs-glob", default="",
                   help="comma-separated glob patterns")
    p.add_argument("--outputs-glob", default="",
                   help="comma-separated glob patterns")
    p.add_argument("--start-ts", required=True)
    p.add_argument("--end-ts", required=True)
    p.add_argument("--exit-code", required=True, type=int)
    p.add_argument("--triggered-by", required=True,
                   choices=sorted(ALLOWED_TRIGGERS))
    p.add_argument("--agent-backend", default=None)
    p.add_argument("--working-directory", default=None, type=Path)
    p.add_argument("--env-overrides", default=None, type=Path)
    p.add_argument("--notes", default="")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _infer_repo_root() -> Path:
    """Default repo_root: two parents up from this script
    (``r2g-rtl2gds/scripts/ledger/append_ledger.py``  ->  ``<repo>``)."""
    return Path(__file__).resolve().parents[2]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    repo_root = args.repo_root or _infer_repo_root()
    base = args.working_directory or Path.cwd()

    inputs_files = _expand_globs(args.inputs_glob, base=base)
    outputs_files = _expand_globs(args.outputs_glob, base=base)

    try:
        record = append_record(
            teaching_root=args.teaching_root,
            repo_root=repo_root,
            design=args.design,
            stage=args.stage,
            step=args.step,
            command=args.command,
            inputs_files=inputs_files,
            outputs_files=outputs_files,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
            exit_code=args.exit_code,
            triggered_by=args.triggered_by,
            agent_backend=args.agent_backend,
            working_directory=args.working_directory,
            env_overrides_path=args.env_overrides,
            notes=args.notes,
        )
    except (PermissionError, ValueError) as e:
        print(f"append_ledger: policy violation: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"append_ledger: ledger corrupt or unwritable: {e}",
              file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"append_ledger: unexpected: {e}", file=sys.stderr)
        return 1

    if args.verbose:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(record["record_hash"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
