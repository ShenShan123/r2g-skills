#!/usr/bin/env python3
"""Semantic RTL readiness gate (RMD-HO-P0-01, held-out V3 P0-HO-01).

The defect: the initially-selected `secworks_sha3` repository states in its own
README `Not completed. Does not work. Do. Not. Use.` The Agent synthesized and
promoted it anyway — despite substantial undriven internal-wire evidence in the
synthesis log — and then spent physical-design resources until detailed routing
reported 8,726 violations. Routing happened to block publication that time, but
**physical cleanliness is not a functional-correctness proof**: a structurally
incomplete design that DID route clean would have entered the graph corpus as
training data.

Design constraints this gate is built around:

  * README keyword matching alone must NOT be a rejection rule. Repositories say
    "unsupported" and "deprecated" in historical notes, changelogs and
    third-party attributions all the time. A textual claim is one input.
  * Structural evidence alone must not be one either: an undriven TOP-LEVEL input
    port is normal, and so is an explicitly tied-off net.
  * The gate therefore combines them. A strong negative project status
    CORROBORATED by structural warnings blocks; either signal alone downgrades to
    `manual_review` at most, and neither yields `ready`.
  * Absence of a testbench is `not_available`, never a fabricated pass.

Verdicts: `ready` | `manual_review` | `rejected_semantic_incomplete`.
Everything the verdict rests on is returned as evidence so the decision is
reproducible from the source manifest.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

READY = "ready"
MANUAL_REVIEW = "manual_review"
REJECTED = "rejected_semantic_incomplete"

# Files whose text is treated as a project STATUS declaration. Deliberately small:
# a CHANGELOG or a docs/ tree is history, not current status.
STATUS_FILES = ("README", "README.md", "README.rst", "README.txt", "STATUS",
                "STATUS.md", "readme.md", "Readme.md")

# STRONG negative declarations — phrases that state the project itself does not
# work. Each is a full phrase, never a bare word: "unsupported" or "deprecated"
# on its own is far too common in historical notes to carry a verdict.
STRONG_NEGATIVE_RE = re.compile(
    r"(?im)^\W*(?:"
    r"not\s+complete(?:d)?\b"
    r"|does\s+not\s+work\b"
    r"|doesn'?t\s+work\b"
    r"|do\.?\s*not\.?\s*use\b"
    r"|do\s+not\s+use\s+this\b"
    r"|work\s+in\s+progress\s*[-—:]\s*(?:not|does\s+not)\b"
    r"|this\s+(?:core|design|project)\s+is\s+(?:broken|incomplete|unfinished)\b"
    r"|non[- ]functional\b"
    r")")

# Yosys structural warnings that indicate the design is not whole. Undriven
# INTERNAL wires and unresolved module references are the two that mattered on
# SHA3; a blackbox is only a defect when it is not a declared dependency.
UNDRIVEN_RE = re.compile(
    r"(?im)^\s*Warning:\s*Wire\s+(\S+)\s+is\s+used\s+but\s+has\s+no\s+driver")
UNRESOLVED_MODULE_RE = re.compile(
    r"(?im)Warning:\s*(?:Identified|Found)?\s*.*?module\s+[`'\\]?([\w$\\]+)['`]?\s+"
    r"(?:not\s+found|is\s+not\s+part\s+of\s+the\s+design|declared\s+as\s+blackbox)")
DRIVER_CONFLICT_RE = re.compile(
    r"(?im)Warning:\s*.*?(?:multiple\s+drivers|conflicting\s+drivers)")

# How much undriven internal logic is "material". A handful of undriven wires is
# routine in real cores (unused status bits, tied-off debug taps); dozens of them
# alongside an explicit "does not work" is the SHA3 signature.
MATERIAL_UNDRIVEN = 8


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def find_status_root(start: Path | None, *, max_up: int = 4) -> Path | None:
    """Nearest ancestor of `start` that carries a project status file.

    A candidate is not always a clone directly under the downloads root — a
    `local_tree` source has no such ancestor at all, and a cloned repo's RTL
    usually sits a few directories below its README. Searching upward a bounded
    number of levels finds the declaration in both shapes without wandering into
    an unrelated parent project.
    """
    if start is None:
        return None
    cur = Path(start)
    if cur.is_file():
        cur = cur.parent
    for _ in range(max_up + 1):
        if any((cur / n).is_file() for n in STATUS_FILES):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def repository_health(repo_dir: Path | None) -> dict:
    """Scan the project's own status declaration. Records file, line and digest so
    the decision can be re-checked against the exact bytes that produced it."""
    out: dict = {"checked": False, "strong_negative": False, "file": None,
                 "line": None, "match": None, "digest": None}
    if repo_dir is None or not Path(repo_dir).is_dir():
        return out
    for name in STATUS_FILES:
        p = Path(repo_dir) / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out["checked"] = True
        out["file"] = str(p)
        out["digest"] = _digest(text)
        m = STRONG_NEGATIVE_RE.search(text)
        if m:
            out["strong_negative"] = True
            out["line"] = text[:m.start()].count("\n") + 1
            out["match"] = m.group(0).strip()[:120]
        return out
    return out


def structural_integrity(synth_log: str | None,
                         declared_dependencies: set[str] | None = None) -> dict:
    """Structural completeness evidence from the synthesis log.

    `declared_dependencies` are module names the candidate KNOWS it depends on
    (e.g. a hard-macro stub); a blackbox for one of those is expected, not a
    defect.
    """
    declared = {d.lower() for d in (declared_dependencies or set())}
    out: dict = {"checked": False, "undriven_wires": [], "undriven_count": 0,
                 "unresolved_modules": [], "driver_conflicts": 0,
                 "material": False}
    if not synth_log:
        return out
    out["checked"] = True
    undriven = [w for w in UNDRIVEN_RE.findall(synth_log)]
    out["undriven_wires"] = sorted(set(undriven))[:20]
    out["undriven_count"] = len(undriven)
    unresolved = [m for m in UNRESOLVED_MODULE_RE.findall(synth_log)
                  if m.strip("\\").lower() not in declared]
    out["unresolved_modules"] = sorted(set(unresolved))[:20]
    out["driver_conflicts"] = len(DRIVER_CONFLICT_RE.findall(synth_log))
    out["material"] = bool(
        out["undriven_count"] >= MATERIAL_UNDRIVEN
        or out["unresolved_modules"]
        or out["driver_conflicts"])
    return out


def functional_evidence(repo_dir: Path | None) -> dict:
    """Whether the repository supplies bounded functional evidence.

    We only RECORD availability here. Absence is `not_available` — never a
    fabricated pass, and never on its own a reason to reject.
    """
    out = {"status": "not_available", "evidence": None}
    if repo_dir is None or not Path(repo_dir).is_dir():
        return out
    for pattern in ("**/Makefile", "**/*_tb.v", "**/*_tb.sv", "**/tb_*.v", "**/*.core"):
        hit = next(iter(Path(repo_dir).glob(pattern)), None)
        if hit is not None:
            out["status"] = "available_not_run"
            out["evidence"] = str(hit)
            break
    return out


def assess(*, repo_dir: Path | None = None, synth_log: str | None = None,
           declared_dependencies: set[str] | None = None) -> dict:
    """Combined readiness verdict + all supporting evidence.

    Truth table (deliberately conservative in both directions):

      strong negative + material structural warnings -> rejected
      strong negative alone                          -> manual_review
      material structural warnings alone             -> manual_review
      neither                                        -> ready
    """
    health = repository_health(repo_dir)
    structure = structural_integrity(synth_log, declared_dependencies)
    functional = functional_evidence(repo_dir)

    if health["strong_negative"] and structure["material"]:
        verdict, reason = REJECTED, (
            "the repository declares itself unusable AND synthesis reports material "
            "structural incompleteness")
    elif health["strong_negative"]:
        verdict, reason = MANUAL_REVIEW, (
            "the repository declares itself unusable, but synthesis found no material "
            "structural defect — a human must decide")
    elif structure["material"]:
        verdict, reason = MANUAL_REVIEW, (
            "synthesis reports material structural incompleteness "
            f"(undriven={structure['undriven_count']}, "
            f"unresolved={structure['unresolved_modules'][:3]}, "
            f"conflicts={structure['driver_conflicts']})")
    else:
        verdict, reason = READY, "no semantic-readiness defect found"

    return {
        "rtl_readiness": verdict,
        "reason": reason,
        "repository_health": health,
        "structural_integrity": structure,
        "functional_evidence": functional,
    }


def blocks_promotion(assessment: dict) -> bool:
    """Only `ready` may enter NORMAL autonomous promotion."""
    return assessment.get("rtl_readiness") != READY
