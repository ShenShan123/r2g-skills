"""ann-v2 manual-audit package (spec §8).

* strata: failure type x step effect, with positives as their own sub-stratum of CLEARED
  (a miscounted positive is a critical error, so positives are always sampled);
* sample: fixed, recorded seed; at least `min_per_stratum` units per stratum, or all;
  independent of record order;
* worksheet: one section per sampled unit pointing the reviewer (who did not write the
  annotator) at the raw files, with the five critical checks and a verdict line;
* summary: any critical error blocks the freeze and expands its stratum to a full check;
  the non-critical rate is reported with an exact (Clopper-Pearson) upper bound -- the
  2% figure is an operating threshold, never a claim about the true rate.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

CRITICAL_CHECKS = (
    "wrong run binding (post or pre run is not the run this action produced / responded to)",
    "UNKNOWN recorded as KNOWN (a KNOWN field without raw evidence, or a bound run without an E1 link)",
    "miscounted positive (effect, new failures, rollback or binding do not support `positive`)",
    "missed rollback (the action was rolled back / restored but rolled_back is not true)",
    "confused lineage or identity (design, ip_family or source_group wrong)",
)
NON_CRITICAL_NOTE = ("2% is an operating threshold for non-critical errors only; the upper bound is an exact "
                     "one-sided 95% Clopper-Pearson bound and no claim is made that the true rate is under 2%.")


def _key(r: dict) -> str | None:
    if r.get("status") != "ATTEMPT":
        return None
    kinds = sorted(r.get("step_effect") or {"UNKNOWN": "UNKNOWN"})
    kind = kinds[0]
    effect = (r.get("step_effect") or {}).get(kind, "UNKNOWN")
    return "|".join((kind, effect, "positive" if r.get("positive") else "not_positive"))


def strata(records: list) -> dict:
    out: dict = {}
    for r in records:
        k = _key(r)
        if k is not None:
            out.setdefault(tuple(k.split("|")), []).append(r["unit_id"])
    return {k: sorted(v) for k, v in out.items()}


def sample(records: list, seed: int, min_per_stratum: int = 10) -> dict:
    by = {}
    for k, ids in sorted(strata(records).items()):
        rng = random.Random(f"{seed}:{'|'.join(k)}")
        by["|".join(k)] = sorted(ids) if len(ids) <= min_per_stratum else sorted(rng.sample(ids, min_per_stratum))
    return {"seed": seed, "min_per_stratum": min_per_stratum,
            "stratum_sizes": {"|".join(k): len(v) for k, v in strata(records).items()}, "by_stratum": by}


def _field_lines(fields: dict) -> list:
    out = []
    for name, f in sorted(fields.items()):
        if f.get("status") == "NOT_APPLICABLE":
            continue
        srcs = "; ".join(f"{s.get('path')} @ {s.get('locator')}" for s in f.get("source") or []) or "-"
        out.append(f"    - {name}: {f.get('value')!r} [{f.get('status')}"
                   f"{(' ' + f['unknown_reason']) if f.get('unknown_reason') else ''}]  <- {srcs}")
    return out


def worksheet(r: dict, unit: dict) -> str:
    raw = unit.get("raw_row") or {}
    srcs = [s for s in (unit.get("provenance") or {}).get("sources") or []]
    b = r["binding"]
    lines = [f"## {r['unit_id']}", "",
             f"- stratum: `{_key(r)}`",
             f"- action row: strategy={raw.get('strategy')} iter={raw.get('iter')} session={raw.get('fix_session_id')}",
             "- fix-log sources: " + "; ".join(f"{s.get('path')} line {s.get('line')}" for s in srcs)]
    for side in ("post", "pre"):
        x = b[side]
        if x["status"] == "KNOWN":
            e1 = (x.get("evidence") or {}).get("e1") or {}
            paths = e1.get("paths") or []
            lines.append(f"- {side}-action run: {x['run']} [KNOWN]  paths: {'; '.join(paths) or '-'}")
            lines.append(f"    - E1: {json.dumps({k: v for k, v in e1.items() if k != 'paths'}, default=str)}")
            lines.append(f"    - E2: {json.dumps((x.get('evidence') or {}).get('e2'), default=str)}")
        else:
            lines.append(f"- {side}-action run: none [UNKNOWN {x.get('unknown_reason')}]; candidates: "
                         + (", ".join(c.get("run", "?") for c in x.get("candidates") or []) or "-"))
    for t in r.get("targets") or []:
        lines.append(f"- target: {t.get('type')} [{t.get('type_status')}]")
        lines += _field_lines(t.get("fields") or {})
    lines.append(f"- step effect: {json.dumps(r.get('step_effect'))}")
    lines.append(f"- new failures: {json.dumps(r.get('new_failures'))}")
    rb = r.get("rolled_back") or {}
    lines.append(f"- rolled_back: {rb.get('value')!r} [{rb.get('status')}] <- "
                 + ("; ".join(f"{s.get('path')} @ {s.get('locator')}" for s in rb.get("source") or []) or "-"))
    lines.append(f"- positive: {r.get('positive')}  blockers: {r.get('positive_blockers')}")
    ident = r.get("identity") or {}
    lines.append("- identity: " + ", ".join(f"{k}={ident[k].get('value')!r}[{ident[k].get('status')}]"
                                            for k in ("design_id", "ip_family", "source_group") if k in ident))
    if r.get("post_run_advisory"):
        lines.append(f"- post_run_advisory (NOT a binding): {json.dumps(r['post_run_advisory'])}")
    lines += ["", "Critical checks (tick only after checking the raw files named above):"]
    lines += [f"- [ ] {c}" for c in CRITICAL_CHECKS]
    lines += ["", "Non-critical errors found: ", "", "VERDICT: ", ""]
    return "\n".join(lines)


def binomial_upper_bound(k: int, n: int, conf: float = 0.95) -> float:
    """Exact one-sided upper confidence bound on a binomial proportion (Clopper-Pearson)."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    alpha = 1.0 - conf

    def cdf(p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def summary(verdicts: list, stratum_sizes: dict) -> dict:
    by: dict = {}
    for v in verdicts:
        s = by.setdefault(v["stratum"], {"reviewed": 0, "critical": 0, "non_critical": 0, "critical_units": []})
        s["reviewed"] += 1
        if v.get("critical"):
            s["critical"] += 1
            s["critical_units"].append(v["unit_id"])
        s["non_critical"] += len(v.get("non_critical") or [])
    for name, s in by.items():
        s["stratum_size"] = stratum_sizes.get(name)
        s["expand_to_full_stratum"] = s["critical"] > 0
        s["non_critical_rate_upper_95"] = round(binomial_upper_bound(min(s["non_critical"], s["reviewed"]),
                                                                     s["reviewed"]), 4)
    crit = sum(s["critical"] for s in by.values())
    return {"reviewed": len(verdicts), "critical_errors": crit, "freeze_blocked": crit > 0, "by_stratum": by,
            "note": NON_CRITICAL_NOTE}


def build_package(annotation_jsonl: Path, source_jsonl: Path, out_dir: Path, seed: int) -> dict:
    """Write audit_sample.json and audit_worksheet.md for the reviewer."""
    records = [json.loads(l) for l in Path(annotation_jsonl).read_text().splitlines() if l.strip()]
    units = {}
    for l in Path(source_jsonl).read_text().splitlines():
        if l.strip():
            u = json.loads(l)
            units[u["unit_id"]] = u
    smp = sample(records, seed)
    by_id = {r["unit_id"]: r for r in records}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit_sample.json").write_text(json.dumps(smp, indent=1, sort_keys=True) + "\n")
    parts = ["# ann-v2 manual audit worksheet (DEV annotation)", "",
             f"seed: {seed}; min per stratum: {smp['min_per_stratum']}; "
             f"sampled units: {sum(len(v) for v in smp['by_stratum'].values())}", "",
             "The reviewer must not be the annotator's author. Tick a critical check ONLY after reading the raw "
             "files named in the unit section. One critical error anywhere blocks the freeze.", ""]
    for stratum, ids in smp["by_stratum"].items():
        parts.append(f"# stratum {stratum} ({len(ids)} of {smp['stratum_sizes'][stratum]})")
        parts.append("")
        for uid in ids:
            parts.append(worksheet(by_id[uid], units[uid]))
    (out_dir / "audit_worksheet.md").write_text("\n".join(parts))
    return smp


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="build the ann-v2 manual-audit package")
    ap.add_argument("--annotation", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    s = build_package(a.annotation, a.source, a.out, a.seed)
    print(json.dumps({k: len(v) for k, v in s["by_stratum"].items()}, indent=1))
