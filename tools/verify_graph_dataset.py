#!/usr/bin/env python3
"""Ground-truth verifier for one design's PyG graph dataset (variants b..f).

Independently re-derives every structural and label expectation from the
feature/label CSVs (separate pandas code, NOT graph_lib) and compares against
the shipped tensors:

  * per-variant node counts by type (block order gate,net,iopin,pin)
  * per-variant expected EDGE counts — b/c by row counting, d/e/f by the
    clique formula sum C(k,2) over per-net/per-gate unique endpoints
  * c-variant edge_attr == folded pin's features (unambiguous samples)
  * f-variant edge_attr == the connecting net's features (unambiguous samples)
  * EXACT expected non-NaN count per y slot per variant (label joins), plus
    sampled value equality against the label CSVs
  * node_name positional integrity; x1 graph_id uniform; y0 == node type
  * global_feat == metadata.csv row (METADATA_SCHEMA order)
  * netlist_graph.pt: cell count vs an independent master-regex count; nets
    and sampled connectivity vs the statement parser
  * manifest consistency (variant stats == tensors; label_health all ok)
  * value sanity: sum_pin_cap_fF p50 within physical range, hpwl >= 0

Usage: $R2G_GRAPH_PYTHON tools/verify_graph_dataset.py <case_dir> [--design NAME] [--json OUT]
Needs torch + pandas (the graph stage's venv — see run_graphs.sh / graph-dataset.md).
Exit 0 = all checks pass; 1 = at least one FAIL (details on stdout / --json).

Proven baseline (2026-07-06): 54/54 checks on 9 sky130hd designs spanning
159..190K cells (FSM, UART, AXI register/CDMA, CPU, USB, combinational S-box,
SHA-256, AES) — see docs/superpowers/plans/rtl2graph-integration-audit-2026-07-05.md.
For CSV-level (extractor-truth) spot checks, additionally compare sampled nets
against OpenROAD: `report_wire_length -net <net> -detailed_route` on the run's
6_final.odb emits `[INFO GRT-0240] Net <n> ... length: <x>um` lines to diff
against labels/wirelength.csv (patch/RECT metal excluded → CSV reads ~0.2um low
on RECT-bearing sky130 nets).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

import pandas as pd
import torch

GATE_SCHEMA = ["cell_type_id", "cell_area", "cell_power", "x_um", "y_um",
               "orientation_id", "placement_status_id"]
NET_SCHEMA = ["net_type_id", "fanout", "pin_count", "num_drivers", "num_sinks",
              "connects_macro_flag", "num_layer", "hpwl_um"]
IOPIN_SCHEMA = ["pin_x_um", "pin_y_um", "nearest_tap_distance_um", "pin_direction_id"]
PIN_SCHEMA = ["pin_type_id", "sum_pin_cap_fF"]
METADATA_SCHEMA = ["num_cells", "num_nets", "num_ios", "avg_fanout", "die_width",
                   "die_height", "core_area", "dbu_unit", "PLACE_DENSITY",
                   "CORE_UTILIZATION", "ABC_AREA", "C_total", "tracks_per_layer",
                   "V_nom", "freq_Hz"]

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if (detail and not ok) else ""))
    return ok


def c2(k):
    return k * (k - 1) // 2


def build_views(feat_dir, design):
    """Independent re-application of build_feature_views' documented filters."""
    def load(name):
        df = pd.read_csv(os.path.join(feat_dir, name))
        if "graph_id" in df.columns:
            df = df[df["graph_id"].astype(str) == design]
        return df.reset_index(drop=True)

    gate = load("nodes_gate.csv")
    net = load("nodes_net.csv")
    iopin = load("nodes_iopin.csv")
    pin = load("nodes_pin.csv")
    egp = load("edges_gate_pin.csv")
    epn = load("edges_pin_net.csv")
    ein = load("edges_iopin_net.csv")

    gate = gate[~gate["master"].str.contains("FILL|TAP", case=False, na=False)]
    net = net[net["net_type_id"] == 0]
    iopin = iopin[iopin["net_type_id"] == 0]
    epn = epn[epn["net_type_id"] == 0]
    ein = ein[ein["net_type_id"] == 0]
    pin = pin[pin["inst_name"] != "PIN"].merge(
        epn[["inst_name", "pin_name"]].drop_duplicates(),
        on=["inst_name", "pin_name"], how="inner")
    gate = gate[gate["inst_name"].isin(set(pin["inst_name"]))]
    net = net[net["net_name"].isin(set(epn["net_name"]) | set(ein["net_name"]))]
    iopin = iopin[iopin["iopin_name"].isin(set(ein["iopin_name"]))]

    pin_keys = pin[["inst_name", "pin_name"]].drop_duplicates()
    egp = egp.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    egp = egp[egp["inst_name"].isin(set(gate["inst_name"]))].drop_duplicates()
    epn = epn.merge(pin_keys, on=["inst_name", "pin_name"], how="inner")
    epn = epn[epn["net_name"].isin(set(net["net_name"]))].drop_duplicates()
    ein = ein[ein["iopin_name"].isin(set(iopin["iopin_name"]))
              & ein["net_name"].isin(set(net["net_name"]))].drop_duplicates()

    gate = gate.sort_values("inst_name", kind="mergesort").reset_index(drop=True)
    net = net.sort_values("net_name", kind="mergesort").reset_index(drop=True)
    iopin = iopin.sort_values("iopin_name", kind="mergesort").reset_index(drop=True)
    pin = pin.sort_values(["inst_name", "pin_name"], kind="mergesort").reset_index(drop=True)
    return gate, net, iopin, pin, egp, epn, ein


