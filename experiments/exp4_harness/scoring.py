#!/usr/bin/env python3
"""Hidden evaluator for Exp4's B--F dataset-production harness comparison.

    python3 scoring.py TRUTH.pkl OUTPUT_DIR REPORT.json [--run-report RUN.json]

TRUTH.pkl is the independent exp4_validation/truth_defgraph.py output. Never mount
this evaluator, its imports, or truth inside an agent's workspace. OUTPUT_DIR may
contain dataset/ or be that directory itself. Both documented graph forms are
required for strict acceptance. Primary label accuracy counts the six physical
label families exactly once, on B hetero raw labels, with a truth-key denominator.
It does not count off-type NaNs, powers, raw/log twins or graph-view/form copies.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import pickle
import sys

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parent / "exp4_validation"
LABEL_TOLS = {
    "congestion": (1e-6, 1e-5), "timing": (1e-4, 1e-6),
    "wirelength": (2e-4, 1e-6), "ground_cap": (1e-5, 1e-5),
    "coupling_cap": (1e-5, 1e-5), "resistance": (1e-4, 1e-5),
}


def within(a, b, tol, *, nan_equal=False):
    """Finite numerical tolerance; NaNs earn credit only for strict mask checks."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError, OverflowError):
        return False
    if math.isnan(a) or math.isnan(b):
        return nan_equal and math.isnan(a) and math.isnan(b)
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tol[0] + tol[1] * abs(b)


def maximum_matches(claims, expected, compatible):
    """Maximum cardinality matching, independent of parallel-row serialization order.

    The endpoint bucket sizes are normally one or two. An iterative augmenting
    path avoids recursion limits on designs with many parallel folded entities.
    Each submitted row can satisfy at most one expected row.
    """
    if len(claims) == len(expected) == 1:
        return int(compatible(claims[0], expected[0]))
    adjacent = [[j for j, row in enumerate(claims) if compatible(row, truth)] for truth in expected]
    owner = {}
    for start in range(len(expected)):
        queue, seen_truth, seen_claim, parent = [start], {start}, set(), {}
        end = None
        for i in queue:
            for j in adjacent[i]:
                if j in seen_claim:
                    continue
                seen_claim.add(j)
                parent[j] = i
                if j not in owner:
                    end = j
                    break
                nxt = owner[j]
                if nxt not in seen_truth:
                    seen_truth.add(nxt)
                    queue.append(nxt)
            if end is not None:
                break
        if end is not None:
            while end is not None:
                i = parent[end]
                previous = next((j for j, t in owner.items() if t == i), None)
                owner[end] = i
                end = previous
    return len(owner)


def _pin_node(pin):
    pin = tuple(pin)
    return ("iopin", pin[1]) if pin[0] == "PIN" else ("pin", pin)


def expected_labels(truth, *, finite_only=True):
    """Fixed expected semantic keys, independent of submitted coverage/format."""
    result = {
        "congestion": {("gate", g): truth["congestion"][g][0] for g in truth["gates"]},
        "timing": {("pin", tuple(p)): truth["path_delay"][p[0]] for p in truth["pins"]},
        "wirelength": {("net", n): truth["wirelength_um"][n] for n in truth["nets"]},
        "ground_cap": {("net", n): truth["ground_cap_fF"].get(n, math.nan) for n in truth["nets"]},
        "coupling_cap": {}, "resistance": {},
    }
    nets = set(truth["nets"])
    for pair, value in truth.get("rc_coupling_nets", {}).items():
        if all(n in nets for n in pair):
            result["coupling_cap"][frozenset(("net", n) for n in pair)] = value
    available = {("pin", tuple(p)) for p in truth["pins"]} | {("iopin", p) for p in truth["iopins"]}
    for pair, value in truth.get("rc_res_pairs", {}).items():
        ends = frozenset(_pin_node(p) for p in pair)
        if len(ends) == 2 and ends <= available:
            result["resistance"][ends] = value
    return {label: {key: float(value) for key, value in rows.items() if not finite_only or math.isfinite(float(value))}
            for label, rows in result.items()}


