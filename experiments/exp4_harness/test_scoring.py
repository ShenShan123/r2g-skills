"""Small semantic evaluator controls; no physical-flow runs or model calls."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scoring import LABEL_TOLS, expected_labels, folded_checks, maximum_matches, score_design, score_labels, within


def tiny_truth():
    return {
        "design": "synthetic", "platform": "nangate45",
        "gates": ["g1", "g2"], "nets": ["n1", "n2"], "pins": [("g1", "A"), ("g2", "Z")], "iopins": ["out"],
        "congestion": {"g1": (0.1, 0.3), "g2": (0.2, 0.4)},
        "path_delay": {"g1": 1.0, "g2": 2.0}, "wirelength_um": {"n1": 10.0, "n2": 20.0},
        "ground_cap_fF": {"n1": 1.5, "n2": 2.5}, "rc_coupling_nets": {("n1", "n2"): 0.5},
        "rc_res_pairs": {(("g1", "A"), ("g2", "Z")): 100.0},
    }


def tiny_claim(truth):
    return {
        "nodes": {"gate": list(truth["gates"]), "net": list(truth["nets"]), "pin": list(truth["pins"])},
        "y_raw": {"gate": {"congestion": [0.1, 0.2]}, "pin": {"timing": [1.0, 2.0]},
                  "net": {"wirelength": [10.0, 20.0], "ground_cap": [1.5, 2.5]}},
        "rc": [("rc_coupling", ("net", "n1"), ("net", "n2"), (0, math.nan), (0.5, math.nan)),
               ("rc_coupling", ("net", "n2"), ("net", "n1"), (0, math.nan), (0.5, math.nan)),
               ("rc_resistance", ("pin", ("g1", "A")), ("pin", ("g2", "Z")), (math.nan, 0), (math.nan, 100.0)),
               ("rc_resistance", ("pin", ("g2", "Z")), ("pin", ("g1", "A")), (math.nan, 0), (math.nan, 100.0))],
    }


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.truth = tiny_truth()
        self.claim = tiny_claim(self.truth)

    def test_perfect_and_reverse_edge_deduplication(self):
        result = score_labels(self.truth, self.claim)
        self.assertEqual(result["label_accuracy"], 1)
        self.assertEqual(result["labels"]["coupling_cap"]["expected"], 1)
        self.assertEqual(result["labels"]["resistance"]["matched"], 1)

    def test_missing_output_has_fixed_zero_denominators(self):
        result = score_labels(self.truth, None)
        self.assertEqual(result["label_accuracy"], 0)
        self.assertEqual(result["labels"]["wirelength"]["expected"], 2)
        self.assertEqual(result["labels"]["wirelength"]["matched"], 0)

    def test_missing_entity_cannot_shrink_value_denominator(self):
        self.claim["nodes"]["net"] = ["n1"]
        self.claim["y_raw"]["net"]["wirelength"] = [10]
        self.claim["y_raw"]["net"]["ground_cap"] = [1.5]
        result = score_labels(self.truth, self.claim)
        self.assertEqual(result["labels"]["wirelength"]["rate"], 0.5)
        self.assertEqual(result["labels"]["wirelength"]["missing"], 1)

    def test_duplicate_identity_never_gets_best_of_credit(self):
        self.claim["nodes"]["net"] = ["n1", "n1", "n2"]
        self.claim["y_raw"]["net"]["wirelength"] = [10, 999, 20]
        result = score_labels(self.truth, self.claim)
        self.assertEqual(result["labels"]["wirelength"]["matched"], 1)
        self.assertEqual(result["labels"]["wirelength"]["duplicate_expected_keys"], 1)

    def test_wrong_nan_extra_and_missing_values_are_not_accuracy_credit(self):
        self.claim["nodes"]["gate"] += ["unknown"]
        self.claim["y_raw"]["gate"]["congestion"] = [0.1, math.nan, 0.2]
        self.claim["y_raw"]["pin"]["timing"] = []
        result = score_labels(self.truth, self.claim)
        self.assertEqual(result["labels"]["congestion"]["matched"], 1)
        self.assertEqual(result["labels"]["congestion"]["extra_keys"], 1)
        self.assertEqual(result["labels"]["timing"]["matched"], 0)

    def test_nonfinite_truth_and_empty_rc_not_free_points(self):
        self.truth["congestion"]["g2"] = (math.nan, math.nan)
        self.truth["rc_coupling_nets"] = {}
        self.truth["rc_res_pairs"] = {}
        result = score_labels(self.truth, self.claim)
        self.assertEqual(result["n_applicable_labels"], 4)
        self.assertIsNone(result["labels"]["coupling_cap"]["rate"])
        self.assertEqual(result["labels"]["congestion"]["expected"], 1)
        self.assertEqual(result["labels"]["congestion"]["extra_keys"], 0)
        self.assertEqual(result["labels"]["congestion"]["masked_truth_keys"], 1)

    def test_label_macro_is_not_pooled_by_entity_count(self):
        self.claim["y_raw"]["gate"]["congestion"] = [0, 0]
        result = score_labels(self.truth, self.claim)
        self.assertAlmostEqual(result["label_accuracy"], 5 / 6)

    def test_only_finite_values_within_frozen_tolerance_pass(self):
        tol = LABEL_TOLS["timing"]
        self.assertTrue(within(1.0 + 0.5 * tol[0], 1.0, tol))
        self.assertFalse(within(1.0 + 2 * tol[0], 1.0, tol))
        self.assertFalse(within(math.inf, math.inf, tol))
        self.assertFalse(within(math.nan, math.nan, tol))


class ParallelRowTests(unittest.TestCase):
    def test_matching_uses_augmenting_path(self):
        # Greedy first-fit would strand expected row 1.
        self.assertEqual(maximum_matches([0, 1], [0, 1], lambda c, t: t == 0 or c == 0), 2)

    def test_complete_parallel_multiset_accepts_permutation(self):
        nan = math.nan
        rows = {
            "n1": ([0] * 8, [nan, nan, nan, 1.00001, nan], [nan, nan, nan, 10.00001, nan]),
            "n2": ([0] * 8, [nan, nan, nan, 1.00002, nan], [nan, nan, nan, 10.00002, nan]),
        }
        legacy = SimpleNamespace(
            truth_edge_rows=lambda _truth, _view, _fam, ent: rows[ent],
            attr_tol=lambda _family, _j: (0, 0), SLOTS=["congestion", "irdrop", "timing", "wirelength", "ground_cap"],
            TOLS={"v": (1e-7, 0)}, LBL_TOL={s: ("v", "v") for s in ("congestion", "timing", "wirelength", "ground_cap")})
        a, b = ("gate", "g1"), ("gate", "g2")
        truth = {"views": {"f": {"net": [(a, b, "n1"), (a, b, "n2")]}}}
        claims = {"edges": [("net", a, b, *rows["n2"]), ("net", a, b, *rows["n1"])]}
        result = folded_checks(truth, "f", claims, legacy)
        self.assertTrue(result["E_net_complete_semantic_multiset"]["passed"])
        # Break attachment between distinct attributes and labels: multiset fails.
        rows["n1"][0][0] = 1
        rows["n2"][0][0] = 2
        claims["edges"] = [("net", a, b, rows["n1"][0], rows["n2"][1], rows["n2"][2]),
                           ("net", a, b, rows["n2"][0], rows["n1"][1], rows["n1"][2])]
        self.assertFalse(folded_checks(truth, "f", claims, legacy)["E_net_complete_semantic_multiset"]["passed"])


class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("torch_geometric"), "requires conversion runtime")
    def test_missing_and_malformed_products_fail_without_crashing(self):
        from check_format import check_output
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(check_output(tmp)["ok"])
            (Path(tmp) / "b_graph.pt").write_text("invalid pickle")
            report = check_output(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any(row["check"] == "load/format exception" for row in report["failed"]))

    @unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("torch_geometric"), "requires conversion runtime")
    def test_all_views_both_forms_and_strict_failure_preserves_label_denominator(self):
        import torch
        from torch_geometric.data import Data, HeteroData
        from scoring import _legacy_modules
        adapter, legacy = _legacy_modules()
        truth = tiny_truth()
        # A top port and its net may have the same spelling; typed identities
        # remain distinct. The public checker must allow this in homogeneous N.
        truth["iopins"] = ["n2"]
        truth["order"] = {"gate": truth["gates"], "net": truth["nets"], "pin": truth["pins"], "iopin": truth["iopins"]}
        truth["pin_net"] = dict(zip(truth["pins"], truth["nets"]))
        truth["rc_driver"] = dict(zip(truth["nets"], truth["pins"]))
        for nt, names in truth["order"].items():
            truth[nt + "_x"] = {name: {col: 0.0 for col in adapter.DOC_COLS[nt]} for name in names}
        g1, g2 = ("gate", "g1"), ("gate", "g2")
        n1, n2 = ("net", "n1"), ("net", "n2")
        p1, p2 = ("pin", ("g1", "A")), ("pin", ("g2", "Z"))
        io = ("iopin", "n2")
        truth["views"] = {
            "b": {"connects": [(g1, p1, None), (p1, n1, None), (g2, p2, None), (p2, n2, None), (io, n2, None)]},
            "c": {"pin": [(g1, n1, p1[1]), (g2, n2, p2[1])], "iopin_connection": [(io, n2, None)]},
            "d": {"gate_pin": [(g1, p1, None), (g2, p2, None)], "net": [(p2, io, "n2")]},
            "e": {"gate": [], "net": [(p2, io, "n2")]},
            "f": {"net": [(g2, io, "n2")]},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for view, nts in legacy.VIEW_TYPES.items():
                keys = [(nt, name) for nt in nts for name in truth["order"][nt]]
                index = {key: i for i, key in enumerate(keys)}
                x = torch.tensor([[adapter.NT_ID[nt], 0] + [truth[nt + "_x"][name][c] for c in adapter.DOC_COLS[nt]] for nt, name in keys], dtype=torch.float32)
                raw = torch.full((len(keys), 6), math.nan, dtype=torch.float32)
                raw[:, 0] = x[:, 0]
                y = raw.clone()
                for i, (nt, name) in enumerate(keys):
                    for j, slot in enumerate(adapter.SLOTS, 1):
                        if slot == "irdrop" or nt not in legacy.LABEL_AT[view][slot]:
                            continue
                        value = {"congestion": lambda: truth["congestion"][name][0],
                                 "timing": lambda: truth["path_delay"][name[0]],
                                 "wirelength": lambda: truth["wirelength_um"][name],
                                 "ground_cap": lambda: truth["ground_cap_fF"][name if nt == "net" else truth["pin_net"][name]]}[slot]()
                        raw[i, j] = value
                        y[i, j] = truth["congestion"][name][1] if slot == "congestion" else math.log1p(value)
                edges, families, attrs, ys, raws = [], [], [], [], []
                ids = {family: code for code, family in adapter.FAMILY[view].items() if code is not None}
                for family, rows in truth["views"][view].items():
                    for a, b, entity in rows:
                        at, yy, rr = legacy.truth_edge_rows(truth, view, family, entity)
                        for aa, bb in ((a, b), (b, a)):
                            edges.append([index[aa], index[bb]])
                            families.append(ids[family]); attrs.append(at)
                            ys.append([ids[family]] + yy); raws.append([ids[family]] + rr)
                rc = []
                if view in "bc":
                    rc.append((n1, n2, 0, 0.5))
                elif view in "de":
                    rc.append((p1, p2, 0, 0.5))
                if view in "bde":
                    rc.append((p1, p2, 1, 100.0))
                re, rt, ry, rr = [], [], [], []
                for a, b, code, value in rc:
                    for aa, bb in ((a, b), (b, a)):
                        re.append([index[aa], index[bb]]); rt.append(code)
                        ry.append([code, math.log1p(value) if code == 0 else math.nan, math.log1p(value) if code == 1 else math.nan])
                        rr.append([code, value if code == 0 else math.nan, value if code == 1 else math.nan])
                graph = Data(x=x, y=y, y_raw=raw, node_name=[name if nt != "pin" else "/".join(name) for nt, name in keys],
                             edge_index=torch.tensor(edges, dtype=torch.long).reshape(-1, 2).T.contiguous(),
                             edge_type=torch.tensor(families, dtype=torch.long), edge_attr=torch.tensor(attrs, dtype=torch.float32).reshape(-1, 8),
                             edge_y=torch.tensor(ys, dtype=torch.float32).reshape(-1, 6), edge_y_raw=torch.tensor(raws, dtype=torch.float32).reshape(-1, 6),
                             rc_edge_index=torch.tensor(re, dtype=torch.long).reshape(-1, 2).T.contiguous(), rc_edge_type=torch.tensor(rt, dtype=torch.long),
                             rc_edge_y=torch.tensor(ry, dtype=torch.float32).reshape(-1, 3), rc_edge_y_raw=torch.tensor(rr, dtype=torch.float32).reshape(-1, 3))
                hetero = HeteroData(graph_kind="hetero")
                local = {}
                for nt in nts:
                    selected = [i for i, key in enumerate(keys) if key[0] == nt]
                    local.update({i: k for k, i in enumerate(selected)})
                    hetero[nt].x = x[selected, 1:]
                    hetero[nt].y = y[selected, 1:]
                    hetero[nt].y_raw = raw[selected, 1:]
                    hetero[nt].node_name = [graph.node_name[i] for i in selected]
                for isrc, ei, codes in ((False, graph.edge_index, graph.edge_type), (True, graph.rc_edge_index, graph.rc_edge_type)):
                    stores = {}
                    for k, (a, b) in enumerate(ei.T.tolist()):
                        family = adapter.RC_FAMILY[int(codes[k])] if isrc else adapter.FAMILY[view][int(codes[k])]
                        stores.setdefault((keys[a][0], family, keys[b][0]), []).append(k)
                    for rel, selected in stores.items():
                        store = hetero[rel]
                        store.edge_index = torch.tensor([[local[int(ei[0, k])], local[int(ei[1, k])]] for k in selected], dtype=torch.long).T.contiguous()
                        if isrc:
                            store.rc_edge_y = graph.rc_edge_y[selected, 1:]
                            store.rc_edge_y_raw = graph.rc_edge_y_raw[selected, 1:]
                        else:
                            store.edge_attr = graph.edge_attr[selected]
                            store.edge_y = graph.edge_y[selected, 1:]
                            store.edge_y_raw = graph.edge_y_raw[selected, 1:]
                torch.save(graph, root / f"{view}_graph_homo.pt")
                torch.save(hetero, root / f"{view}_graph.pt")
            result = score_design(truth, root)
            self.assertTrue(result["strict_full_pass"], str(result["failures"]) + str({k: v.get("failed_groups", v.get("error")) for k, v in result["views"].items()}))
            self.assertEqual(result["label_accuracy"], 1)
            # Numeric corruption may yield infinite diagnostics inside the old
            # checker; the new failed result must still be valid JSON.
            corrupt_path = root / "b_graph_homo.pt"
            corrupt = torch.load(corrupt_path, weights_only=False)
            original_x = corrupt.x.clone()
            corrupt.x[0, 5] = math.inf
            torch.save(corrupt, corrupt_path)
            bad = score_design(truth, root)
            self.assertFalse(bad["strict_full_pass"])
            json.dumps(bad, allow_nan=False)
            corrupt.x = original_x
            torch.save(corrupt, corrupt_path)
            (root / "f_graph_homo.pt").unlink()
            result = score_design(truth, root)
            self.assertFalse(result["strict_full_pass"])
            self.assertEqual(result["label_accuracy"], 1)
            self.assertEqual(result["labels"]["wirelength"]["expected"], 2)
            (root / "b_graph.pt").unlink()
            result = score_design(truth, root)
            self.assertEqual(result["label_accuracy"], 0)


if __name__ == "__main__":
    unittest.main()
