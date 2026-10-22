#!/usr/bin/env python3
"""P1c exit conditions under the MAIN caliber on the post-recovery export (scratch stores only).
X1 one-shot vs batched (chronological / hash order), D-R2 history-rule isolation, continued learning (the same
hold-out fed to every store), X2 marker round trip, X3 re-import, X4 identity scope.
Usage: x_main.py <code root> <export dir> <work dir>"""
import collections
import json
import sys
import time
from pathlib import Path

code, exp, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code / "memory"))
from tehm import evidence as ev  # noqa: E402
from tehm import neutral_export as nx  # noqa: E402
from tehm.retrieval.index import build_index  # noqa: E402
from tehm.retrieval.vetoes import situation_vetoes  # noqa: E402
from tehm_backend import TehmMemoryBackend  # noqa: E402

units = [u for u in nx.read_export(exp) if u["source_eligible"]]
chrono = sorted(units, key=lambda u: (str(u["identity"]["ts"] or ""), u["unit_id"]))
cut = len(chrono) - len(chrono) // 10
base, holdout = chrono[:cut], chrono[cut:]
situations = sorted({json.dumps(u["raw_row"]["situation"], sort_keys=True) for u in units})


def chunks(seq, n):
    k = (len(seq) + n - 1) // n
    return [seq[i:i + k] for i in range(0, len(seq), k)]


def snapshot(conn):
    tup = lambda q: sorted(tuple(r) for r in conn.execute(q).fetchall())
    rules = {r[0]: r[1:] for r in tup("SELECT rule_id, before_pattern_json, after_pattern_json, "
                                      "hard_preconditions_json, validity_status FROM tehm_rules")}
    status = collections.defaultdict(dict)
    for rid, sc, st in tup("SELECT rule_id, target_scope, status FROM tehm_rule_status"):
        status[rid][sc] = st
    evid = {rid: ev.rule_evidence(conn, rid) for rid in rules}
    active = {rid for rid, s in status.items() if any(v in ("candidate", "promoted") for v in s.values())}
    adm = lambda cal: sorted((rid, sc) for rid, s in status.items() for sc, v in s.items()
                             if v not in ("retired", "quarantined") and ev.admissible(evid[rid], v, caliber_name=cal))
    return {
        "attempts": tup("SELECT record_id, transition_id, record_digest FROM tehm_attempts"),
        "transitions": tup("SELECT transition_id FROM tehm_transitions"),
        "episodes": tup("SELECT episode_id FROM tehm_episodes"),
        "rules": rules, "status": {k: dict(v) for k, v in status.items()}, "evidence": evid, "active": active,
        "admitted_main": adm("main"), "admitted_history": adm("history"),
        "index_trial": sorted(build_index(conn, lifecycle_statuses=frozenset({"candidate", "promoted"})).rule_ids()),
        "index_promoted": sorted(build_index(conn, lifecycle_statuses=frozenset({"promoted"})).rule_ids()),
        "vetoes": {s: json.dumps(situation_vetoes(conn, json.loads(s)), sort_keys=True, default=str) for s in situations},
    }


def compare(a, b):
    """Effective results (D-R2): everything that can act must agree; history-only rules are listed and checked."""
    out = {}
    for k in ("attempts", "transitions", "episodes", "admitted_main", "admitted_history", "index_trial",
              "index_promoted", "vetoes"):
        out[k] = "equal" if a[k] == b[k] else "DIFFERENT"
    common = set(a["rules"]) & set(b["rules"])
    out["active_rules"] = "equal" if ({r: a["rules"][r] for r in a["active"]} == {r: b["rules"][r] for r in b["active"]}) else "DIFFERENT"
    out["common_rule_content"] = "equal" if all(a["rules"][r] == b["rules"][r] for r in common) else "DIFFERENT"
    out["common_rule_evidence"] = "equal" if all(a["evidence"][r] == b["evidence"][r] for r in common) else "DIFFERENT"
    out["common_rule_status"] = "equal" if all(a["status"].get(r) == b["status"].get(r) for r in common) else \
        sorted((r, a["status"].get(r), b["status"].get(r)) for r in common if a["status"].get(r) != b["status"].get(r))[:8]
    extra_a, extra_b = set(a["rules"]) - set(b["rules"]), set(b["rules"]) - set(a["rules"])
    out["history_only_rules"] = {"first": len(extra_a), "second": len(extra_b),
                                 "second_statuses": dict(collections.Counter(
                                     "|".join(sorted(b["status"].get(r, {}).values())) or "no_status" for r in extra_b))}
    out["history_rules_never_indexed"] = not (extra_b & set(b["index_trial"])) and not (extra_a & set(a["index_trial"]))
    out["history_rules_never_admitted"] = not any(r in extra_b for r, _ in b["admitted_main"] + b["admitted_history"])
    return out


