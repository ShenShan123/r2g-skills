#!/usr/bin/env python3
"""Verify a teaching submission against TEACHING_POLICY.md.

This is the *enforcement point*. It does not trust prose: it recomputes
everything it can from the artifacts on disk. A submission that cannot be
verified is graded ``INSUFFICIENT_EVIDENCE`` rather than trusted.

Two modes:

  Single submission (one student's teaching_root):
      python3 verify_submission.py --teaching-root path/to/teaching_root

  Batch (a directory containing many teaching_roots, one per student):
      python3 verify_submission.py --batch path/to/all_submissions

Single mode writes, under the teaching_root:
  - status_summary.csv     one row per (design, stage)
  - SUBMISSION_REPORT.md    human-readable verdict
and prints a short summary.

Batch mode additionally runs cross-submission duplicate detection on the big
artifacts (GDS / DEF / routed DEF / the 12 Stage-4 CSVs) and writes
  - <batch>/COLLISIONS.csv
flagging any artifact hash shared by more than one student (copy/share).

The check categories per (design, stage):
  1. status enum validity            (status must be in the policy's set)
  2. report file exists              (the report referenced by CASE_STATE)
  3. PASS needs evidence             (a *_PASS must have its artifacts present
                                      and non-trivial; Stage 4 CSVs checked)
  4. no machine-absolute paths       (落盘记录 must be relative — policy §3)
  5. platform lock                   (CASE_STATE platform == nangate45 — red line 3)
  6. ledger (optional)               (if run_ledger.jsonl present: chain + hash)

Exit code 0 always (grading is data, not a process failure); read the CSV/MD.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "ledger"))

from status_enums import (  # noqa: E402
    FORBIDDEN_PATH_SUBSTRINGS,
    PASS_STATUSES,
    PLATFORM_LOCK,
    REQUIRED_FEATURE_CSVS,
    REQUIRED_LABEL_CSVS,
    STAGE_STATUS_SETS,
    UNKNOWN_CELL_TYPE_ID,
    UNKNOWN_SHARE_WARN_THRESHOLD,
    stage_status_valid,
)

# Ledger verification is optional — only used if a ledger is present AND the
# ledger module is importable. We degrade gracefully if not.
try:
    from canonical import verify_record_hash  # type: ignore
    _HAVE_LEDGER = True
except Exception:  # noqa: BLE001
    _HAVE_LEDGER = False


# ─── data ────────────────────────────────────────────────────────────────────

@dataclass
class StageVerdict:
    design: str
    stage: str
    final_status: str
    error_tags: str = "NONE"
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.checks_failed

    def row(self) -> dict:
        return {
            "design_name": self.design,
            "stage": self.stage,
            "final_status": self.final_status,
            "error_tags": self.error_tags,
            "verify_result": "OK" if self.ok else "FLAGGED",
            "failed_checks": ";".join(self.checks_failed) or "NONE",
        }


# ─── small parsers ───────────────────────────────────────────────────────────

def parse_case_state(path: Path) -> dict[str, str]:
    """Parse the simple ``key: value`` lines of CASE_STATE.md. Lines starting
    with '#' and blank lines are ignored. Last value wins."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip()
    return out


def file_nonempty(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def csv_rowcount(p: Path) -> Optional[int]:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f)
        return max(0, n - 1)  # minus header
    except OSError:
        return None


def sha256_file(p: Path, chunk: int = 1 << 20) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def _detect_repo_root(teaching_root: Path) -> Optional[Path]:
    """Search upward from *teaching_root* for an ancestor directory that
    contains ``r2g-rtl2gds/`` as an immediate subdirectory.  Returns the
    ancestor (the *parent* of r2g-rtl2gds), or ``None``."""
    d = teaching_root.resolve()
    while True:
        if (d / "r2g-rtl2gds").is_dir():
            return d
        parent = d.parent
        if parent == d:          # reached filesystem root
            return None
        d = parent


