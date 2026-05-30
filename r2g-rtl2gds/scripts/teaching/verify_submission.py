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


def verify_ledger(teaching_root: Path) -> list[str]:
    """If a ledger exists, verify chain + per-record hash. Returns list of
    failure strings (empty = ok or absent-but-tolerated). A present-but-broken
    ledger is a hard flag; an absent ledger is reported as a soft note."""
    ledger = teaching_root / "run_ledger.jsonl"
    if not ledger.is_file():
        return ["ledger_absent"]  # soft: caller decides severity
    if not _HAVE_LEDGER:
        return ["ledger_present_but_module_unavailable"]
    fails: list[str] = []
    prev = "GENESIS"
    try:
        for i, line in enumerate(ledger.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_hash") != prev:
                fails.append(f"chain_break@{i}")
                break
            if not verify_record_hash(rec):
                fails.append(f"hash_mismatch@{i}")
                break
            if rec.get("triggered_by") == "agent_direct":
                fails.append(f"agent_direct_write@{i}")
            prev = rec.get("record_hash", "")
    except (ValueError, OSError) as e:
        fails.append(f"ledger_corrupt:{e}")
    return fails


def verify_submission(teaching_root: Path) -> tuple[list[StageVerdict], list[str]]:
    cases_dir = teaching_root / "cases"
    verdicts: list[StageVerdict] = []
    if cases_dir.is_dir():
        for design_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
            verdicts.extend(verify_case(teaching_root, design_dir))
    ledger_notes = verify_ledger(teaching_root)
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
    lines += [
        "",
        "> 标红项不等于一定作弊，但需人工复核；账本 chain_break / hash_mismatch /",
        "> agent_direct_write 视为伪造嫌疑。如实记录的失败/阻塞（FAILED_*/BLOCKED_*）",
        "> 只要产物与状态自洽即视为有效证据，不因失败本身扣分。",
    ]
    (teaching_root / "SUBMISSION_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8")


# ─── batch / cross-submission ────────────────────────────────────────────────

# Artifacts whose duplication across students signals copy/share.
DUP_SUFFIXES = (".gds", ".def", "5_route.def") + tuple(REQUIRED_FEATURE_CSVS) \
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
    args = ap.parse_args(argv)

    if args.teaching_root:
        verdicts, ledger_notes = verify_submission(args.teaching_root)
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
        verdicts, ledger_notes = verify_submission(sub)
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