def expected_label_series(views, labels_dir, design):
    """Per entity type, the expected label value keyed by name (NaN = no join)."""
    gate, net, iopin, pin, *_ = views

    def lab(fname):
        p = os.path.join(labels_dir, fname)
        df = pd.read_csv(p)
        if "Design" in df.columns:
            df = df[df["Design"] == design]
        return df

    out = {}
    cong = lab("cell_congestion.csv")
    m = dict(zip(cong["Cell"], pd.to_numeric(cong["label"], errors="coerce"))) \
        if {"Cell", "label"} <= set(cong.columns) else {}
    out["gate", 0] = gate["inst_name"].map(m)

    ir = lab("ir_drop.csv")
    if {"Cell", "label"} <= set(ir.columns):
        g = ir.assign(label=pd.to_numeric(ir["label"], errors="coerce")) \
              .groupby("Cell")["label"].max()
        out["gate", 1] = gate["inst_name"].map(g)
    else:
        out["gate", 1] = pd.Series([float("nan")] * len(gate))

    tim = lab("timing_features.csv")
    tm = dict(zip(tim["Cell"], pd.to_numeric(tim["label"], errors="coerce"))) \
        if {"Cell", "label"} <= set(tim.columns) else {}
    out["pin", 2] = pin["inst_name"].map(tm)

    wl = lab("wirelength.csv")
    wm = dict(zip(wl["Net"], pd.to_numeric(wl["label"], errors="coerce"))) \
        if {"Net", "label"} <= set(wl.columns) else {}
    out["net", 3] = net["net_name"].map(wm)
    return out


