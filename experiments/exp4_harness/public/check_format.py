#!/usr/bin/env python3
"""Public B--F graph format checker; no ground truth or R2G implementation imports.

    python3 check_format.py OUTPUT_DIR [--json REPORT.json]

OUTPUT_DIR contains dataset/ or the ten required b--f *_graph{,_homo}.pt
files. This checks loadability, documented shapes/dtypes/identities, label masks,
relations, reverse symmetry and value-preserving homo/hetero node conversion.
It cannot determine whether a physical value or connectivity is correct. Power
feature/label values are excluded. Images and four-stage graphs are out of scope.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

TYPES = {"b": ("gate", "net", "iopin", "pin"), "c": ("gate", "net", "iopin"),
         "d": ("gate", "iopin", "pin"), "e": ("iopin", "pin"), "f": ("gate", "iopin")}
TYPE_ID = {"gate": 0, "net": 1, "iopin": 2, "pin": 3}
FAMILIES = {"b": {0: "connects"}, "c": {0: "pin", 1: "iopin_connection"},
            "d": {0: "gate_pin", 1: "net"}, "e": {0: "gate", 1: "net"}, "f": {0: "net"}}


class Report:
    def __init__(self):
        self.checks = []

    def add(self, item, name, ok, detail=""):
        self.checks.append({"item": item, "check": name, "ok": bool(ok), "detail": str(detail)[:300]})
        return bool(ok)


def _same(torch, a, b):
    return a.shape == b.shape and bool(torch.all((a == b) | (torch.isnan(a) & torch.isnan(b))))


def _names_ok(names):
    return isinstance(names, (list, tuple)) and all(isinstance(n, str) for n in names) and len(set(names)) == len(names)


def _matrix(torch, value, rows, width, dtype):
    return isinstance(value, torch.Tensor) and value.dtype == dtype and tuple(value.shape) == (rows, width)


def _index(torch, value, n_source, n_dest):
    return (isinstance(value, torch.Tensor) and value.dtype == torch.int64 and value.dim() == 2
            and value.shape[0] == 2 and (value.numel() == 0 or
            (int(value.min()) >= 0 and int(value[0].max()) < n_source and int(value[1].max()) < n_dest)))


def _node_labels(r, item, torch, y, raw, node_types, view, offset):
    # IR-drop (slot 1) is excluded even when it contains arbitrary values/masks.
    selected = [offset + i for i in (0, 2, 3, 4)]
    r.add(item, "nonpower label/raw masks agree", bool((torch.isnan(y[:, selected]) == torch.isnan(raw[:, selected])).all()))
    allowed = {0: {"gate"}, 2: {"pin"}, 3: {"net"},
               4: ({"net"} if view in "bc" else {"pin"} if view in "de" else set())}
    for slot, nts in allowed.items():
        bad = [i for i, nt in enumerate(node_types) if nt not in nts]
        for twin, a in (("label", y), ("raw", raw)):
            r.add(item, f"{twin} slot {slot} off-type NaN", bool(torch.isnan(a[bad, offset + slot]).all()))
        r.add(item, f"slot {slot} has no infinity", not bool(torch.isinf(y[:, offset + slot]).any() | torch.isinf(raw[:, offset + slot]).any()))
    for slot in (2, 3, 4):
        a, b = y[:, offset + slot], raw[:, offset + slot]
        mask = torch.isfinite(b)
        r.add(item, f"slot {slot} log1p raw", bool(torch.all(b[mask] >= 0)) and bool(torch.allclose(a[mask], torch.log1p(b[mask]), atol=1e-6, rtol=1e-5)))


def _edge_rows(r, item, torch, store, count, homo, family=None, ids=None):
    width, offset = (6, 1) if homo else (5, 0)
    checks = [r.add(item, f"{key} float32 [E,{w}]", _matrix(torch, getattr(store, key, None), count, w, torch.float32))
              for key, w in (("edge_attr", 8), ("edge_y", width), ("edge_y_raw", width))]
    if not all(checks):
        return
    a, y, raw = store.edge_attr, store.edge_y, store.edge_y_raw
    if homo:
        r.add(item, "edge label type columns", ids is not None and bool((y[:, 0] == ids).all() and (raw[:, 0] == ids).all()))
    r.add(item, "ground cap absent on folded edges", bool(torch.isnan(y[:, offset + 4]).all() and torch.isnan(raw[:, offset + 4]).all()))
    selected = [offset + i for i in (0, 2, 3, 4)]
    r.add(item, "nonpower edge label/raw masks agree", bool((torch.isnan(y[:, selected]) == torch.isnan(raw[:, selected])).all()))
    if family is not None:
        allowed = {"gate": 0, "pin": 2, "net": 3}.get(family)
        for slot in (0, 2, 3, 4):
            if slot != allowed:
                r.add(item, f"edge slot {slot} off-family NaN", bool(torch.isnan(y[:, offset + slot]).all() and torch.isnan(raw[:, offset + slot]).all()))
    if homo:
        attr_cols = [i for i in range(8) if i != 2]  # gate cell_power; other col 2 checked by hidden truth
        label_cols = [0] + selected
        r.add(item, "fwd/rev semantic rows identical", _same(torch, a[0::2, attr_cols], a[1::2, attr_cols])
              and _same(torch, y[0::2, label_cols], y[1::2, label_cols])
              and _same(torch, raw[0::2, label_cols], raw[1::2, label_cols]))


def _homo(r, item, torch, view, g):
    if not r.add(item, "Data object", type(g).__name__ == "Data"):
        return None
    if not r.add(item, "x matrix", isinstance(getattr(g, "x", None), torch.Tensor) and g.x.dim() == 2):
        return None
    n = g.x.shape[0]
    xok = r.add(item, "x float32 [N,10]", _matrix(torch, g.x, n, 10, torch.float32))
    yok = all([r.add(item, f"{k} float32 [N,6]", _matrix(torch, getattr(g, k, None), n, 6, torch.float32)) for k in ("y", "y_raw")])
    names = getattr(g, "node_name", None)
    namesok = r.add(item, "string node_name[N]", isinstance(names, (list, tuple)) and len(names) == n
                    and all(isinstance(name, str) for name in names))
    nts = None
    if xok:
        ids = g.x[:, 0]
        nt = ids.long()
        valid = bool((ids == nt).all()) and set(nt.tolist()) <= {TYPE_ID[t] for t in TYPES[view]}
        r.add(item, "integral documented node types", valid)
        if valid:
            nts = [{v: k for k, v in TYPE_ID.items()}[int(k)] for k in nt]
            r.add(item, "type blocks ordered", nt.tolist() == sorted(nt.tolist()))
            if namesok:
                for node_type in TYPES[view]:
                    block_names = [names[i] for i, found_type in enumerate(nts) if found_type == node_type]
                    r.add(item, f"{node_type} names unique within type", _names_ok(block_names))
            if yok:
                r.add(item, "label type columns equal x", bool((g.y[:, 0] == ids).all() and (g.y_raw[:, 0] == ids).all()))
                _node_labels(r, item, torch, g.y, g.y_raw, nts, view, 1)
    ei = getattr(g, "edge_index", None)
    if r.add(item, "edge_index int64 [2,E] in range", _index(torch, ei, n, n)):
        e = ei.shape[1]
        r.add(item, "fwd/rev interleaving", e % 2 == 0 and bool((ei[:, 0::2] == ei.flip(0)[:, 1::2]).all()))
        if view != "b":
            ids = getattr(g, "edge_type", None)
            idok = r.add(item, "edge_type int64 [E]", isinstance(ids, torch.Tensor) and ids.dtype == torch.int64 and tuple(ids.shape) == (e,))
            if idok:
                r.add(item, "documented edge families", set(ids.tolist()) <= set(FAMILIES[view]))
            _edge_rows(r, item, torch, g, e, True, ids=ids if idok else None)
    rei = getattr(g, "rc_edge_index", None)
    if r.add(item, "rc_edge_index int64 [2,R] in range", _index(torch, rei, n, n)):
        q = rei.shape[1]
        r.add(item, "RC fwd/rev interleaving", q % 2 == 0 and bool((rei[:, 0::2] == rei.flip(0)[:, 1::2]).all()))
        rt = getattr(g, "rc_edge_type", None)
        rtok = r.add(item, "rc_edge_type int64 [R]", isinstance(rt, torch.Tensor) and rt.dtype == torch.int64 and tuple(rt.shape) == (q,))
        shapes = [r.add(item, f"{k} float32 [R,3]", _matrix(torch, getattr(g, k, None), q, 3, torch.float32)) for k in ("rc_edge_y", "rc_edge_y_raw")]
        if rtok and all(shapes):
            r.add(item, "documented RC types", set(rt.tolist()) <= {0, 1})
            r.add(item, "RC type column", bool((g.rc_edge_y[:, 0] == rt).all() and (g.rc_edge_y_raw[:, 0] == rt).all()))
            for twin in (g.rc_edge_y, g.rc_edge_y_raw):
                r.add(item, "RC off-type NaN", bool(torch.isnan(twin[rt == 0, 2]).all() and torch.isnan(twin[rt == 1, 1]).all()))
            r.add(item, "RC reverse rows equal", _same(torch, g.rc_edge_y[0::2], g.rc_edge_y[1::2]) and _same(torch, g.rc_edge_y_raw[0::2], g.rc_edge_y_raw[1::2]))
        if view == "f":
            r.add(item, "F has empty RC", q == 0)
    return nts


def _hetero(r, item, torch, view, h, homo, homo_types):
    if not r.add(item, "HeteroData object", type(h).__name__ == "HeteroData"):
        return
    r.add(item, "documented node stores", set(h.node_types) == set(TYPES[view]))
    r.add(item, "graph_kind hetero", getattr(h, "graph_kind", None) == "hetero")
    counts = {}
    for nt in h.node_types:
        store = h[nt]
        x = getattr(store, "x", None)
        n = x.shape[0] if isinstance(x, torch.Tensor) and x.dim() == 2 else -1
        counts[nt] = n
        xok = r.add(item, f"{nt}.x float32 [N,9]", _matrix(torch, x, n, 9, torch.float32))
        yok = all([r.add(item, f"{nt}.{key} float32 [N,5]", _matrix(torch, getattr(store, key, None), n, 5, torch.float32)) for key in ("y", "y_raw")])
        names = getattr(store, "node_name", None)
        r.add(item, f"{nt}.unique string node_name[N]", _names_ok(names) and len(names) == n)
        if yok:
            _node_labels(r, item + ":" + nt, torch, store.y, store.y_raw, [nt] * n, view, 0)
        if homo_types is not None and xok and yok:
            idx = [i for i, t in enumerate(homo_types) if t == nt]
            valid = len(idx) == n and list(names or []) == [homo.node_name[i] for i in idx]
            r.add(item, f"{nt} identities equal homo", valid)
            if valid:
                cols = [i for i in range(9) if not (nt == "gate" and i == 3)]
                r.add(item, f"{nt} nonpower features equal homo", _same(torch, store.x[:, cols], homo.x[idx, 1:][:, cols]))
                for key in ("y", "y_raw"):
                    r.add(item, f"{nt}.{key} nonpower values equal homo", _same(torch, store[key][:, [0, 2, 3, 4]], homo[key][idx, 1:][:, [0, 2, 3, 4]]))
    records = Counter()
    for source, family, dest in h.edge_types:
        store = h[(source, family, dest)]
        key = item + ":" + "|".join((source, family, dest))
        isrc = family.startswith("rc_")
        allowed = {"rc_coupling", "rc_resistance"} if isrc else set(FAMILIES[view].values())
        r.add(key, "documented relation", family in allowed and (not isrc or view != "f"))
        ei = getattr(store, "edge_index", None)
        if not r.add(key, "edge_index int64 [2,E] in range", _index(torch, ei, counts.get(source, -1), counts.get(dest, -1))):
            continue
        count = ei.shape[1]
        if isrc:
            shapeok = all([r.add(key, f"{k} float32 [E,2]", _matrix(torch, getattr(store, k, None), count, 2, torch.float32)) for k in ("rc_edge_y", "rc_edge_y_raw")])
            if not shapeok:
                continue
            arrays = (store.rc_edge_y, store.rc_edge_y_raw)
            off = 1 if family == "rc_coupling" else 0
            r.add(key, "RC off-type NaN", all(bool(torch.isnan(a[:, off]).all()) for a in arrays))
        else:
            if view != "b":
                _edge_rows(r, key, torch, store, count, False, family=family)
                if any(not hasattr(store, name) for name in ("edge_attr", "edge_y", "edge_y_raw")):
                    continue
                arrays = (store.edge_attr, store.edge_y, store.edge_y_raw)
            else:
                arrays = ()
        # Reverse relations may serialize edges in a different order. Compare the
        # full semantic row multiset, excluding only the known power columns.
        for i in range(count):
            payload = []
            for j, a in enumerate(arrays):
                row = a[i].tolist()
                omit = ({2} if not isrc and j == 0 and family == "gate" else {1} if not isrc and j > 0 else set())
                payload.extend("NaN" if value != value else value for k, value in enumerate(row) if k not in omit)
            records[(source, family, dest, int(ei[0, i]), int(ei[1, i]), tuple(payload))] += 1
    reverse = Counter({(d, f, s, b, a, payload): count for (s, f, d, a, b, payload), count in records.items()})
    r.add(item, "semantic reverse-relation symmetry", records == reverse)


def check_output(output_dir):
    """Return a complete public report; malformed files produce failed checks."""
    import torch

    root = Path(output_dir)
    root = root / "dataset" if (root / "dataset").is_dir() else root
    report = Report()
    for view in "bcdef":
        homo, types = None, None
        for form, suffix in (("homo", "_graph_homo.pt"), ("hetero", "_graph.pt")):
            item, path = f"{view}.{form}", root / f"{view}{suffix}"
            if not report.add(item, "required file exists", path.is_file(), path.name):
                continue
            try:
                graph = torch.load(path, map_location="cpu", weights_only=False)
                if form == "homo":
                    homo = graph
                    types = _homo(report, item, torch, view, graph)
                else:
                    _hetero(report, item, torch, view, graph, homo, types)
            except Exception as exc:
                report.add(item, "load/format exception", False, f"{type(exc).__name__}: {exc}")
    failed = [check for check in report.checks if not check["ok"]]
    return {"ok": not failed, "n_checks": len(report.checks), "failed": failed, "checks": report.checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = check_output(args.output_dir)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