def _resolve_ledger_path(
    teaching_root: Path,
    repo_root: Optional[Path],
    rel: str,
) -> Optional[Path]:
    """Resolve a ledger-normalised path key back to an absolute :class:`Path`.

    Ledger ``inputs`` / ``outputs`` keys use two anchors:
      ``<teaching_root>/...``  and  ``<repo>/...``
    (see ``append_ledger.py``).  Returns ``None`` when an anchor cannot be
    resolved — e.g. ``<repo>`` without a known *repo_root*.
    """
    rel = rel.strip()
    if not rel:
        return None
    if rel.startswith("<teaching_root>/"):
        return teaching_root / rel[len("<teaching_root>/"):]
    if rel.startswith("<repo>/"):
        if repo_root is None:
            return None
        return repo_root / rel[len("<repo>/"):]
    # bare absolute path (fallback for legacy records)
    p = Path(rel)
    return p if p.is_absolute() else None


def text_has_forbidden_path(text: str) -> list[str]:
    """Return the forbidden absolute-path substrings found in text."""
    hits = []
    for sub in FORBIDDEN_PATH_SUBSTRINGS:
        if sub in text:
            hits.append(sub)
    return sorted(set(hits))


# ─── single-submission verification ─────────────────────────────────────────

def verify_case(teaching_root: Path, design_dir: Path) -> list[StageVerdict]:
    design = design_dir.name
    cs = parse_case_state(design_dir / "CASE_STATE.md")
    verdicts: list[StageVerdict] = []

    # red line 3: platform lock (checked once, attributed to all stages' notes)
    platform_ok = (cs.get("platform", "").strip() == PLATFORM_LOCK)

    for stage in ("stage1", "stage2", "stage3", "stage4"):
        status = cs.get(f"{stage}_status", "").strip()
        if not status:
            continue  # stage not attempted — skip silently
        v = StageVerdict(design=design, stage=stage, final_status=status)

        # 1. enum validity
        if stage_status_valid(stage, status):
            v.checks_passed.append("enum_valid")
        else:
            v.checks_failed.append(f"bad_status:{status}")

        # 2. report exists
        report_rel = cs.get(f"{stage}_report", "").strip()
        if report_rel:
            report_path = _resolve(teaching_root, report_rel)
            if report_path and report_path.is_file():
                v.checks_passed.append("report_exists")
                # 4. no machine-absolute paths inside the report
                txt = report_path.read_text(encoding="utf-8", errors="replace")
                hits = text_has_forbidden_path(txt)
                if hits:
                    v.checks_failed.append("abs_path_in_report:" + ",".join(hits))
                else:
                    v.checks_passed.append("report_paths_relative")
            else:
                v.checks_failed.append("report_missing")
        else:
            # a PASS with no report path is suspicious; a failure/blocked may
            # legitimately have a brief report — flag only if claiming PASS
            if status in PASS_STATUSES:
                v.checks_failed.append("pass_without_report")

        # 5. platform lock
        if platform_ok:
            v.checks_passed.append("platform_locked_nangate45")
        else:
            v.checks_failed.append(
                f"platform_not_nangate45:{cs.get('platform','<unset>')}")

        # 3. PASS needs evidence — Stage 4 CSV presence is the concrete check
        if stage == "stage4" and status in PASS_STATUSES:
            _check_stage4_artifacts(teaching_root, design_dir, cs, v)

        verdicts.append(v)

    # 4 (also): scan CASE_STATE.md itself for absolute paths
    cs_text = (design_dir / "CASE_STATE.md").read_text(
        encoding="utf-8", errors="replace") if (design_dir / "CASE_STATE.md").is_file() else ""
    abs_hits = text_has_forbidden_path(cs_text)
    if abs_hits and verdicts:
        verdicts[-1].checks_failed.append(
            "abs_path_in_case_state:" + ",".join(abs_hits))

    return verdicts


# Mapping from CASE_STATE key to teaching_root subdir for the
# case_state_path_should_be_teaching_root soft check.
_CASE_STATE_ARTIFACT_SUBDIR = {
    "gds_path": "stage2_orfs",
    "def_path": "stage2_orfs",
    "odb_path": "stage2_orfs",
    "spef_path": "stage3_drc_lvs_rcx",
}


