#!/usr/bin/env python3
"""Apply the production graph validator's tensor checks to external converters.

The context is an R2G project generated from the same raw inputs. Its feature and
label CSVs supply the production checks; no hidden scorer or reference pickle is
read. External tensors are never rewritten. R2G manifests, global metadata,
physical signoff, and power are outside this exported-tensor contract.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import torch


COVERAGE = {
    "all_values": ["node identities and type-block order in all ten files",
                   "nonpower node features in all ten files",
                   "congestion/timing/wirelength normalized node labels",
                   "node ground-cap normalized labels from SPEF",
                   "raw/log1p identities for timing/wirelength/RC labels"],
    "structural": ["ordinary edge counts", "ordinary and RC edge symmetry",
                   "production topology checks and sampled folded-edge features"],
    "raw_input_checks": ["B coupling count and values against production SPEF parser"],
    "not_claimed": ["independent full numeric oracle", "full resistance recomputation",
                    "all folded-edge labels", "every RC endpoint in D/E",
                    "R2G manifest/global metadata", "power and physical signoff"],
}


def load_verifier(path):
    spec = importlib.util.spec_from_file_location("r2g_production_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(value):
    return str(value).replace("\\", "")


def close_arrays(actual, expected, *, atol=1e-4, rtol=1e-5):
    a = actual.detach().cpu().numpy() if torch.is_tensor(actual) else np.asarray(actual)
    b = expected.detach().cpu().numpy() if torch.is_tensor(expected) else np.asarray(expected)
    if a.shape != b.shape:
        return False, f"shape {a.shape} != {b.shape}"
    good = np.isclose(a, b, atol=atol, rtol=rtol, equal_nan=True)
    return bool(good.all()), f"{int((~good).sum())}/{good.size} mismatched"


def expected_views(vgd, project, design):
    views = vgd.build_views(str(project / "features"), design)
    gate, net, io, pin, egp, epn, ein = views
    frames = {"gate": gate, "net": net, "iopin": io, "pin": pin}
    names = {"gate": gate.inst_name.tolist(), "net": net.net_name.tolist(),
             "iopin": io.iopin_name.tolist(),
             "pin": [f"{i}/{p}" for i, p in zip(pin.inst_name, pin.pin_name)]}
    labels = vgd.expected_label_series(views, str(project / "labels"), design)
    return views, frames, names, labels


def ordinary_edge_counts(views):
    gate, net, io, pin, egp, epn, ein = views
    net_endpoints, gate_pins = defaultdict(set), defaultdict(set)
    for i, p, n in epn[["inst_name", "pin_name", "net_name"]].itertuples(index=False, name=None):
        net_endpoints[n].add(("pin", i, p))
    for i, n in ein[["iopin_name", "net_name"]].itertuples(index=False, name=None):
        net_endpoints[n].add(("io", i))
    for i, p in egp[["inst_name", "pin_name"]].itertuples(index=False, name=None):
        gate_pins[i].add(p)
    clique = lambda n: n * (n - 1) // 2
    nc = sum(clique(len(net_endpoints[n])) for n in net.net_name)
    fc = sum(clique(len({(e[0], e[1]) for e in net_endpoints[n]})) for n in net.net_name)
    gp = len(egp[["inst_name", "pin_name"]].drop_duplicates())
    pn = len(epn[["inst_name", "pin_name", "net_name"]].drop_duplicates())
    inn = len(ein[["iopin_name", "net_name"]].drop_duplicates())
    return {"b": 2 * (gp + pn + inn), "c": 2 * (pn + inn),
            "d": 2 * (gp + nc), "e": 2 * (sum(clique(len(s)) for s in gate_pins.values()) + nc),
            "f": 2 * fc}


def check_nodes(vgd, tag, variant, graph, frames, names, labels, ground, pin_nets):
    blocks = vgd._VIEW_BLOCK_ORDER[variant]
    expected_names = list(itertools.chain.from_iterable(names[t] for t, _ in blocks))
    got_names = list(getattr(graph, "node_name", []))
    ordered = [identity(n) for n in got_names] == [identity(n) for n in expected_names]
    vgd.check(f"{tag} node identities and order", ordered)
    if not ordered or graph.x.shape != (len(expected_names), 10):
        vgd.check(f"{tag} x shape", graph.x.shape == (len(expected_names), 10), str(tuple(graph.x.shape)))
        return False
    # The public contract permits removing DEF escape characters; restore only
    # the spelling used by the context in this in-memory view for helper joins.
    graph.node_name = expected_names
    schemas = {"gate": vgd.GATE_SCHEMA, "net": vgd.NET_SCHEMA,
               "iopin": vgd.IOPIN_SCHEMA, "pin": vgd.PIN_SCHEMA}
    label_slots = {"gate": (0, 1), "pin": (2, 3), "net": (3, 4)}
    offset = 0
    for kind, tid in blocks:
        frame, n = frames[kind], len(frames[kind])
        x = graph.x[offset:offset+n]
        vgd.check(f"{tag} {kind} node type", bool((x[:, 0] == tid).all()))
        for column, field in enumerate(schemas[kind], 2):
            if field == "cell_power":
                continue
            target = pd.to_numeric(frame[field], errors="coerce").to_numpy(dtype=np.float32)
            categorical = field.endswith("_id") or field in {"fanout", "pin_count", "num_drivers", "num_sinks", "connects_macro_flag", "num_layer"}
            atol, rtol = ((0, 0) if categorical else (6e-3, 1e-6) if field == "hpwl_um"
                          else (1e-6, 1e-5) if field in {"cell_area", "sum_pin_cap_fF"}
                          else (1e-4, 1e-6))
            ok, detail = close_arrays(x[:, column], target, atol=atol, rtol=rtol)
            vgd.check(f"{tag} {kind}.{field}", ok, detail)
        if kind in label_slots:
            order, column = label_slots[kind]
            target = labels[kind, order].to_numpy(dtype=np.float32)
            atol, rtol = ((1e-4, 1e-6) if kind == "pin" else (1e-6, 1e-5))
            ok, detail = close_arrays(graph.y[offset:offset+n, column], target, atol=atol, rtol=rtol)
            vgd.check(f"{tag} {kind} normalized label", ok, detail)
        if kind == "net" or (kind == "pin" and variant in "de"):
            ns = names[kind] if kind == "net" else [pin_nets.get(name) for name in names[kind]]
            target = np.array([math.log1p(ground[name]) if name in ground else np.nan for name in ns], dtype=np.float32)
            ok, detail = close_arrays(graph.y[offset:offset+n, 5], target, atol=1e-5, rtol=1e-5)
            vgd.check(f"{tag} {kind} ground cap from SPEF", ok, detail)
        offset += n
    vgd.check(f"{tag} constant graph_id", not len(graph.x) or bool((graph.x[:, 1] == graph.x[0, 1]).all()))
    for name, raw_name, columns in (("y", "y_raw", (3, 4, 5)),
                                    ("edge_y", "edge_y_raw", (3, 4)),
                                    ("rc_edge_y", "rc_edge_y_raw", (1, 2))):
        if not hasattr(graph, name):
            continue
        a, raw = getattr(graph, name), getattr(graph, raw_name, None)
        vgd.check(f"{tag} {raw_name} shape", raw is not None and raw.shape == a.shape)
        if raw is None or raw.shape != a.shape:
            continue
        for column in columns:
            ok, detail = close_arrays(a[:, column], torch.log1p(raw[:, column].clamp(min=0)), atol=1e-4, rtol=1e-5)
            vgd.check(f"{tag} {name}[{column}] raw/log1p identity", ok, detail)
    return True


def check_b_coupling(vgd, tag, graph, net_names, coupling):
    expected = {(a, b): value for (a, b), value in coupling.items() if a in net_names and b in net_names}
    mask = graph.rc_edge_type == 0
    vgd.check(f"{tag} coupling directed count from SPEF", int(mask.sum()) == 2 * len(expected),
              f"got {int(mask.sum())} expected {2*len(expected)}")
    missing = bad = 0
    for k in mask.nonzero().view(-1).tolist():
        u, v = graph.rc_edge_index[:, k].tolist()
        key = tuple(sorted((graph.node_name[u], graph.node_name[v])))
        if key not in expected:
            missing += 1
        else:
            got = float(graph.rc_edge_y_raw[k, 1])
            bad += not math.isfinite(got) or abs(got - expected[key]) > 1e-5 + 1e-5 * abs(expected[key])
    vgd.check(f"{tag} coupling endpoints and raw values from SPEF", missing == 0 and bad == 0,
              f"unknown pairs={missing}, wrong values={bad}")


def audit(project, dataset, inputs, verifier_path):
    start = time.monotonic()
    vgd = load_verifier(verifier_path)
    os.environ["R2G_DEF"] = str(inputs / "6_final.def")
    os.environ["R2G_SPEF"] = str(inputs / "6_final.spef")
    design = json.loads((project / "dataset/graph_manifest.json").read_text())["design"]
    views, frames, names, labels = expected_views(vgd, project, design)
    counts = ordinary_edge_counts(views)
    rc = vgd.read_spef_truth(str(project))
    ground = rc.get("ground", {})
    pin_nets = {f"{i}/{p}": n for i, p, n in views[5][["inst_name", "pin_name", "net_name"]].itertuples(index=False, name=None)}
    for form, suffix in (("hetero", ""), ("homo", "_homo")):
        tensors, hetero = {}, {}
        for variant in "bcdef":
            tag, path = f"{form}.{variant}", dataset / f"{variant}_graph{suffix}.pt"
            vgd.check(f"{tag} file exists", path.is_file())
            if not path.is_file():
                continue
            try:
                graph, hg = vgd._load_graph(str(path))
                if not check_nodes(vgd, tag, variant, graph, frames, names, labels, ground, pin_nets):
                    continue
                tensors[variant], hetero[variant] = graph, hg
                vgd.check(f"{tag} ordinary edge count", graph.edge_index.shape[1] == counts[variant],
                          f"got {graph.edge_index.shape[1]} expected {counts[variant]}")
                if variant == "b":
                    check_b_coupling(vgd, tag, graph, set(names["net"]), rc.get("coupling", {}))
                if variant == "e":
                    # Gate leakage is excluded from the native task. Exclude it
                    # symmetrically in the in-memory folded-feature comparison.
                    graph.edge_attr[graph.edge_type == 0, 2] = 0
            except Exception as error:
                vgd.check(f"{tag} load/validate", False, f"{type(error).__name__}: {error}")
        if len(tensors) == 5:
            topology_views = list(views)
            topology_views[0] = views[0].copy()
            topology_views[0]["cell_power"] = 0.0
            before = len(vgd.RESULTS)
            try:
                # Heterogeneous conversion groups relations; native homogeneous
                # contracts permit this order too, so use relation alignment for
                # hetero and the declared interleaved check for homo.
                vgd.topology_checks(tuple(topology_views), tensors,
                                    hetero=hetero if all(hetero.values()) else None)
            except Exception as error:
                vgd.check("topology validation completed", False, f"{type(error).__name__}: {error}")
            for row in vgd.RESULTS[before:]:
                row["check"] = form + "." + row["check"]
    failed = [row for row in vgd.RESULTS if not row["ok"]]
    return {"schema": 1, "profile": "external_nonpower_tensors_v1", "design": design,
            "dataset": str(dataset), "context": str(project), "raw_inputs": str(inputs),
            "coverage": COVERAGE, "passed": len(vgd.RESULTS)-len(failed), "failed": len(failed),
            "flagged": bool(failed), "results": vgd.RESULTS,
            "wall_seconds": round(time.monotonic()-start, 3),
            "source_sha256": {"adapter": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              "production_verifier": hashlib.sha256(Path(verifier_path).read_bytes()).hexdigest()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--verifier", type=Path, default=Path(__file__).resolve().parents[2] / "tools/verify_graph_dataset.py")
    args = parser.parse_args()
    result = audit(args.project, args.dataset, args.inputs, args.verifier)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("design", "passed", "failed", "flagged", "wall_seconds")}))
    return int(result["flagged"])


if __name__ == "__main__":
    raise SystemExit(main())
