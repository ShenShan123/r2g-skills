"""ann-v2 regression snapshot as a pre-action-run source (user decision, 2026-10-17: adopt,
but only when uniquely owned).

`reports/global_regression_it<N>.json` is written at the regression-revert site of
fix_signoff.sh. Its `pre` block is the result vector captured BEFORE the step: the newest
backend run by mtime (`pre.layout.run_tag`), that run's 6_final.def sha256, and the signals
with their freshness. One file per iteration NUMBER per project, so a later session
overwrites an earlier one. It names the pre-action run only when ALL of these hold:

  S1 attributable: exactly one recorded attempt row (copy roots deduplicated) has verdict
     `regression`, the same iteration, the same strategy and the same `global_regressions`
     list as the snapshot -- and it is this attempt (a same-iteration row is not enough;
     an overwritten file or a cross-session repeat leaves several or the wrong one);
  S2 no copy conflict: every copy root holding the file holds the same bytes;
  S3 not stale: the target check's signal is `fresh` in `pre` (its report is bound to the
     named run; after an earlier rollback the newest run can be the rejected one, and the
     restored report then reads not fresh);
  S4 verifiable: the named run exists, its 6_final.def sha256 equals `pre.layout.def_digest`,
     and it recorded the target failure;
  S5 not contradicted: when the post-action run is bound independently, the snapshot's
     `post.layout.run_tag` names the same run; and the snapshot was captured no later than
     the row was written (a timestamp may only contradict, never decide).

The post-action binding and the rollback judgement stay independent: the snapshot never
binds a post run, and its existence shows a rollback (`config_restored: true`) but never
"not rolled back".
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

SIGNAL = {"drc": "drc", "lvs": "lvs", "timing": "timing", "orfs_stage": "orfs", "route": "route"}


def _jsonl(path: Path) -> list:
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _sha(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _def_sha(run_dir: Path) -> str | None:
    for rel in ("results/6_final.def", "final/6_final.def"):
        if (run_dir / rel).exists():
            return _sha(run_dir / rel)
    return None


def _ts(s):
    try:
        return _dt.datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def _regs(x) -> list:
    return sorted(x) if isinstance(x, list) else []


def load_owned(row: dict, projects) -> dict:
    """S1 + S2: {status: OWNED|UNKNOWN|ABSENT, snap, reason, sources}."""
    it = int(row.get("iter") or 1)
    files = [Path(p) / "reports" / f"global_regression_it{it}.json" for p in projects]
    files = [f for f in files if f.exists()]
    if not files:
        return {"status": "ABSENT", "snap": None, "reason": "not_recorded", "sources": []}
    srcs = [{"path": str(f), "locator": "config_restored, iter, strategy, regressions (unique owner row)",
             "sha256": _sha(f)} for f in files]
    if len({s["sha256"] for s in srcs}) > 1:                                         # S2
        return {"status": "UNKNOWN", "snap": None, "reason": "copy_conflict", "sources": srcs}
    try:
        snap = json.loads(files[0].read_text())
    except (OSError, ValueError):
        return {"status": "UNKNOWN", "snap": None, "reason": "metric_absent", "sources": srcs}
    if not isinstance(snap, dict) or snap.get("config_restored") is not True \
            or snap.get("strategy") != row.get("strategy") or int(snap.get("iter") or 0) != it:
        return {"status": "UNKNOWN", "snap": None, "reason": "no_explicit_link", "sources": srcs}
    owners = set()                                                                    # S1
    for p in projects:
        for r in _jsonl(Path(p) / "reports" / "fix_log.jsonl"):
            if r.get("verdict") == "regression" and int(r.get("iter") or 1) == it \
                    and r.get("strategy") == snap.get("strategy") \
                    and _regs(r.get("global_regressions")) == _regs(snap.get("regressions")):
                owners.add(json.dumps(r, sort_keys=True))
    if owners != {json.dumps(row, sort_keys=True)}:
        return {"status": "UNKNOWN", "snap": None, "sources": srcs,
                "reason": "ambiguous_binding" if len(owners) > 1 else "no_explicit_link"}
    return {"status": "OWNED", "snap": snap, "reason": None, "sources": srcs}


def snapshot_pre(row: dict, projects, run_dirs: dict, post_run: str | None, failure_recorded,
                 conflicted=frozenset()) -> dict:
    """{status: KNOWN|UNKNOWN|ABSENT, run, reason, evidence, sources}.

    run_dirs: {run_tag: first path}; failure_recorded(run_tag) -> True/False/None."""
    own = load_owned(row, projects)
    srcs = own["sources"]
    if own["status"] != "OWNED":
        return {"status": own["status"] if own["status"] == "ABSENT" else "UNKNOWN", "run": None,
                "reason": own["reason"], "evidence": None, "sources": srcs}
    snap = own["snap"]

    def unknown(reason, detail=None):
        return {"status": "UNKNOWN", "run": None, "reason": reason, "detail": detail or reason,
                "evidence": None, "sources": srcs}
    pre = snap.get("pre") or {}
    layout = pre.get("layout") or {}
    tag = layout.get("run_tag")
    sig = (pre.get("signals") or {}).get(SIGNAL.get(row.get("check"), ""), {}) or {}
    if not tag:
        return unknown("no_explicit_link")
    if sig.get("fresh") is not True:                                                  # S3
        return unknown("conflicting_sources", "S3_target_signal_not_fresh")
    if tag not in run_dirs:                                                           # S4
        return unknown("run_missing_on_disk")
    if tag in conflicted:
        return unknown("copy_conflict")
    if not layout.get("def_digest") or _def_sha(Path(run_dirs[tag])) != layout.get("def_digest"):
        return unknown("conflicting_sources", "S4_def_digest_mismatch")
    if failure_recorded(tag) is not True:
        return unknown("conflicting_sources", "S4_failure_not_recorded")
    post_tag = ((snap.get("post") or {}).get("layout") or {}).get("run_tag")          # S5
    if post_run is not None and post_tag != post_run:
        return unknown("contradicted_binding", "S5_post_run_disagrees")
    a, b = _ts(pre.get("captured_at")), _ts(row.get("ts"))
    if a is not None and b is not None and a > b:
        return unknown("contradicted_binding", "S5_captured_after_row")
    return {"status": "KNOWN", "run": tag, "reason": None, "sources": srcs,
            "evidence": {"snapshot": srcs[0]["path"], "snapshot_sha256": srcs[0]["sha256"], "run_tag": tag,
                         "def_digest": layout.get("def_digest"), "target_signal_fresh": True,
                         "owner_rows": 1, "post_run_agrees": post_run is not None}}


def merge_pre(pre: dict, snap: dict, run_paths: dict) -> dict:
    """Combine the spec §4 pre binding with a snapshot result. A disagreement is never
    resolved by choosing: both are recorded and the binding is UNKNOWN."""
    if snap["status"] != "KNOWN":
        if snap["status"] == "UNKNOWN":
            pre = {**pre, "snapshot": {"status": "UNKNOWN", "reason": snap["reason"],
                                       "detail": snap.get("detail") or snap["reason"]}}
        return pre
    if pre["status"] == "KNOWN":
        if pre["run"] == snap["run"]:
            ev = dict(pre.get("evidence") or {})
            ev["snapshot"] = snap["evidence"]
            return {**pre, "evidence": ev}
        return {"status": "UNKNOWN", "run": None, "unknown_reason": "contradicted_binding",
                "candidates": [{"run": pre["run"], "via": "spec_4"}, {"run": snap["run"], "via": "snapshot"}],
                "evidence": None}
    if pre.get("unknown_reason") in ("contradicted_binding", "copy_conflict", "run_missing_on_disk"):
        return {**pre, "snapshot": {"status": "KNOWN_BUT_NOT_USED", "run": snap["run"]}}
    if pre.get("unknown_reason") == "ambiguous_binding":
        cands = {c.get("run") for c in pre.get("candidates") or []}
        if cands and snap["run"] not in cands:
            return {**pre, "unknown_reason": "contradicted_binding",
                    "candidates": list(pre.get("candidates") or []) + [{"run": snap["run"], "via": "snapshot"}]}
    return {"status": "KNOWN", "run": snap["run"], "unknown_reason": None,
            "candidates": [{"run": snap["run"], "via": "snapshot"}],
            "evidence": {"e1": {"snapshot": snap["evidence"], "paths": [run_paths.get(snap["run"])]}, "e2": {
                "failure": "consistent", "def_digest": "consistent", "target_signal_fresh": "consistent"}}}
