#!/usr/bin/env python3
"""Frozen evidence index for TEHM-v2 ranking, built from verified ann-v2 labels.

One entry per (unit, knob move) for every attempt with BOTH runs bound and a typed target:
  outcome  admitted_positive    admission ADMITTED (strict single-step positive)
           harm                 step effect REGRESSED
           cleared_new_failure  CLEARED, but a new failure appeared
           improved             IMPROVED
           no_change            NO_CHANGE (ineffective; distinct from harm)
           cleared_unconfirmed  D8 candidate class (rollback unknown): reported, never ranked
Moves come from the action's own row: config_before -> the delta's `after`, or the recorded
cumulative_config where the delta is empty. A unit whose move cannot be recovered is skipped
and counted. The view `dev_verification` drops every VERIFY-cluster unit (contract split).

  python3 -m tehm_v2.evidence_index --labels <ann jsonl> [...] --sources <source_export> [...] \
      --contract <t2_history_collection_contract.json> --view dev_verification|t2 --out <index.json>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path


def signature_of(rec: dict) -> str | None:
    t = (rec.get("targets") or [{}])[0]
    f = t.get("fields") or {}
    v = lambda k: (f.get(k) or {}).get("value")  # noqa: E731
    if t.get("type_status") != "KNOWN":
        return None
    if t["type"] == "stage_abort":
        return f"{v('stage')}:{v('error_code')}"
    if t["type"] == "drc":
        return f"drc:{v('rule_class')}"
    if t["type"] in ("drv", "setup", "hold", "lvs", "route_residual"):
        return t["type"]
    return None


def signature_type(sig: str) -> str:
    head = sig.split(":")[0]
    return head if head in ("drc", "drv", "setup", "hold", "lvs") else head   # route / place / ...


# ORFS defaults of the knobs whose unset state is a real value (variables.yaml). A move is measured
# against the default when the config leaves the knob unset, in the index and in the policy alike;
# otherwise "unset -> 0.1" and "0.5 -> 0.1" would be different moves for the same action.
ORFS_DEFAULTS = {"ROUTING_LAYER_ADJUSTMENT": 0.5, "GPL_ROUTABILITY_DRIVEN": 1.0}


def row_moves(raw: dict) -> dict:
    """{knob: (before, after, direction)}; direction 'set' when the knob had no prior value and no
    ORFS default."""
    before = dict(raw.get("config_before") or {})
    delta = raw.get("config_delta")
    if isinstance(delta, str):
        try:
            delta = json.loads(delta)
        except ValueError:
            delta = {}
    after = {}
    for k, v in (delta or {}).items():
        if isinstance(v, dict):                     # engine-loop recorders: {before, after}
            after[k] = v.get("after")
            if v.get("before") is not None and k not in before:
                before[k] = v.get("before")
        else:
            after[k] = v
    if not after:
        cum = raw.get("cumulative_config")
        if isinstance(cum, str):
            try:
                cum = json.loads(cum)
            except ValueError:
                cum = {}
        after = {k: v for k, v in (cum or {}).items() if str(before.get(k)) != str(v)}
    out = {}
    for k, a in after.items():
        try:
            x = float(a)
        except (TypeError, ValueError):
            continue
        try:
            b = float(before.get(k) if before.get(k) is not None else ORFS_DEFAULTS.get(k))
        except (TypeError, ValueError):
            out[k] = (None, x, "set")
            continue
        if b != x:
            out[k] = (b, x, "up" if x > b else "down")
    return out


def outcome_of(rec: dict) -> str | None:
    if (rec.get("admission") or {}).get("status") == "ADMITTED":
        return "admitted_positive"
    if rec.get("candidate_class"):
        return "cleared_unconfirmed"
    effects = set((rec.get("step_effect") or {}).values())
    if "REGRESSED" in effects:
        return "harm"
    if effects == {"CLEARED"}:
        return "cleared_new_failure" if any(v == "KNOWN-present" for v in (rec.get("new_failures") or {}).values()) \
            else None
    if "IMPROVED" in effects:
        return "improved"
    if effects == {"NO_CHANGE"}:
        return "no_change"
    return None


def task_of(unit: dict) -> str:
    srcs = (unit.get("provenance") or {}).get("sources") or []
    proj = Path(srcs[0]["path"]).parent.parent.name if srcs else "?"
    m = re.match(r"(.+?__u\d+|.+?__default)", proj)
    return m.group(1) if m else proj.split("__")[0]


def build(labels: list, sources: list, split: dict, view: str) -> dict:
    units = {}
    for s in sources:
        for l in Path(s).read_text().splitlines():
            if l.strip():
                u = json.loads(l)
                units[u["unit_id"]] = u
    verify = set(split.get("verify") or []) | set(split.get("verify_source_groups") or [])
    entries, skipped = [], collections.Counter()
    for p in labels:
        for l in Path(p).read_text().splitlines():
            r = json.loads(l)
            if r.get("status") != "ATTEMPT":
                continue
            b = r.get("binding") or {}
            if not ((b.get("post") or {}).get("status") == "KNOWN" and (b.get("pre") or {}).get("status") == "KNOWN"):
                skipped["not_bound"] += 1
                continue
            sig, out = signature_of(r), outcome_of(r)
            if sig is None or out is None:
                skipped["untyped_or_unclassified"] += 1
                continue
            grp = ((r.get("identity") or {}).get("source_group") or {}).get("value")
            fam = ((r.get("identity") or {}).get("ip_family") or {}).get("value")
            if view == "dev_verification" and (grp in verify or fam in verify or grp is None):
                skipped["verify_cluster_masked"] += 1
                continue
            u = units.get(r["unit_id"])
            mv = row_moves((u or {}).get("raw_row") or {})
            if not mv:
                skipped["move_not_recoverable"] += 1
                continue
            for k, (bv, av, d) in mv.items():
                entries.append({"unit_id": r["unit_id"], "signature": sig, "signature_type": signature_type(sig),
                                "knob": k, "before": bv, "after": av, "direction": d, "outcome": out,
                                "source_group": grp, "ip_family": fam, "task": task_of(u)})
    return {"schema": "tehm-v2-evidence-index-v1", "view": view, "entries": entries, "skipped": dict(skipped),
            "built_from": {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in list(labels) + list(sources)}}


def build_shared(units: list, labels: list, view: str) -> dict:
    """The index from a SHARED history package (t2_shared_history): the outcome class, signature and
    binding come from each unit's shared_label, so package overrides (a cleared congestion whose
    signoff failed, a tool no-op excluded) bind TEHM exactly as they bind every other arm. The
    source group / IP family come from the verified label records of the same units."""
    ident = {}
    for p in labels:
        for l in Path(p).read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                ident[r["unit_id"]] = r.get("identity") or {}
    entries, skipped = [], collections.Counter()
    for u in units:
        lab = u.get("shared_label") or {}
        out, sig = lab.get("outcome_class"), lab.get("signature")
        if out == "tool_no_op":      # the platform's router never read the edit: an ineffective action
            out = "no_change"
        if out is None or sig is None:
            skipped["untyped_or_unclassified"] += 1
            continue
        b = lab.get("binding") or {}
        if not (b.get("post") == "KNOWN" and b.get("pre") == "KNOWN"):
            skipped["not_bound"] += 1
            continue
        i = ident.get(u["unit_id"]) or {}
        grp, fam = (i.get("source_group") or {}).get("value"), (i.get("ip_family") or {}).get("value")
        if grp is None:
            skipped["identity_unknown"] += 1
            continue
        mv = row_moves(u.get("raw_row") or {})
        if not mv:
            skipped["move_not_recoverable"] += 1
            continue
        for k, (bv, av, d) in mv.items():
            entries.append({"unit_id": u["unit_id"], "signature": sig, "signature_type": signature_type(sig),
                            "knob": k, "before": bv, "after": av, "direction": d, "outcome": out,
                            "source_group": grp, "ip_family": fam, "task": task_of(u)})
    return {"schema": "tehm-v2-evidence-index-v1", "view": view, "entries": entries, "skipped": dict(skipped),
            "built_from": "shared package (units' shared_label) + label identities"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", nargs="+", required=True)
    ap.add_argument("--sources", nargs="+", required=True)
    ap.add_argument("--contract", type=Path, required=True)
    ap.add_argument("--view", choices=("dev_verification", "t2"), required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    idx = build(a.labels, a.sources, json.loads(a.contract.read_text())["split"], a.view)
    a.out.write_text(json.dumps(idx, indent=1, sort_keys=True) + "\n")
    c = collections.Counter((e["signature"], e["knob"], e["direction"], e["outcome"]) for e in idx["entries"])
    print(json.dumps({"entries": len(idx["entries"]), "skipped": idx["skipped"]}, indent=1))
    for k, v in sorted(c.items()):
        print(v, k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