def _check_case_state_paths(teaching_root: Path, design_dir: Path) -> list[str]:
    """Soft-check: if CASE_STATE artifact paths still use ``<repo>/...`` but
    the corresponding file already exists under *teaching_root*, emit a soft
    note so the mismatch does not silently escape detection.

    This does NOT block submission — the ``<repo>/...`` path may still be
    valid.  The note is a hint that the path should be updated to
    ``<teaching_root>/...`` so the artifact participates in re-hash and
    cross-commit dedup.
    """
    cs = parse_case_state(design_dir / "CASE_STATE.md")
    notes: list[str] = []
    for key, subdir in _CASE_STATE_ARTIFACT_SUBDIR.items():
        val = cs.get(key, "").strip()
        if not val or not val.startswith("<repo>/"):
            continue
        basename = val.rsplit("/", 1)[-1]
        if not basename:
            continue
        expected = design_dir / subdir / basename
        if expected.is_file():
            notes.append(f"case_state_path_should_be_teaching_root:{key}")
    return notes


def _ledger_output_paths(teaching_root: Path) -> set[str]:
    """Return every normalized output path key recorded by the run ledger.

    This intentionally checks membership only.  Hash verification remains in
    ``verify_ledger``; this helper detects submitted artifacts that are present
    on disk but have no ledger output record at all.
    """
    ledger = teaching_root / "run_ledger.jsonl"
    if not ledger.is_file():
        return set()
    out: set[str] = set()
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            outputs = rec.get("outputs", {})
            if not isinstance(outputs, dict):
                continue
            for relpath, claimed_hash in outputs.items():
                if isinstance(relpath, str) and isinstance(claimed_hash, str):
                    out.add(relpath)
    except OSError:
        return set()
    return out


def _check_artifacts_in_ledger(
    teaching_root: Path,
    design_dir: Path,
    ledger_outputs: set[str],
) -> list[str]:
    """Soft-review check for artifacts present in the submission but absent
    from ledger outputs.

    These notes do not prove forgery: older legal flows may have copied
    artifacts before ledger support existed.  They do mean the artifact is not
    covered by ledger re-hash tamper detection and needs teacher review.
    """
    cs = parse_case_state(design_dir / "CASE_STATE.md")
    candidates: list[str] = []

    for key in _CASE_STATE_ARTIFACT_SUBDIR:
        rel = cs.get(key, "").strip()
        if rel.startswith("<teaching_root>/"):
            candidates.append(rel)

    design = design_dir.name
    for csv_name in REQUIRED_LABEL_CSVS:
        candidates.append(
            f"<teaching_root>/cases/{design}/stage4_labels/{csv_name}")
    for csv_name in REQUIRED_FEATURE_CSVS:
        candidates.append(
            f"<teaching_root>/cases/{design}/stage4_features/{csv_name}")

    notes: list[str] = []
    seen: set[str] = set()
    for rel in candidates:
        if rel in seen:
            continue
        seen.add(rel)
        abs_path = _resolve_ledger_path(teaching_root, None, rel)
        if abs_path is None or not abs_path.is_file():
            continue
        if rel not in ledger_outputs:
            notes.append(f"artifact_not_in_ledger:{rel}")
    return notes


def _check_stage4_artifacts(teaching_root, design_dir, cs, v: StageVerdict):
    labels_dir = design_dir / "stage4_labels"
    feats_dir = design_dir / "stage4_features"

    missing_labels = [c for c in REQUIRED_LABEL_CSVS
                      if not file_nonempty(labels_dir / c)]
    missing_feats = [c for c in REQUIRED_FEATURE_CSVS
                     if not file_nonempty(feats_dir / c)]

    # For a full STAGE4_EXTRACTION_PASS we need all 12 present & non-empty.
    if v.final_status == "STAGE4_EXTRACTION_PASS":
        if missing_labels:
            v.checks_failed.append("missing_label_csv:" + ",".join(missing_labels))
        if missing_feats:
            v.checks_failed.append("missing_feature_csv:" + ",".join(missing_feats))
        if not missing_labels and not missing_feats:
            v.checks_passed.append("all_12_csv_present")
    elif v.final_status == "STAGE4_LABELS_PASS_FEATURES_FAILED":
        if missing_labels:
            v.checks_failed.append("missing_label_csv:" + ",".join(missing_labels))
        else:
            v.checks_passed.append("4_label_csv_present")
    elif v.final_status == "STAGE4_FEATURES_PASS_LABELS_FAILED":
        if missing_feats:
            v.checks_failed.append("missing_feature_csv:" + ",".join(missing_feats))
        else:
            v.checks_passed.append("8_feature_csv_present")

    # UNKNOWN cell_type_id share sanity (wrong platform / missing lib signal)
    ng = feats_dir / "nodes_gate.csv"
    share = _unknown_cell_type_share(ng)
    if share is not None and share > UNKNOWN_SHARE_WARN_THRESHOLD:
        v.checks_failed.append(f"high_unknown_cell_type:{share:.0%}")