def score_labels(truth, b_hetero):
    """Six family rates with missing/duplicate expected identities scored as errors.

    A malformed duplicate identity receives no value credit, even if one copy is
    correct. Extra identities are reported and fail strict acceptance; they cannot
    shrink or inflate the truth denominator. Wrong-direction/duplicated RC rows do
    not receive credit. Masked/unavailable truth is excluded, with n recorded.
    """
    expected = expected_labels(truth)
    known_keys = expected_labels(truth, finite_only=False)
    claimed = {label: defaultdict(list) for label in expected}
    if b_hetero is not None:
        for label, nt in (("congestion", "gate"), ("timing", "pin"), ("wirelength", "net"), ("ground_cap", "net")):
            names = b_hetero.get("nodes", {}).get(nt, [])
            values = b_hetero.get("y_raw", {}).get(nt, {}).get(label, [])
            for i, name in enumerate(names):
                name = tuple(name) if nt == "pin" else name
                claimed[label][(nt, name)].append(values[i] if i < len(values) else None)
        for family, a, b, _normalized, raw in b_hetero.get("rc", []):
            if repr(a) >= repr(b):
                continue  # one canonical direction; full symmetry is a strict check
            label, col = {"rc_coupling": ("coupling_cap", 0), "rc_resistance": ("resistance", 1)}.get(family, (None, None))
            if label is not None:
                claimed[label][frozenset((a, b))].append(raw[col] if len(raw) > col else None)
    reports = {}
    for label, rows in expected.items():
        cc = claimed[label]
        matched = sum(len(cc.get(key, [])) == 1 and within(cc[key][0], value, LABEL_TOLS[label])
                      for key, value in rows.items())
        reports[label] = {
            "expected": len(rows), "matched": matched,
            "rate": matched / len(rows) if rows else None,
            "missing": sum(key not in cc for key in rows),
            "duplicate_expected_keys": sum(len(cc.get(key, [])) > 1 for key in rows),
            "extra_keys": sum(key not in known_keys[label] for key in cc),
            "masked_truth_keys": len(known_keys[label]) - len(rows),
            "atol": LABEL_TOLS[label][0], "rtol": LABEL_TOLS[label][1],
        }
    rates = [r["rate"] for r in reports.values() if r["rate"] is not None]
    return {"label_accuracy": sum(rates) / len(rates) if rates else 0.0,
            "labels": reports, "n_applicable_labels": len(rates),
            "source": "b_graph.pt: HeteroData raw labels, each finite truth key once"}


def folded_checks(truth, view, claims, legacy):
    """Compare complete parallel-edge semantic rows as the documented multiset.

    The legacy checker rounds rows before sorting. Near-equal rows can tie, making
    label attachment depend on file order. Tolerance-aware bipartite matching
    removes that artifact without weakening any per-column tolerance or masks.
    """
    if view == "b":
        return {}
    submitted, expected = defaultdict(list), defaultdict(list)
    for family, a, b, attrs, y, raw in claims.get("edges", []):
        if repr(a) < repr(b):
            submitted[(family, frozenset((a, b)))].append((attrs, y, raw))
    for family, rows in truth["views"][view].items():
        for a, b, entity in rows:
            expected[(family, frozenset((a, b)))].append(legacy.truth_edge_rows(truth, view, family, entity))

    def compatible(family, a, b):
        if any(len(part) != width for part, width in zip(a, (8, 5, 5))):
            return False
        for j in range(8):
            tol = legacy.attr_tol(family, j)
            if tol is not None and not within(a[0][j], b[0][j], tol, nan_equal=True):
                return False
        for k in (1, 2):
            for j, slot in enumerate(legacy.SLOTS):
                if slot == "irdrop" and family == "gate":
                    continue
                tol = (0.0, 0.0) if slot == "irdrop" else legacy.TOLS[legacy.LBL_TOL[slot][k == 2]]
                if not within(a[k][j], b[k][j], tol, nan_equal=True):
                    return False
        return True

    stats = defaultdict(lambda: {"expected": 0, "claimed": 0, "matched": 0})
    for key in expected.keys() | submitted.keys():
        family = key[0]
        cr, tr = submitted[key], expected[key]
        s = stats[family]
        s["expected"] += len(tr)
        s["claimed"] += len(cr)
        s["matched"] += maximum_matches(cr, tr, lambda a, b: compatible(family, a, b))
    return {f"E_{family}_complete_semantic_multiset": {
                **s, "passed": s["expected"] == s["claimed"] == s["matched"]}
            for family, s in stats.items()}


def _legacy_modules():
    if str(VALIDATION) not in sys.path:
        sys.path.insert(0, str(VALIDATION))
    import adapter_defgraph
    import v2_defgraph_check
    return adapter_defgraph, v2_defgraph_check


