#!/usr/bin/env python3
"""Public FORMAT checker for the graph-conversion contract (CONTRACT.md v1.0).

    python3 check_format.py <out_dir> [--json report.json]

Checks only what the contract states about the *form* of the output: the files load, the
required fields exist with the right dtypes and shapes, the node-type blocks are in order and
name-sorted, every label slot is NaN exactly where the contract says a label does not apply,
the raw/normalized twins agree on NaN placement and on the pure log1p identities, the edge
lists are interleaved forward/reverse pairs between the allowed node types, and views b and c
agree on the nodes they share. It never reads the design inputs and knows nothing about the
correct values: passing it says the output is well-formed, not that it is right.

Exit code 0 iff every check passes.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import torch

GATE, NET, IOPIN, PIN = 0, 1, 2, 3
REQUIRED = {
    "b": ["x", "y", "y_raw", "edge_index", "node_name"],
    "c": ["x", "y", "y_raw", "edge_index", "edge_attr", "edge_type", "edge_y", "edge_y_raw", "node_name"],
}
BLOCKS = {"b": [GATE, NET, IOPIN, PIN], "c": [GATE, NET, IOPIN]}
# per node type: label slots that must be NaN (y/y_raw columns 1..5); slot 2 (IR drop) is
# out of scope -> any value allowed on gates
NAN_SLOTS = {"b": {GATE: [3, 4, 5], NET: [1, 2, 3], IOPIN: [1, 2, 3, 4, 5], PIN: [1, 2, 4, 5]},
             "c": {GATE: [3, 4, 5], NET: [1, 2, 3], IOPIN: [1, 2, 3, 4, 5]}}
FINITE_SLOTS = {"b": {GATE: [1], NET: [4], PIN: [3]}, "c": {GATE: [1], NET: [4]}}
ZERO_PAD_FROM = {GATE: 9, NET: 10, IOPIN: 6, PIN: 4}     # x columns >= this must be 0
B_EDGE_TYPES = {(GATE, PIN), (PIN, GATE), (PIN, NET), (NET, PIN), (IOPIN, NET), (NET, IOPIN)}
C_EDGE_TYPES = {(GATE, NET): 0, (NET, GATE): 0, (IOPIN, NET): 1, (NET, IOPIN): 1}


class Report:
    def __init__(self):
        self.checks = []

    def add(self, view, name, ok, detail=""):
        self.checks.append({"view": view, "check": name, "ok": bool(ok), "detail": str(detail)[:500]})
        return ok

    @property
    def ok(self):
        return all(c["ok"] for c in self.checks)


def _canon(s):
    return s.replace("\\", "")


def check_view(rep, v, data):
    for k in REQUIRED[v]:
        if not rep.add(v, f"field {k} present", k in data, "" if k in data else "missing"):
            return None
    x, y, yr, ei, names = data.x, data.y, data.y_raw, data.edge_index, data.node_name
    ok = rep.add(v, "x float32 [N,10]", torch.is_tensor(x) and x.dtype == torch.float32 and x.dim() == 2
                 and x.shape[1] == 10, f"{getattr(x, 'dtype', type(x))} {tuple(getattr(x, 'shape', ()))}")
    if not ok:
        return None
    n = x.shape[0]
    for nm, t in (("y", y), ("y_raw", yr)):
        rep.add(v, f"{nm} float32 [N,6]", torch.is_tensor(t) and t.dtype == torch.float32 and tuple(t.shape) == (n, 6),
                f"{getattr(t, 'dtype', type(t))} {tuple(getattr(t, 'shape', ()))}")
    rep.add(v, "node_name list[str] of length N", isinstance(names, (list, tuple)) and len(names) == n
            and all(isinstance(s, str) and s for s in names), f"type={type(names).__name__} len={len(names) if hasattr(names, '__len__') else '?'}")
    rep.add(v, "edge_index int64 [2,E]", torch.is_tensor(ei) and ei.dtype == torch.int64 and ei.dim() == 2 and ei.shape[0] == 2,
            f"{getattr(ei, 'dtype', type(ei))} {tuple(getattr(ei, 'shape', ()))}")
    if not rep.ok:
        return None
    rep.add(v, "x finite", bool(torch.isfinite(x).all()), f"{int((~torch.isfinite(x)).sum())} non-finite")
    nt = x[:, 0]
    rep.add(v, "x[:,0] is an integer node type", bool(((nt == nt.round()) & (nt >= 0) & (nt <= 3)).all()))
    t = nt.long()
    present = sorted(set(t.tolist()))
    rep.add(v, "node types present", present == BLOCKS[v], f"present {present}, expected {BLOCKS[v]}")
    order = [BLOCKS[v].index(int(a)) if int(a) in BLOCKS[v] else 99 for a in t.tolist()]
    rep.add(v, "type blocks in contract order", all(a <= b for a, b in zip(order, order[1:])))
    rep.add(v, "x[:,1] constant", n == 0 or bool((x[:, 1] == x[0, 1]).all()))
    for typ, col in ZERO_PAD_FROM.items():
        m = t == typ
        if m.any() and col < 10:
            rep.add(v, f"type {typ} zero padding x[:, {col}:]", bool((x[m, col:] == 0).all()))
    # names: unique, sorted within each block, pin names inst/pin over gate names
    blocks = {typ: [names[i] for i in range(n) if int(t[i]) == typ] for typ in BLOCKS[v]}
    for typ, lst in blocks.items():
        rep.add(v, f"type {typ} names unique", len(set(map(_canon, lst))) == len(lst),
                f"{len(lst) - len(set(map(_canon, lst)))} duplicates")
        if typ == PIN:
            keys = [tuple(s.rsplit("/", 1)) if "/" in s else (s, "") for s in lst]
            rep.add(v, "pin names are inst/pin", all(len(k) == 2 and k[1] for k in keys))
            rep.add(v, "pin block sorted by (inst, pin)", keys == sorted(keys))
            gates = set(map(_canon, blocks[GATE]))
            bad = [s for s in lst if _canon(s.rsplit("/", 1)[0]) not in gates]
            rep.add(v, "every pin's inst is a gate node", not bad, bad[:3])
        else:
            rep.add(v, f"type {typ} block sorted by name", lst == sorted(lst))
    # labels
    rep.add(v, "y[:,0] == node type", bool((y[:, 0] == nt).all()))
    rep.add(v, "y_raw[:,0] == node type", bool((yr[:, 0] == nt).all()))
    rep.add(v, "y and y_raw have identical NaN placement", bool((torch.isnan(y) == torch.isnan(yr)).all()),
            f"{int((torch.isnan(y) != torch.isnan(yr)).sum())} cells differ")
    for typ, slots in NAN_SLOTS[v].items():
        m = t == typ
        for s in slots:
            rep.add(v, f"type {typ} y[:,{s}] NaN", bool(torch.isnan(y[m, s]).all()), f"{int((~torch.isnan(y[m, s])).sum())} non-NaN")
    for typ, slots in FINITE_SLOTS[v].items():
        m = t == typ
        for s in slots:
            rep.add(v, f"type {typ} y[:,{s}] finite", bool(torch.isfinite(y[m, s]).all()), f"{int((~torch.isfinite(y[m, s])).sum())} not finite")
    for s in (3, 4, 5):
        m = torch.isfinite(yr[:, s])
        if m.any():
            d = (y[m, s].double() - torch.log1p(yr[m, s].double())).abs()
            rep.add(v, f"y[:,{s}] == log1p(y_raw[:,{s}])", bool((d <= 1e-5 + 1e-5 * y[m, s].double().abs()).all()), f"max diff {float(d.max()):.3g}")
            rep.add(v, f"y_raw[:,{s}] >= 0", bool((yr[m, s] >= 0).all()))
    m = torch.isfinite(yr[:, 1])
    if m.any():
        rep.add(v, "congestion raw >= 0 and label >= 0", bool((yr[m, 1] >= 0).all() and (y[m, 1] >= 0).all()))
    # edges
    e = ei.shape[1]
    if e:
        rep.add(v, "edge_index in range", bool((ei >= 0).all() and (ei < n).all()))
        if not rep.ok:
            return None
        rep.add(v, "no self loops", bool((ei[0] != ei[1]).all()))
        rep.add(v, "E even, columns interleaved [fwd, rev, ...]", e % 2 == 0 and bool((ei[:, 1::2] == ei.flip(0)[:, 0::2]).all()))
        pairs = set(zip(t[ei[0]].tolist(), t[ei[1]].tolist()))
        allowed = B_EDGE_TYPES if v == "b" else set(C_EDGE_TYPES)
        rep.add(v, "edges only between allowed node types", pairs <= allowed, sorted(pairs - allowed))
        if v == "b":
            rep.add(v, "no duplicate directed edges", len(set(zip(ei[0].tolist(), ei[1].tolist()))) == e)
            deg = torch.bincount(ei[0], minlength=n)
            pin = t == PIN
            rep.add(v, "every pin has exactly one gate and one net neighbour", bool((deg[pin] == 2).all()))
    if v == "c":
        et, ea, ey, eyr = data.edge_type, data.edge_attr, data.edge_y, data.edge_y_raw
        rep.add(v, "edge_type int64 [E]", torch.is_tensor(et) and et.dtype == torch.int64 and tuple(et.shape) == (e,))
        rep.add(v, "edge_attr float32 [E,8]", torch.is_tensor(ea) and ea.dtype == torch.float32 and tuple(ea.shape) == (e, 8))
        for nm, tt in (("edge_y", ey), ("edge_y_raw", eyr)):
            rep.add(v, f"{nm} float32 [E,6]", torch.is_tensor(tt) and tt.dtype == torch.float32 and tuple(tt.shape) == (e, 6))
        if not rep.ok or not e:
            return t
        exp = torch.tensor([C_EDGE_TYPES.get((int(a), int(b)), -1) for a, b in zip(t[ei[0]].tolist(), t[ei[1]].tolist())])
        rep.add(v, "edge_type matches endpoint types", bool((et == exp).all()))
        rep.add(v, "directed pairs carry identical rows", all(bool(torch.equal(a[0::2], a[1::2])) for a in (et, ea))
                and all(bool(((a[0::2] == a[1::2]) | (torch.isnan(a[0::2]) & torch.isnan(a[1::2]))).all()) for a in (ey, eyr)))
        rep.add(v, "edge_attr finite", bool(torch.isfinite(ea).all()))
        rep.add(v, "edge_attr[:,2:] == 0", bool((ea[:, 2:] == 0).all()))
        rep.add(v, "iopin edges have zero edge_attr", bool((ea[et == 1] == 0).all()))
        rep.add(v, "edge_y[:,0] == edge_type", bool((ey[:, 0] == et.float()).all() and (eyr[:, 0] == et.float()).all()))
        rep.add(v, "edge_y / edge_y_raw identical NaN placement", bool((torch.isnan(ey) == torch.isnan(eyr)).all()))
        rep.add(v, "edge_y[:,1,2,4,5] NaN", bool(torch.isnan(ey[:, [1, 2, 4, 5]]).all()))
        rep.add(v, "pin edges: edge_y[:,3] finite", bool(torch.isfinite(ey[et == 0, 3]).all()))
        rep.add(v, "iopin edges: edge_y[:,3] NaN", bool(torch.isnan(ey[et == 1, 3]).all()))
        m = et == 0
        if m.any():
            d = (ey[m, 3].double() - torch.log1p(eyr[m, 3].double())).abs()
            rep.add(v, "edge_y[:,3] == log1p(edge_y_raw[:,3])", bool((d <= 1e-5 + 1e-5 * ey[m, 3].double().abs()).all()))
    return t


def cross_check(rep, b, c, tb, tc):
    nb = [i for i in range(len(b.node_name)) if int(tb[i]) != PIN]
    names_b = [b.node_name[i] for i in nb]
    if not rep.add("b+c", "c nodes == b's gate/net/iopin nodes, same order", names_b == list(c.node_name)):
        return
    idx = torch.tensor(nb, dtype=torch.long)
    rep.add("b+c", "c x == b x on shared nodes", bool(torch.equal(b.x[idx, 0], c.x[:, 0]) and torch.equal(b.x[idx, 2:], c.x[:, 2:])))
    for s in (1, 2, 4, 5):
        for nm in ("y", "y_raw"):
            a, z = getattr(b, nm)[idx, s], getattr(c, nm)[:, s]
            rep.add("b+c", f"c {nm}[:,{s}] == b {nm}[:,{s}]", bool(((a == z) | (torch.isnan(a) & torch.isnan(z))).all()))
    npin = int((tb == PIN).sum())
    if torch.is_tensor(c.edge_type):
        rep.add("b+c", "c has one undirected pin edge per b pin node", int((c.edge_type == 0).sum()) == 2 * npin,
                f"{int((c.edge_type == 0).sum()) // 2} vs {npin}")
        nio = int((tc == IOPIN).sum())
        rep.add("b+c", "c has at most one undirected iopin edge per iopin node", int((c.edge_type == 1).sum()) <= 2 * nio)
        tb_ = tb.long()
        nio_b = int(((tb_[b.edge_index[0]] == IOPIN) | (tb_[b.edge_index[1]] == IOPIN)).sum())
        rep.add("b+c", "b and c have the same number of iopin-net edges", nio_b == int((c.edge_type == 1).sum()),
                f"b {nio_b} vs c {int((c.edge_type == 1).sum())} directed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rep = Report()
    loaded, types = {}, {}
    for v in "bc":
        p = a.out_dir / f"{v}_graph.pt"
        if not rep.add(v, "file exists", p.is_file(), str(p)):
            continue
        try:
            d = torch.load(p, map_location="cpu", weights_only=False)
        except Exception as exc:                       # noqa: BLE001
            rep.add(v, "torch.load", False, f"{type(exc).__name__}: {exc}")
            continue
        if not rep.add(v, "is torch_geometric Data", type(d).__name__ == "Data", type(d).__name__):
            continue
        before = len([c for c in rep.checks if not c["ok"]])
        types[v] = check_view(rep, v, d)
        if len([c for c in rep.checks if not c["ok"]]) == before:
            loaded[v] = d
    if set(loaded) == {"b", "c"}:
        cross_check(rep, loaded["b"], loaded["c"], types["b"], types["c"])
    failed = [c for c in rep.checks if not c["ok"]]
    out = {"ok": rep.ok, "n_checks": len(rep.checks), "n_failed": len(failed), "failed": failed}
    if a.json:
        a.json.write_text(json.dumps({**out, "checks": rep.checks}, indent=1))
    print(json.dumps(out, indent=1))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