def _unknown_cell_type_share(nodes_gate_csv: Path) -> Optional[float]:
    if not file_nonempty(nodes_gate_csv):
        return None
    try:
        with nodes_gate_csv.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "cell_type_id" not in reader.fieldnames:
                return None
            total = unknown = 0
            for r in reader:
                total += 1
                if str(r.get("cell_type_id", "")).strip() == UNKNOWN_CELL_TYPE_ID:
                    unknown += 1
        return (unknown / total) if total else None
    except OSError:
        return None


def _resolve(teaching_root: Path, rel: str) -> Optional[Path]:
    """Resolve a placeholder/relative path recorded in artifacts to a real path
    under the submission. We only resolve <teaching_root>-anchored or plainly
    relative paths; <repo>-anchored paths point outside the submission and are
    treated as 'exists unknown' (skipped)."""
    rel = rel.strip()
    if not rel:
        return None
    if rel.startswith("<teaching_root>/"):
        return teaching_root / rel[len("<teaching_root>/"):]
    if rel.startswith("<repo>") or rel.startswith("<feature") or rel.startswith("<label"):
        return None  # outside the submitted teaching_root; can't verify here
    # plain relative -> assume relative to teaching_root
    p = (teaching_root / rel)
    return p


def verify_ledger(
    teaching_root: Path,
    repo_root: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """Verify the ledger chain, record-level hashes, and output-artifact
    integrity.

    Returns ``(hard_fails, soft_notes)``:

    *hard_fails* — record-chain breaks, record-hash mismatches,
    agent-direct writes, and **output-artifact hash mismatches / missing
    files** (after the path anchor was successfully resolved).

    *soft_notes* — absent ledger, unresolvable repo_root, unresolvable
    path anchors, and any input-artifact mismatches.
    """
    ledger = teaching_root / "run_ledger.jsonl"
    if not ledger.is_file():
        return [], ["ledger_absent"]
    if not _HAVE_LEDGER:
        return [], ["ledger_present_but_module_unavailable"]
    fails: list[str] = []
    notes: list[str] = []
    prev = "GENESIS"
    _repo_unresolved_noted = False

    # -- first pass: find latest occurrence of each output artifact path -----
    # When a fixed-location artifact (e.g. stage2_orfs/6_final.gds) is
    # overwritten by a re-run, only the LAST record's hash should be hard-
    # verified; older records' hashes for that path are soft-skipped.
    _latest_for_output: dict[str, int] = {}
    _all_lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    for _i, _line in enumerate(_all_lines):
        try:
            _rec = json.loads(_line)
        except (ValueError, TypeError):
            continue
        for _relpath, _h in _rec.get("outputs", {}).items():
            if isinstance(_h, str) and len(_h) == 64:
                _latest_for_output[_relpath] = _i

    try:
        for i, line in enumerate(_all_lines):
            rec = json.loads(line)

            # -- record-level integrity (unchanged) ------------------------
            claimed_prev = rec.get("prev_hash")
            if not isinstance(claimed_prev, str) or claimed_prev != prev:
                fails.append(f"chain_break@{i}")
                break
            if not verify_record_hash(rec):
                fails.append(f"hash_mismatch@{i}")
                break
            if rec.get("triggered_by") == "agent_direct":
                fails.append(f"agent_direct_write@{i}")
            prev = rec.get("record_hash", "")

            # -- output-artifact re-hash (HARD) ----------------------------
            for relpath, claimed_hash in rec.get("outputs", {}).items():
                if not isinstance(claimed_hash, str) or len(claimed_hash) != 64:
                    continue
                # If a later record overwrote this artifact path, skip hard
                # verification for this older record (soft note only).
                if _latest_for_output.get(relpath, i) > i:
                    notes.append(f"output_superseded@{i}:{relpath}")
                    continue
                abs_path = _resolve_ledger_path(teaching_root, repo_root, relpath)
                if abs_path is None:
                    if relpath.startswith("<repo>/") and repo_root is None:
                        if not _repo_unresolved_noted:
                            notes.append("repo_root_unresolved:skip_repo_outputs")
                            _repo_unresolved_noted = True
                    else:
                        notes.append(f"unresolvable_output_path@{i}:{relpath}")
                    continue
                if not abs_path.is_file():
                    fails.append(f"artifact_missing@{i}:{relpath}")
                    continue
                actual = sha256_file(abs_path)
                if actual is None:
                    fails.append(f"artifact_unreadable@{i}:{relpath}")
                elif actual != claimed_hash:
                    fails.append(f"artifact_hash_mismatch@{i}:{relpath}")

            # -- input-artifact re-hash (SOFT only) ------------------------
            for relpath, claimed_hash in rec.get("inputs", {}).items():
                if not isinstance(claimed_hash, str) or len(claimed_hash) != 64:
                    continue
                abs_path = _resolve_ledger_path(teaching_root, repo_root, relpath)
                if abs_path is None:
                    continue
                if not abs_path.is_file():
                    notes.append(f"input_missing@{i}:{relpath}")
                    continue
                actual = sha256_file(abs_path)
                if actual is None:
                    notes.append(f"input_unreadable@{i}:{relpath}")
                elif actual != claimed_hash:
                    notes.append(f"input_hash_mismatch@{i}:{relpath}")

    except (ValueError, OSError) as e:
        fails.append(f"ledger_corrupt:{e}")
    return fails, notes


def verify_submission(
    teaching_root: Path,
    repo_root: Optional[Path] = None,
) -> tuple[list[StageVerdict], list[str]]:
    """Verify one submission.  *repo_root* is passed through to
    :func:`verify_ledger` for output-artifact re-hashing."""
    cases_dir = teaching_root / "cases"
    verdicts: list[StageVerdict] = []
    cs_notes: list[str] = []
    ledger_outputs = _ledger_output_paths(teaching_root)
    if cases_dir.is_dir():
        for design_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
            verdicts.extend(verify_case(teaching_root, design_dir))
            cs_notes.extend(_check_case_state_paths(teaching_root, design_dir))
            cs_notes.extend(_check_artifacts_in_ledger(
                teaching_root, design_dir, ledger_outputs))
    hard_fails, soft_notes = verify_ledger(teaching_root, repo_root)
    # Merge: hard_fails first so they appear prominently in the report.
    ledger_notes = hard_fails + soft_notes + cs_notes
    return verdicts, ledger_notes


def write_outputs(teaching_root: Path, verdicts: list[StageVerdict],
                  ledger_notes: list[str]) -> None:
    # status_summary.csv
    csv_path = teaching_root / "status_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "design_name", "stage", "final_status", "error_tags",
            "verify_result", "failed_checks"])
        w.writeheader()
        for v in verdicts:
            w.writerow(v.row())

    # SUBMISSION_REPORT.md
    n_ok = sum(1 for v in verdicts if v.ok)
    n_flag = len(verdicts) - n_ok
    artifact_not_in_ledger = [
        n for n in ledger_notes if n.startswith("artifact_not_in_ledger:")
    ]
    lines = [
        "# 提交校验报告（SUBMISSION_REPORT）",
        "",
        f"- 校验条目（design×stage）：{len(verdicts)}",
        f"- 通过：{n_ok}    标红：{n_flag}",
        f"- 账本：{'、'.join(ledger_notes) if ledger_notes else 'OK'}",
        "",
        "| design | stage | final_status | 校验 | 失败项 |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        lines.append(
            f"| {v.design} | {v.stage} | {v.final_status} | "
            f"{'OK' if v.ok else '标红'} | {';'.join(v.checks_failed) or '—'} |")
    if artifact_not_in_ledger:
        lines += [
            "",
            "## 产物未纳入账本（需人工复核）",
            "",
            "以下产物存在于提交目录，但未在 run_ledger.jsonl 的 outputs 中找到记录；"
            "这不自动判定为伪造，但说明账本重哈希防篡改未覆盖这些产物。",
            "",
            "| design | artifact |",
            "|---|---|",
        ]
        for note in artifact_not_in_ledger:
            rel = note.split(":", 1)[1]
            lines.append(f"| {_design_from_teaching_path(rel)} | {rel} |")
    lines += [
        "",
        "> 标红项不等于一定作弊，但需人工复核；账本 chain_break / hash_mismatch /",
        "> agent_direct_write 视为伪造嫌疑。如实记录的失败/阻塞（FAILED_*/BLOCKED_*）",
        "> 只要产物与状态自洽即视为有效证据，不因失败本身扣分。",
    ]
    (teaching_root / "SUBMISSION_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8")