def _json_safe(value):
    """Corrupt infinity-valued claims must remain reportable as failed outputs."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


_WORKER_TRUTH = None
_WORKER_DATASET = None


def _init_score_worker(truth, dataset):
    global _WORKER_TRUTH, _WORKER_DATASET
    _WORKER_TRUTH, _WORKER_DATASET = truth, dataset
    import torch
    torch.set_num_threads(1)


def _public_view(view):
    try:
        from .check_format import check_output
    except ImportError:
        from check_format import check_output
    return check_output(_WORKER_DATASET, views=view)


def _private_view(task):
    form, view, public_ok = task
    truth, dataset = _WORKER_TRUTH, _WORKER_DATASET
    adapter, legacy = _legacy_modules()
    suffix = "_graph.pt" if form == "hetero" else "_graph_homo.pt"
    key, path = f"{form}.{view}", dataset / f"{view}{suffix}"
    groups, canonical_b = {}, None
    try:
        claim = adapter.load_view_v2(path, view)
        if claim["form"] != form:
            raise ValueError(f"expected {form}, found {claim['form']}")
        if key == "hetero.b":
            canonical_b = claim
        legacy.check_view(truth, view, claim, groups, "", check_folded=not public_ok)
        groups = {name: result for name, result in groups.items() if not name.startswith("E_")}
        groups.update(folded_checks(truth, view, claim, legacy))
        failed = [name for name, result in groups.items() if not result.get("passed", False)]
        report = {"passed": not failed, "n_groups": len(groups), "failed_groups": failed, "groups": groups}
    except Exception as exc:
        report = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    # Preserve the original labels exception boundary and avoid giant-claim IPC.
    labels = score_labels(truth, canonical_b) if key == "hetero.b" else None
    return key, report, labels


def _score_design_parallel(truth, output_dir, run_report, workers):
    import multiprocessing
    import os
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    import torch
    torch.set_num_threads(1)
    output_dir = Path(output_dir)
    dataset = output_dir / "dataset" if (output_dir / "dataset").is_dir() else output_dir
    views = "bcdef"
    with multiprocessing.get_context("fork").Pool(
        processes=min(workers, len(views)), initializer=_init_score_worker, initargs=(truth, dataset), maxtasksperchild=1
    ) as pool:
        public_parts = pool.map(_public_view, views, chunksize=1)
        checks = [check for part in public_parts for check in part["checks"]]
        failed_checks = [check for check in checks if not check["ok"]]
        public = {"ok": not failed_checks, "n_checks": len(checks), "failed": failed_checks, "checks": checks}
        private_parts = pool.map(_private_view, [(form, view, public["ok"])
                                               for form in ("hetero", "homo") for view in views], chunksize=1)
    all_views = {key: report for key, report, _ in private_parts}
    view_reports = {f"{form}.{view}": all_views[f"{form}.{view}"]
                    for form in ("hetero", "homo") for view in views}
    failures = [key for key, report in view_reports.items() if not report["passed"]]
    if not public["ok"]:
        failures.append("format")
    if run_report is not None:
        if run_report.get("timed_out", False) or run_report.get("exit_code", 0) != 0:
            failures.append("production_run")
    labels = private_parts[0][2]
    return _json_safe({
        "schema_version": "exp4-harness-score-v1", "design": truth["design"],
        "platform": truth.get("platform"), "strict_full_pass": not failures,
        **labels, "failures": failures, "format": public, "views": view_reports,
        "run_report": run_report,
    })


def score_design(truth, output_dir, run_report=None, *, workers=1):
    """Evaluate one design. Output errors become failed results, never omissions."""
    if workers > 1:
        return _score_design_parallel(truth, output_dir, run_report, workers)
    try:
        from .check_format import check_output
    except ImportError:
        from check_format import check_output

    output_dir = Path(output_dir)
    dataset = output_dir / "dataset" if (output_dir / "dataset").is_dir() else output_dir
    public = check_output(dataset)
    adapter, legacy = _legacy_modules()
    canonical_b, view_reports, failures = None, {}, []
    for form, suffix in (("hetero", "_graph.pt"), ("homo", "_graph_homo.pt")):
        for view in "bcdef":
            key, path = f"{form}.{view}", dataset / f"{view}{suffix}"
            groups = {}
            try:
                claim = adapter.load_view_v2(path, view)
                if claim["form"] != form:
                    raise ValueError(f"expected {form}, found {claim['form']}")
                if key == "hetero.b":
                    canonical_b = claim
                legacy.check_view(truth, view, claim, groups, "", check_folded=not public["ok"])
                groups = {name: result for name, result in groups.items() if not name.startswith("E_")}
                groups.update(folded_checks(truth, view, claim, legacy))
                failed = [name for name, result in groups.items() if not result.get("passed", False)]
                view_reports[key] = {"passed": not failed, "n_groups": len(groups), "failed_groups": failed, "groups": groups}
            except Exception as exc:  # malformed output must not abort other views/designs
                view_reports[key] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
            if not view_reports[key]["passed"]:
                failures.append(key)
    if not public["ok"]:
        failures.append("format")
    if run_report is not None:
        if run_report.get("timed_out", False) or run_report.get("exit_code", 0) != 0:
            failures.append("production_run")
    labels = score_labels(truth, canonical_b)
    return _json_safe({
        "schema_version": "exp4-harness-score-v1", "design": truth["design"],
        "platform": truth.get("platform"), "strict_full_pass": not failures,
        **labels, "failures": failures, "format": public, "views": view_reports,
        "run_report": run_report,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("truth", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--run-report", type=Path)
    args = parser.parse_args()
    # Truth and output .pt files are trusted campaign artifacts, not arbitrary uploads.
    with args.truth.open("rb") as stream:
        truth = pickle.load(stream)
    run = json.loads(args.run_report.read_text()) if args.run_report else None
    result = score_design(truth, args.output_dir, run)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, allow_nan=False, default=str) + "\n")
    print(json.dumps({k: result[k] for k in ("design", "strict_full_pass", "label_accuracy", "failures")}))
    return 0  # a scored failed design is a successful evaluator invocation


if __name__ == "__main__":
    raise SystemExit(main())
