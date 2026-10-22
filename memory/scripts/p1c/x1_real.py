#!/usr/bin/env python3
"""P1c X1 / T10 on the real corpus: one-shot vs batched import of the neutral export into scratch stores.
Usage: x1_real.py <code root> <export dir> <work dir>. Only scratch stores under <work dir> are written."""
import json
import sqlite3
import sys
import time
from pathlib import Path

code, exp, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code / "memory"))
from tehm import evidence as ev  # noqa: E402
from tehm import neutral_export as nx  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402

units = [u for u in nx.read_export(exp) if u["source_eligible"]]


def build(name, batches):
    d = work / name
    if d.exists():
        raise SystemExit("refusing to reuse %s" % d)
    b = TehmMemoryBackend(db_path=d / "tehm.sqlite", artifact_root=d / "art", read_only_eval=False)
    conn, store = b._open()
    t0 = time.time()
    for batch in batches:
        nx.import_units(conn, store, batch)
        b.rebuild()
    tup = lambda q: sorted(tuple(r) for r in conn.execute(q).fetchall())
    rules = tup("SELECT rule_id, before_pattern_json, after_pattern_json, hard_preconditions_json, validity_status "
                "FROM tehm_rules")
    snap = {"attempts": tup("SELECT record_id, transition_id, record_digest FROM tehm_attempts"),
            "transitions": tup("SELECT transition_id FROM tehm_transitions"),
            "episodes": tup("SELECT episode_id FROM tehm_episodes"),
            "rules": rules, "status": tup("SELECT rule_id, target_scope, status FROM tehm_rule_status"),
            "evidence": {r[0]: ev.rule_evidence(conn, r[0]) for r in rules}}
    b.close()
    return snap, time.time() - t0


def chunks(seq, n):
    k = (len(seq) + n - 1) // n
    return [seq[i:i + k] for i in range(0, len(seq), k)]


chrono = sorted(units, key=lambda u: (str(u["identity"]["ts"]), u["unit_id"]))
hashed = sorted(units, key=lambda u: u["unit_id"])
runs = {"one_shot": [units], "chronological_4": chunks(chrono, 4), "hash_order_4": chunks(hashed, 4)}
snaps, report = {}, {"units": len(units)}
for name, batches in runs.items():
    snaps[name], secs = build(name, batches)
    report[name] = {"batches": len(batches), "seconds": round(secs, 1),
                    **{k: len(v) for k, v in snaps[name].items()}}
for name in ("chronological_4", "hash_order_4"):
    diff = {}
    for k in snaps["one_shot"]:
        a, b = snaps["one_shot"][k], snaps[name][k]
        if a != b:
            if isinstance(a, dict):
                diff[k] = sorted(set(a) ^ set(b))[:5] + [r for r in a if r in b and a[r] != b[r]][:5]
            else:
                diff[k] = {"only_one_shot": [x for x in a if x not in b][:5], "only_batched": [x for x in b if x not in a][:5],
                           "counts": [len(a), len(b)]}
    report[name + "_vs_one_shot"] = diff or "identical"
(work / "x1_real_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
print(json.dumps(report, indent=1, default=str)[:6000])
