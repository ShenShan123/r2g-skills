#!/usr/bin/env python3
"""Shared history package: ONE set of records and ONE label set for every arm (user review
2026-10-18: RECIPE, retrieval and external-memory methods must see the same successes,
failures and costs as TEHM; construction cost is tracked separately).

Inputs: SOURCE-format unit exports and their verified ann-v2 labels (labels v2). Output: the
units unchanged, each with a `shared_label`:
  version, admitted (the ONLY success definition, = ann-v2 admission), outcome_class
  (admitted_positive / harm / cleared_new_failure / improved / no_change /
  cleared_unconfirmed / None), candidate_class, signature, step_effect, new_failures,
  rolled_back, binding (post/pre status), edits (the action's effective after-values from its
  own row), cost {flows, flows_status}.
The view `dev_verification` keeps only LEARN-cluster units (contract split); `t2` keeps all.
A unit whose identity is unknown is dropped from dev_verification (cannot prove isolation).

  python3 t2_shared_history.py build --sources <export> [...] --labels <ann jsonl> [...] \
      --contract <t2_history_collection_contract.json> --view dev_verification|t2 --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tehm_v2 import evidence_index as EI  # noqa: E402

VERSION = "shared-history-v2-labels"


def sha256(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def label(rec: dict, unit: dict) -> dict:
    b = rec.get("binding") or {}
    moves = EI.row_moves(unit.get("raw_row") or {})
    rerun = bool((unit.get("raw_row") or {}).get("from_stage"))
    post_known = (b.get("post") or {}).get("status") == "KNOWN"
    rb = rec.get("rolled_back") or {}
    return {"version": VERSION,
            "admitted": (rec.get("admission") or {}).get("status") == "ADMITTED",
            "outcome_class": EI.outcome_of(rec) if rec.get("status") == "ATTEMPT" else None,
            "candidate_class": rec.get("candidate_class"),
            "signature": EI.signature_of(rec) if rec.get("status") == "ATTEMPT" else None,
            "step_effect": rec.get("step_effect"), "new_failures": rec.get("new_failures"),
            "rolled_back": {"status": rb.get("status"), "value": rb.get("value")},
            "binding": {"post": (b.get("post") or {}).get("status"), "pre": (b.get("pre") or {}).get("status")},
            "edits": {k: str(int(a) if float(a).is_integer() else a) for k, (_, a, _) in moves.items()},
            "cost": {"flows": (1 if rerun else 0) if (post_known or not rerun) else None,
                     "flows_status": "KNOWN" if (post_known or not rerun) else "UNKNOWN"}}


def _project(u: dict) -> str:
    srcs = (u.get("provenance") or {}).get("sources") or []
    return str(Path(srcs[0]["path"]).parent.parent) if srcs else "?"


def shared_text(u: dict) -> str:
    """One text view for every text-based arm, from the row and the SHARED label only (the v1
    text ended with the export's enriched outcome/rollback, which is not evidence)."""
    raw, lab, ident = u.get("raw_row") or {}, u["shared_label"], u.get("identity") or {}
    sit = raw.get("situation") or {}
    sit_txt = ", ".join(f"{k}={sit[k]}" for k in sorted(sit) if sit[k] is not None and k != "v")
    edits = json.dumps(lab.get("edits") or {}, sort_keys=True)
    eff = ", ".join(f"{k} {v}" for k, v in sorted((lab.get("step_effect") or {}).items())) or "none"
    new = sorted(k for k, v in (lab.get("new_failures") or {}).items() if v == "KNOWN-present")
    text = (f"State: design {ident.get('design')} on {ident.get('platform')}; check {raw.get('check')}, class "
            f"{raw.get('violation_class')}; situation {sit_txt}. Action: {raw.get('strategy')}, edits {edits}, rerun "
            f"from {raw.get('from_stage')}. Verified label (ann-v2): signature {lab.get('signature')}; step effect "
            f"{eff}; new failures {', '.join(new) or 'none'}; rollback {lab['rolled_back'].get('status')}"
            f"{'=' + str(lab['rolled_back'].get('value')) if lab['rolled_back'].get('status') == 'KNOWN' else ''}; "
            f"outcome {lab.get('outcome_class') or 'unbound or not an attempt'}; success "
            f"{'ADMITTED' if lab.get('admitted') else 'no'}.")
    if 'episode_context' in u:
        text += '\nRecorded NB trajectory context (not step-positive evidence): ' + json.dumps(
            u['episode_context'], sort_keys=True, ensure_ascii=False)
    return text


def normalise(units: list) -> list:
    """Every unit in the full SOURCE schema the arms read (lineage, identity.session, capture_context,
    source_eligible), and ONE regenerated text view (`text_view_v1` keeps the original)."""
    sessions = collections.defaultdict(list)
    for u in units:
        sessions[(_project(u), (u.get("raw_row") or {}).get("fix_session_id"))].append(u)
    ctx = {}
    for rows in sessions.values():
        rows = sorted(rows, key=lambda u: (int((u.get("raw_row") or {}).get("iter") or 1),
                                           str((u.get("raw_row") or {}).get("ts") or ""), u["unit_id"]))
        # LLM candidate events contain a verdict OBJECT, not a fix-log verdict
        # string. Preserve that raw object, but never interpret it as a legacy
        # session success or pass an unhashable object to the capture adapter.
        verdicts = [v if isinstance(v, str) else None
                    for r in rows for v in [(r.get("raw_row") or {}).get("verdict")]]
        for i, r in enumerate(rows):
            prev = (rows[i - 1].get("raw_row") or {}).get("cumulative_config") if i else None
            if isinstance(prev, str):
                try:
                    prev = json.loads(prev)
                except ValueError:
                    prev = None
            ctx[r["unit_id"]] = {"step_index": i, "prev_cumulative": prev, "is_terminal": i == len(rows) - 1,
                                 "session_verdicts": verdicts}
    out = []
    for u in units:
        v = dict(u)
        raw = v.get("raw_row") or {}
        ident = dict(v.get("identity") or {})
        ident.setdefault("platform", (raw.get("situation") or {}).get("platform"))
        ident.setdefault("session", raw.get("fix_session_id"))
        ident.setdefault("check", raw.get("check"))
        ident.setdefault("iter", int(raw.get("iter") or 1))
        ident.setdefault("ts", raw.get("ts"))
        v["identity"] = ident
        v.setdefault("lineage", {"design": ident.get("design"), "platform": ident.get("platform"),
                                 "project": Path(_project(u)).name, "session": ident.get("session"),
                                 "evidence_tier": None, "seed_origin": []})
        v.setdefault("capture_context", ctx[u["unit_id"]])
        v.setdefault("source_eligible", True)
        v["text_view_v1"] = u.get("text_view")
        v["text_view"] = shared_text(v)
        out.append(v)
    return out


def apply_override(lab: dict, ov: dict) -> dict:
    """A stricter, evidence-backed rule may only REMOVE success (never grant it)."""
    out = dict(lab)
    if ov.get("admitted") is False:
        out["admitted"] = False
    if ov.get("outcome_class"):
        out["outcome_class"] = ov["outcome_class"]
    out["override"] = {k: v for k, v in ov.items()}
    return out


def build(sources: list, labels: list, split: dict, view: str, overrides: dict | None = None) -> tuple:
    recs = {}
    for p in labels:
        for l in Path(p).read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                recs[r["unit_id"]] = r
    verify = set(split.get("verify") or []) | set(split.get("verify_source_groups") or [])
    out, dropped = [], collections.Counter()
    for p in sources:
        for l in Path(p).read_text().splitlines():
            if not l.strip():
                continue
            u = json.loads(l)
            r = recs.get(u["unit_id"])
            if r is None:
                dropped["no_label"] += 1
                continue
            ident = r.get("identity") or {}
            grp = (ident.get("source_group") or {}).get("value")
            fam = (ident.get("ip_family") or {}).get("value")
            if view == "dev_verification" and (grp is None or grp in verify or fam in verify):
                dropped["verify_cluster_or_unknown_identity"] += 1
                continue
            ov = (overrides or {}).get(u["unit_id"])
            if ov and ov.get("exclude"):
                dropped["excluded_by_override"] += 1
                continue
            lab = label(r, u)
            if ov:
                if ov.get("admitted") not in (None, False):
                    raise ValueError("an override may only remove success: " + u["unit_id"])
                lab = apply_override(lab, ov)
            out.append({**u, "shared_label": lab})
    out = normalise(out)
    manifest = {"schema": "t2-shared-history-v1", "version": VERSION, "view": view, "units": len(out),
                "admitted": sum(u["shared_label"]["admitted"] for u in out),
                "outcomes": dict(collections.Counter(str(u["shared_label"]["outcome_class"]) for u in out)),
                "source_groups": dict(collections.Counter(
                    str((recs[u["unit_id"]]["identity"].get("source_group") or {}).get("value")) for u in out)),
                "dropped": dict(dropped), "split": split,
                "inputs": {str(p): sha256(p) for p in list(sources) + list(labels)},
                "overrides": {"count": len(overrides or {}),
                              "excluded": sum(1 for v in (overrides or {}).values() if v.get("exclude")),
                              "success_removed": sum(1 for v in (overrides or {}).values() if v.get("admitted") is False)},
                "success_definition": "shared_label.admitted (ann-v2 admission) -- the export's enriched "
                                      "rollback/outcome fields are NOT a success signal for any arm"}
    return out, manifest


def tehm_view(units: list) -> list:
    """The package as TEHM's importer (tehm.neutral_export.import_units) reads it: each row's
    `rolled_back` marker is replaced by the verified ann-v2 value (removed when UNKNOWN), so TEHM's
    own evidence sees the SAME rollback evidence as every other arm."""
    out = []
    for u in units:
        v = json.loads(json.dumps(u))
        raw, rb = v["raw_row"], v["shared_label"]["rolled_back"]
        raw.pop("rolled_back", None)
        if rb.get("status") == "KNOWN" and isinstance(rb.get("value"), bool):
            raw["rolled_back"] = rb["value"]
        out.append(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sources", nargs="+", required=True)
    b.add_argument("--labels", nargs="+", required=True)
    b.add_argument("--contract", type=Path, required=True)
    b.add_argument("--view", choices=("dev_verification", "t2"), required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--overrides", type=Path, help="JSON {unit_id: {exclude|admitted:false, outcome_class, ...}}")
    a = ap.parse_args(argv)
    ovs = json.loads(a.overrides.read_text()) if a.overrides else None
    units, man = build(a.sources, a.labels, json.loads(a.contract.read_text())["split"], a.view, ovs)
    if a.overrides:
        man["inputs"][str(a.overrides)] = sha256(a.overrides)
    a.out.mkdir(parents=True, exist_ok=True)
    tv = a.out / "tehm-import"
    tv.mkdir(exist_ok=True)
    data = "".join(json.dumps(u, sort_keys=True) + "\n" for u in tehm_view(units))
    (tv / "source_export.jsonl").write_text(data)
    (tv / "source_manifest.json").write_text(json.dumps({
        "export_sha256": hashlib.sha256(data.encode()).hexdigest(), "view": a.view, "version": VERSION,
        "note": "TEHM import view of the shared package: rolled_back = verified ann-v2 value only"}, indent=1) + "\n")
    (a.out / "shared_units.jsonl").write_text("".join(json.dumps(u, sort_keys=True) + "\n" for u in units))
    man["units_sha256"] = sha256(a.out / "shared_units.jsonl")
    (a.out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: man[k] for k in ("view", "units", "admitted", "outcomes", "source_groups", "dropped")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