def _design_from_teaching_path(rel: str) -> str:
    marker = "<teaching_root>/cases/"
    if not rel.startswith(marker):
        return "UNKNOWN"
    rest = rel[len(marker):]
    return rest.split("/", 1)[0] if "/" in rest else "UNKNOWN"


# ─── batch / cross-submission ────────────────────────────────────────────────

# Artifacts whose duplication across students signals copy/share.
DUP_SUFFIXES = (".gds", ".def", ".odb", ".spef", "5_route.def") + tuple(REQUIRED_FEATURE_CSVS) \
    + tuple(REQUIRED_LABEL_CSVS)

# Files smaller than this are too trivial to fingerprint meaningfully — an
# empty or header-only CSV would collide across every student and produce
# false alarms. Real GDS/DEF and non-trivial feature CSVs are far larger.
MIN_DUP_BYTES = 256


def cross_submission_audit(batch_dir: Path) -> list[dict]:
    """Hash candidate artifacts across all submissions; report collisions where
    the same content hash appears in >1 distinct submission.

    Files below MIN_DUP_BYTES are skipped to avoid false positives on tiny or
    header-only outputs."""
    fingerprints: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for sub in sorted(p for p in batch_dir.iterdir() if p.is_dir()):
        cases = sub / "cases"
        if not cases.is_dir():
            continue
        for f in cases.rglob("*"):
            if not f.is_file():
                continue
            if not (f.name.endswith(DUP_SUFFIXES) or f.name in DUP_SUFFIXES):
                continue
            try:
                if f.stat().st_size < MIN_DUP_BYTES:
                    continue
            except OSError:
                continue
            h = sha256_file(f)
            if h:
                fingerprints[h].append((sub.name, str(f.relative_to(batch_dir))))
    collisions = []
    for h, locs in fingerprints.items():
        distinct_subs = {sid for sid, _ in locs}
        if len(distinct_subs) > 1:
            for sid, relpath in locs:
                collisions.append({
                    "sha256": h, "submission": sid, "path": relpath,
                    "shared_with": ";".join(sorted(distinct_subs - {sid})),
                })
    return collisions


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--teaching-root", type=Path, help="one student's teaching_root")
    g.add_argument("--batch", type=Path, help="dir of many teaching_roots")
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="agent-r2g repo root for resolving <repo> paths "
                         "(default: auto-detect upward from teaching_root)")
    args = ap.parse_args(argv)

    if args.teaching_root:
        repo_root = args.repo_root
        if repo_root is None:
            repo_root = _detect_repo_root(args.teaching_root)
        verdicts, ledger_notes = verify_submission(
            args.teaching_root, repo_root=repo_root,
        )
        write_outputs(args.teaching_root, verdicts, ledger_notes)
        n_flag = sum(1 for v in verdicts if not v.ok)
        print(f"verified {len(verdicts)} stage-entries, {n_flag} flagged; "
              f"ledger: {ledger_notes or 'OK'}")
        print(f"wrote {args.teaching_root/'status_summary.csv'} and "
              f"{args.teaching_root/'SUBMISSION_REPORT.md'}")
        return 0

    # batch
    batch = args.batch
    all_rows = []
    for sub in sorted(p for p in batch.iterdir() if p.is_dir()):
        repo_root = args.repo_root
        if repo_root is None:
            repo_root = _detect_repo_root(sub)
        verdicts, ledger_notes = verify_submission(
            sub, repo_root=repo_root,
        )
        write_outputs(sub, verdicts, ledger_notes)
        for v in verdicts:
            r = v.row()
            r["submission"] = sub.name
            all_rows.append(r)
    collisions = cross_submission_audit(batch)
    coll_path = batch / "COLLISIONS.csv"
    with coll_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sha256", "submission", "path", "shared_with"])
        w.writeheader()
        for c in collisions:
            w.writerow(c)
    print(f"batch: {len(all_rows)} stage-entries across "
          f"{len(set(r['submission'] for r in all_rows))} submissions")
    print(f"cross-submission collisions: {len(collisions)} rows -> {coll_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