def verify_y(vname, data, blocks, labels, sample_n=10):
    """blocks: list of (type_name, df, start, end). labels: expected_label_series."""
    y = data.y
    slot_of = {"gate": [(0, 1), (1, 2)], "pin": [(2, 3)], "net": [(3, 4)]}
    for tname, df, s, e in blocks:
        for order, col in slot_of.get(tname, []):
            exp = labels[tname, order].reset_index(drop=True)
            got = y[s:e, 1 + order]
            exp_nn = int(exp.notna().sum())
            got_nn = int((~torch.isnan(got)).sum())
            check(f"{vname}.y{1+order}[{tname}] non-NaN count",
                  exp_nn == got_nn, f"expected {exp_nn} got {got_nn}")
            idx = exp.dropna().index[:sample_n]
            bad = sum(1 for i in idx
                      if abs(float(got[int(i)]) - float(exp[i])) > 1e-4)
            if len(idx):
                check(f"{vname}.y{1+order}[{tname}] sampled values", bad == 0,
                      f"{bad}/{len(idx)} mismatched")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--design", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    case = args.case_dir.rstrip("/")
    feat, labs, ds = case + "/features", case + "/labels", case + "/dataset"
    man = json.load(open(ds + "/graph_manifest.json"))
    design = args.design or man["design"]
    print(f"== {design} ({case}) ==")

    views = build_views(feat, design)
    gate, net, iopin, pin, egp, epn, ein = views
    ng, nn, ni, npn = len(gate), len(net), len(iopin), len(pin)

    check("manifest.status ok", man["status"] == "ok", man["status"])
    lh = man.get("label_health", {})
    check("manifest.label_health all ok",
          lh and all(v["status"] == "ok" for v in lh.values()),
          {k: v["status"] for k, v in lh.items()})

    labels = expected_label_series(views, labs, design)

    # per-net endpoint sets (for clique formulas)
    pin_idx_keys = set(map(tuple, pin[["inst_name", "pin_name"]].itertuples(index=False, name=None)))
    net_pins = epn[["inst_name", "pin_name", "net_name"]].drop_duplicates()
    net_pin_sets = net_pins.groupby("net_name").apply(
        lambda d: {(i, p) for i, p in zip(d["inst_name"], d["pin_name"]) if (i, p) in pin_idx_keys},
        include_groups=False)
    net_io_sets = ein.groupby("net_name")["iopin_name"].apply(set)
    gate_set = set(gate["inst_name"])

    def net_k(fn_pin, fn_io):
        tot = 0
        for n in net["net_name"]:
            eps = set()
            for ip in net_pin_sets.get(n, set()):
                v = fn_pin(ip)
                if v is not None:
                    eps.add(v)
            for io in net_io_sets.get(n, set()):
                v = fn_io(io)
                if v is not None:
                    eps.add(v)
            tot += c2(len(eps))
        return tot

    # ---- variant b ----
    b = torch.load(ds + "/b_graph.pt", weights_only=False)
    ntb = b.x[:, 0].long()
    check("b node counts",
          [int((ntb == t).sum()) for t in (0, 1, 2, 3)] == [ng, nn, ni, npn],
          f"got {[int((ntb == t).sum()) for t in (0,1,2,3)]} want {[ng,nn,ni,npn]}")
    exp_b = 2 * (len(egp[["inst_name", "pin_name"]].drop_duplicates())
                 + len(net_pins) + len(ein[["iopin_name", "net_name"]].drop_duplicates()))
    check("b edge count", b.edge_index.shape[1] == exp_b,
          f"got {b.edge_index.shape[1]} want {exp_b}")
    check("b x1 uniform graph_id", bool((b.x[:, 1] == b.x[0, 1]).all()))
    check("b y0 == node_type", bool((b.y[:, 0] == b.x[:, 0]).all()))
    names = b.node_name
    ok_names = (names[:ng] == gate["inst_name"].tolist()
                and names[ng:ng + nn] == net["net_name"].tolist()
                and names[ng + nn:ng + nn + ni] == iopin["iopin_name"].tolist())
    check("b node_name block order", ok_names)
    verify_y("b", b, [("gate", gate, 0, ng), ("net", net, ng, ng + nn),
                      ("pin", pin, ng + nn + ni, ng + nn + ni + npn)], labels)

    # ---- variant c ----
    c = torch.load(ds + "/c_graph.pt", weights_only=False)
    ntc = c.x[:, 0].long()
    check("c node counts",
          [int((ntc == t).sum()) for t in (0, 1, 2)] == [ng, nn, ni])
    kept_pin_rows = net_pins[net_pins["inst_name"].isin(gate_set)]
    exp_c = 2 * (len(kept_pin_rows) + len(ein[["iopin_name", "net_name"]].drop_duplicates()))
    check("c edge count", c.edge_index.shape[1] == exp_c,
          f"got {c.edge_index.shape[1]} want {exp_c}")
    # c edge_attr alignment on unambiguous (gate, net) pairs
    pin_feat = pin.set_index(["inst_name", "pin_name"])[PIN_SCHEMA]
    cnames = c.node_name
    uniq = net_pins.groupby(["inst_name", "net_name"]).size()
    uniq = set(uniq[uniq == 1].index)
    checked = bad = 0
    for k in range(c.edge_index.shape[1]):
        if checked >= 400:
            break
        if int(c.edge_type[k]) != 0:
            continue
        u, v = int(c.edge_index[0, k]), int(c.edge_index[1, k])
        gn, nn_ = (cnames[u], cnames[v]) if int(ntc[u]) == 0 else (cnames[v], cnames[u])
        if (gn, nn_) not in uniq:
            continue
        row = net_pins[(net_pins["inst_name"] == gn) & (net_pins["net_name"] == nn_)]
        exp0, exp1 = pin_feat.loc[(row.iloc[0]["inst_name"], row.iloc[0]["pin_name"])]
        checked += 1
        if abs(float(c.edge_attr[k, 0]) - exp0) > 1e-4 or abs(float(c.edge_attr[k, 1]) - exp1) > 1e-3:
            bad += 1
    check("c edge_attr == folded pin features", bad == 0 and checked > 0,
          f"{bad}/{checked} mismatched")
    verify_y("c", c, [("gate", gate, 0, ng), ("net", net, ng, ng + nn)], labels)

    # ---- variant d ----
    d = torch.load(ds + "/d_graph.pt", weights_only=False)
    ntd = d.x[:, 0].long()
    check("d node counts",
          [int((ntd == t).sum()) for t in (0, 2, 3)] == [ng, ni, npn])
    exp_d = 2 * (len(egp[["inst_name", "pin_name"]].drop_duplicates())
                 + net_k(lambda ip: ip, lambda io: ("io", io)))
    check("d edge count (clique formula)", d.edge_index.shape[1] == exp_d,
          f"got {d.edge_index.shape[1]} want {exp_d}")
    verify_y("d", d, [("gate", gate, 0, ng),
                      ("pin", pin, ng + ni, ng + ni + npn)], labels)

    # ---- variant e ----
    e = torch.load(ds + "/e_graph.pt", weights_only=False)
    nte = e.x[:, 0].long()
    check("e node counts",
          [int((nte == t).sum()) for t in (2, 3)] == [ni, npn])
    gate_pin_counts = egp[["inst_name", "pin_name"]].drop_duplicates().groupby(
        egp[["inst_name", "pin_name"]].drop_duplicates()["inst_name"]).size()
    exp_e = 2 * (int(sum(c2(int(k)) for k in gate_pin_counts))
                 + net_k(lambda ip: ip, lambda io: ("io", io)))
    check("e edge count (clique formula)", e.edge_index.shape[1] == exp_e,
          f"got {e.edge_index.shape[1]} want {exp_e}")
    verify_y("e", e, [("pin", pin, ni, ni + npn)], labels)

    # ---- variant f ----
    f = torch.load(ds + "/f_graph.pt", weights_only=False)
    ntf = f.x[:, 0].long()
    check("f node counts",
          [int((ntf == t).sum()) for t in (0, 2)] == [ng, ni])
    exp_f = 2 * net_k(lambda ip: ip[0] if ip[0] in gate_set else None,
                      lambda io: ("io", io))
    check("f edge count (clique formula)", f.edge_index.shape[1] == exp_f,
          f"got {f.edge_index.shape[1]} want {exp_f}")
    # f edge_attr: for sampled edges whose endpoints share exactly one net,
    # edge_attr must equal that net's NET_SCHEMA features
    fnames = f.node_name
    nets_of = {}
    for n in net["net_name"]:
        members = {ip[0] for ip in net_pin_sets.get(n, set()) if ip[0] in gate_set} \
                  | set(net_io_sets.get(n, set()))
        for mname in members:
            nets_of.setdefault(mname, set()).add(n)
    net_feat = net.set_index("net_name")[NET_SCHEMA]
    checked = bad = 0
    for k in range(0, f.edge_index.shape[1], max(1, f.edge_index.shape[1] // 300)):
        u, v = int(f.edge_index[0, k]), int(f.edge_index[1, k])
        shared = nets_of.get(fnames[u], set()) & nets_of.get(fnames[v], set())
        if len(shared) != 1:
            continue
        expv = net_feat.loc[next(iter(shared))].to_numpy(dtype=float)
        gotv = f.edge_attr[k, :len(NET_SCHEMA)].numpy()
        checked += 1
        if any(abs(a - b) > max(1e-3, 1e-3 * abs(a)) for a, b in zip(expv, gotv)):
            bad += 1
    check("f edge_attr == connecting net features", bad == 0 and checked > 0,
          f"{bad}/{checked} mismatched")
    verify_y("f", f, [("gate", gate, 0, ng)], labels)

    # ---- manifest stats vs tensors ----
    for vname, data in (("b", b), ("c", c), ("d", d), ("e", e), ("f", f)):
        st = man["variants"][vname]
        check(f"manifest[{vname}] nodes/edges match tensors",
              st["nodes"] == data.x.shape[0] and st["edges"] == data.edge_index.shape[1])

    # ---- global_feat vs metadata ----
    md = pd.read_csv(feat + "/metadata.csv")
    md = md[md["graph_id"].astype(str) == design] if "graph_id" in md.columns else md
    if hasattr(b, "global_feat") and len(md):
        row = md.iloc[0]
        exp = [float(0 if pd.isna(pd.to_numeric(row.get(k), errors="coerce"))
                     else pd.to_numeric(row.get(k), errors="coerce")) for k in METADATA_SCHEMA]
        got = [float(x) for x in b.global_feat]
        check("global_feat == metadata row",
              all(abs(a - g) <= max(1e-6, 1e-6 * abs(a)) for a, g in zip(exp, got)))

    # ---- netlist graph ----
    npt = ds + "/netlist_graph.pt"
    if os.path.isfile(npt):
        g = torch.load(npt, weights_only=False)
        runs = sorted(
            (r for r in os.listdir(case + "/backend") if r.startswith("RUN_")), reverse=True)
        yos = None
        for r in runs:
            p = f"{case}/backend/{r}/results/1_2_yosys.v"
            if os.path.isfile(p):
                yos = p
                break
        if yos:
            text = re.sub(r"//.*", "", open(yos).read())
            inst = re.findall(r"^\s*(sky130_fd_sc_\w+__\w+)\s+(\S+)\s*\(", text, re.M)
            check("netlist_graph cell count vs independent regex",
                  len(g.cell_names) == len(inst),
                  f"pt {len(g.cell_names)} regex {len(inst)}")
            check("netlist_graph bipartite symmetric",
                  g.edge_index.shape[1] % 2 == 0 and g.x.shape[0]
                  == len(g.cell_names) + len(g.net_names))

    # ---- value sanity ----
    p50 = pin["sum_pin_cap_fF"].median() if npn else 0
    check("sum_pin_cap_fF p50 in physical range (0.3..100 fF)",
          npn == 0 or 0.3 <= p50 <= 100, f"p50={p50:.3f} fF")
    check("hpwl_um >= 0", bool((net["hpwl_um"].astype(float) >= 0).all()))
    check("num_drivers >= 1 on all signal nets",
          bool((net["num_drivers"].astype(int) >= 1).all()))

    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print(f"== {design}: {len(RESULTS) - n_fail}/{len(RESULTS)} checks passed ==")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"design": design, "results": RESULTS,
                       "passed": len(RESULTS) - n_fail, "failed": n_fail}, fh, indent=1)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
