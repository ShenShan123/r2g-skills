#!/usr/bin/env python3
"""Negative controls for scoring/defgraph_check.py: every corruption of a clean output must make
the intended group fail, and the clean output must pass everything.

    negative_controls.py <truth.pkl> <clean_out_dir> <scratch_dir>
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
CHECK = HERE.parent / "scoring" / "defgraph_check.py"


def corrupt(kind, b, c):
    t = b.x[:, 0].long()
    g = (t == 0).nonzero().view(-1)
    n = (t == 1).nonzero().view(-1)
    p = (t == 3).nonzero().view(-1)
    if kind == "congestion_swap":          # D5-like label misalignment
        i, j = int(g[0]), int(g[-1])
        for d in (b, c):
            d.y[[i, j], 1] = d.y[[j, i], 1]
        return "L_congestion"
    if kind == "pincap_x1000":
        b.x[p, 3] *= 1000
        return "F_pin"
    if kind == "drop_edge_pair":
        b.edge_index = b.edge_index[:, 2:]
        return "EDGES_b"
    if kind == "gcap_nan_one":
        k = int(n[0])
        b.y[k, 5] = math.nan; b.y_raw[k, 5] = math.nan
        return "L_ground_cap"
    if kind == "wl_plus_10pct":
        b.y_raw[n, 4] *= 1.1
        return "L_wirelength"
    if kind == "timing_shift_1ps":
        b.y_raw[p, 3] += 0.001
        return "L_timing"
    if kind == "coords_x2":
        b.x[g, 5] *= 2
        return "F_gate_position"
    if kind == "celltype_unknown":
        b.x[int(g[0]), 2] = 999
        return "F_gate_categorical"
    if kind == "rename_net":
        b.node_name[int(n[0])] = "bogus_net"
        return "ID_nets"
    if kind == "order_swap":
        i, j = int(g[0]), int(g[1])
        perm = list(range(b.x.shape[0])); perm[i], perm[j] = j, i
        perm_t = torch.tensor(perm)
        inv = torch.argsort(perm_t)
        b.x, b.y, b.y_raw = b.x[perm_t], b.y[perm_t], b.y_raw[perm_t]
        b.node_name = [b.node_name[k] for k in perm]
        b.edge_index = inv[b.edge_index]
        return "ORDER"
    if kind == "c_edge_attr_cap":
        c.edge_attr[c.edge_type == 0, 1] += 1.0
        return "F_c_pin_edges"
    raise ValueError(kind)


KINDS = ["congestion_swap", "pincap_x1000", "drop_edge_pair", "gcap_nan_one", "wl_plus_10pct",
         "timing_shift_1ps", "coords_x2", "celltype_unknown", "rename_net", "order_swap", "c_edge_attr_cap"]


def run(truth, out_dir, res_json):
    subprocess.run([sys.executable, str(CHECK), "score", str(truth), str(out_dir), str(res_json)],
                   check=True, capture_output=True)
    return json.loads(Path(res_json).read_text())


def main():
    truth, clean, scratch = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    scratch.mkdir(parents=True, exist_ok=True)
    rows = []
    r = run(truth, clean, scratch / "clean.json")
    rows.append(("clean", None, r["all_groups_pass"], r.get("failed_groups")))
    for k in KINDS:
        d = scratch / k
        shutil.rmtree(d, ignore_errors=True); d.mkdir()
        b = torch.load(clean / "b_graph.pt", weights_only=False)
        c = torch.load(clean / "c_graph.pt", weights_only=False)
        want = corrupt(k, b, c)
        torch.save(b, d / "b_graph.pt"); torch.save(c, d / "c_graph.pt")
        r = run(truth, d, d / "score.json")
        rows.append((k, want, want in (r.get("failed_groups") or []), r.get("failed_groups")))
    ok = rows[0][2] and all(x[2] for x in rows[1:])
    for row in rows:
        print(*row)
    print("NEGATIVE_CONTROLS_OK" if ok else "NEGATIVE_CONTROLS_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