report = {"units": len(units), "base": len(base), "holdout": len(holdout),
          "restored_units": sum(1 for u in units if u["raw_row"].get("rollback_restoration"))}
stores = {}
for name, batches in {"one_shot": [base], "chronological_4": chunks(base, 4),
                      "hash_order_4": chunks(sorted(base, key=lambda u: u["unit_id"]), 4)}.items():
    d = work / name
    if d.exists():
        raise SystemExit("refusing to reuse %s" % d)
    b = TehmMemoryBackend(db_path=d / "tehm.sqlite", artifact_root=d / "art", read_only_eval=False)
    conn, store = b._open()
    t0 = time.time()
    for batch in batches:
        nx.import_units(conn, store, batch)
        b.rebuild()
    stores[name] = (b, conn, store, snapshot(conn))
    report[name] = {"seconds": round(time.time() - t0), "rules": len(stores[name][3]["rules"]),
                    "active": len(stores[name][3]["active"]), "admitted_main": len(stores[name][3]["admitted_main"]),
                    "admitted_history": len(stores[name][3]["admitted_history"])}
def dump():
    (work / "x_main_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")


one = stores["one_shot"][3]
for name in ("chronological_4", "hash_order_4"):
    report["X1_%s_vs_one_shot" % name] = compare(one, stores[name][3])
dump()
report["admitted_main_one_shot"] = [{"rule": r, "scope": sc, "evidence": {k: one["evidence"][r][k] for k in (
    "positive", "harmful", "no_clear", "rolled_back", "rollback_unknown", "no_evidence", "units")}}
    for r, sc in one["admitted_main"]]

# X3: re-importing the whole base adds nothing
b, conn, store, _ = stores["one_shot"]
report["X3_reimport_receipts"] = len(nx.import_units(conn, store, base))
# X2: the marker each attempt carries matches the unit's status for the main caliber
want = {u["unit_id"]: ("false" if u["raw_row"].get("rolled_back") is False else
                       "true" if u["raw_row"].get("rolled_back") is True else "UNKNOWN") for u in base}
got = {}
for rid, delta in conn.execute("SELECT a.record_id, t.observation_delta_json FROM tehm_attempts a "
                               "JOIN tehm_transitions t USING (transition_id)"):
    got[rid] = json.loads(delta).get("rolled_back", "UNKNOWN")
report["X2_marker_mismatches"] = sorted(r for r in want if got.get(r) != want[r])[:10]
report["X2_markers"] = dict(collections.Counter(got.values()))
# X4: one attempt per unit, scoped identity, no cross-design merge
scopes = {json.loads(p)["record_scope"]["design"] for (p,) in conn.execute("SELECT provenance_json FROM tehm_attempts")}
report["X4"] = {"attempts": len(got), "units": len(base), "distinct_designs_in_attempts": len(scopes),
                "distinct_designs_in_units": len({u["identity"]["design"] for u in base}),
                "attempt_ids_equal_unit_ids": set(got) == set(want)}

dump()
# continued learning: the same hold-out into every store, then the same comparison
for name, (b, conn, store, _) in stores.items():
    nx.import_units(conn, store, holdout)
    b.rebuild()
    stores[name] = (b, conn, store, snapshot(conn))
one = stores["one_shot"][3]
for name in ("chronological_4", "hash_order_4"):
    report["continued_%s_vs_one_shot" % name] = compare(one, stores[name][3])
report["continued_one_shot"] = {"rules": len(one["rules"]), "active": len(one["active"]),
                                "admitted_main": len(one["admitted_main"])}
for b, *_ in stores.values():
    b.close()
(work / "x_main_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "admitted_main_one_shot"}, indent=1, default=str))
print("admitted under the main caliber (one-shot):")
for a in report["admitted_main_one_shot"]:
    print("  ", a["rule"], a["scope"], a["evidence"])
