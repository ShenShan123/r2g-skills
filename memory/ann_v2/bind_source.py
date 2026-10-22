#!/usr/bin/env python3
"""DEV driver: run ann-v2 binding over SOURCE-v1 attempts (read-only input; output to a
scratch directory). Not the formal annotation: the spec, annotator, evidence list and hashes
are frozen jointly before that.

  python3 -m ann_v2.bind_source --source <SOURCE-v1 dir> --out <scratch dir>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path

from ann_v2 import binding as B


def is_attempt(unit: dict) -> bool:
    raw = unit.get("raw_row") or {}
    sid = (unit.get("action") or {}).get("strategy")
    return bool(sid) and sid != "none" and not str(raw.get("verdict") or "").startswith("stop")


def projects_of(unit: dict) -> list:
    return sorted({os.path.dirname(os.path.dirname(s["path"])) for s in unit["provenance"]["sources"]
                   if s["path"].endswith("reports/fix_log.jsonl")})


def _delta_after(delta) -> dict:
    if isinstance(delta, str):
        try:
            delta = json.loads(delta)
        except ValueError:
            return {}
    return {k: (v.get("after") if isinstance(v, dict) else v) for k, v in (delta or {}).items()}


def attempt_from_row(row: dict, projects: list) -> dict:
    """`rolled_back` is tri-state: True/False only from the row's own raw marker, else None
    (the caller may resolve it from raw rollback evidence; never from the export copy)."""
    rb = row.get("rolled_back")
    return {"projects": projects, "session": row.get("fix_session_id"), "iter": int(row.get("iter") or 1),
            "strategy": row.get("strategy"), "from_stage": row.get("from_stage"), "check": row.get("check"),
            "violation_class": row.get("violation_class"),
            "config_before": row.get("config_before") or {},
            "config_delta": _delta_after(row.get("config_delta")),
            "rolled_back": rb if isinstance(rb, bool) else None,
            # chain log v1 (explicit links; absent on legacy rows)
            "chain_version": row.get("chain_version"), "action_id": row.get("action_id"),
            "pre_run_tag": row.get("pre_run_tag"), "post_run_tag": row.get("post_run_tag"),
            "_row": row}


class Binder:
    def __init__(self):
        self.runs, self.rows, self.memo = {}, {}, {}

    def _runs(self, projects):
        key = tuple(projects)
        if key not in self.runs:
            self.runs[key] = B.load_runs(projects)
        return self.runs[key]

    def _session_rows(self, projects, session):
        key = (tuple(projects), session)
        if key not in self.rows:
            out = {}
            for p in projects:
                try:
                    lines = (Path(p) / "reports/fix_log.jsonl").read_text().splitlines()
                except OSError:
                    continue
                for l in lines:
                    try:
                        r = json.loads(l)
                    except ValueError:
                        continue
                    if r.get("fix_session_id") == session:
                        out.setdefault(int(r.get("iter") or 1), {})[json.dumps(r, sort_keys=True)] = r
            self.rows[key] = {it: list(v.values()) for it, v in out.items()}    # copies deduplicated
        return self.rows[key]

    def predecessor(self, projects, session, it):
        """The unique attempt row of iteration `it`; None when there is none or several
        (one session can hold two attempts at one iteration: a failed memory step and
        its fallback). Row order or time never chooses between them."""
        rows = [r for r in self._session_rows(projects, session).get(it, [])
                if r.get("strategy") not in (None, "none") and not str(r.get("verdict") or "").startswith("stop")]
        return rows[0] if len(rows) == 1 else None

    def bind(self, att: dict):
        key = (tuple(att["projects"]), att["session"], att["iter"], att["strategy"], att.get("action_id"))
        if key in self.memo:
            return self.memo[key]
        runs = self._runs(att["projects"])
        post = B.bind_post(att, runs)
        prev = None
        if att["iter"] > 1 and not att.get("chain_version"):
            row = self.predecessor(att["projects"], att["session"], att["iter"] - 1)
            if row is not None:
                p_att = attempt_from_row(row, att["projects"])
                p_post, p_pre = self.bind(p_att)
                if p_att["rolled_back"] is None:         # raw rollback evidence beyond the row marker
                    from ann_v2 import rollback as RB
                    p_att["rolled_back"] = RB.rollback_state(
                        att["projects"], row, p_pre["run"] if p_pre["status"] == "KNOWN" else None)[0]
                prev = (p_att, p_post, p_pre)
        pre = B.bind_pre(att, runs, post, prev=prev)
        row = att.get("_row")
        if row is not None:                      # regression snapshot (snapshot.py S1-S5)
            from ann_v2 import snapshot as SN
            paths = {t: r["paths"][0] for t, r in runs.items()}
            snap = SN.snapshot_pre(row, att["projects"], paths, post["run"] if post["status"] == "KNOWN" else None,
                                   lambda t: B.failure_recorded(runs[t], att["check"], att.get("violation_class")),
                                   conflicted={t for t, r in runs.items() if r["copy_conflict"]})
            if att.get("chain_version") and pre["status"] != "KNOWN":
                snap = {**snap, "status": "ABSENT"} if snap["status"] == "KNOWN" else snap   # cross-check only
            pre = SN.merge_pre(pre, snap, paths)
        self.memo[key] = (post, pre)
        return post, pre


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    binder = Binder()
    counts = collections.Counter()
    by_type = collections.defaultdict(collections.Counter)
    with (a.out / "binding_dev.jsonl").open("w") as f:
        for line in (a.source / "source_export.jsonl").read_text().splitlines():
            u = json.loads(line)
            kind = "%s/%s" % (u["raw_row"].get("check"), u["raw_row"].get("violation_class"))
            if not is_attempt(u):
                counts["NOT_AN_ATTEMPT"] += 1
                f.write(json.dumps({"unit_id": u["unit_id"], "status": "NOT_AN_ATTEMPT"}) + "\n")
                continue
            projects = projects_of(u)
            if not projects:
                post = pre = {"status": "UNKNOWN", "run": None, "unknown_reason": "run_missing_on_disk",
                              "candidates": [], "evidence": None}
            else:
                post, pre = binder.bind(attempt_from_row(u["raw_row"], projects))
            both = post["status"] == "KNOWN" and pre["status"] == "KNOWN"
            label = "BOTH_KNOWN" if both else "post:%s/pre:%s" % (post["unknown_reason"] or "KNOWN",
                                                                 pre["unknown_reason"] or "KNOWN")
            counts[label] += 1
            by_type[kind][label] += 1
            f.write(json.dumps({"unit_id": u["unit_id"], "kind": kind, "post": post, "pre": pre},
                               sort_keys=True, default=str) + "\n")
    summary = {"dev_only": True, "counts": dict(counts), "by_type": {k: dict(v) for k, v in by_type.items()}}
    (a.out / "binding_dev_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary["counts"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
