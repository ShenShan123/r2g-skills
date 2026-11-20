"""Shared, dataset-format-independent match logic and FROZEN tolerances (PROTOCOL.md §2)."""
import math
from collections import Counter

import numpy as np

# Frozen before results (PROTOCOL.md). (atol, rtol): pass iff |a-b| <= atol + rtol*|b|.
TOL = {
    'exact': (0.0, 0.0),
    'coord_um_f32': (1e-4, 1e-6),
    'pin_in_shape_um': (1e-3, 0.0),
    'liberty_f32': (0.0, 1e-5),
    'sdc_f32': (0.0, 1e-6),
    'wl_um': (1e-6, 1e-9),
    'congestion': (1e-9, 1e-9),
    'overflow_um': (1e-6, 0.0),
    'pct': (1e-9, 0.0),
    'timing_ns': (1e-9, 0.0),
    'sta_wns_ns': (0.005, 0.0),
    'img_label': (1e-6, 0.0),
    'dr_total_rel': (0.0, 1e-3),
}


def set_compare(claim, truth, examples=5):
    """Multiset comparison of hashable items -> precision/recall/F1 + examples."""
    c, t = Counter(claim), Counter(truth)
    inter = sum((c & t).values())
    nc, nt = sum(c.values()), sum(t.values())
    p = inter / nc if nc else (1.0 if not nt else 0.0)
    r = inter / nt if nt else (1.0 if not nc else 0.0)
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    extra = list((c - t).elements())[:examples]
    missing = list((t - c).elements())[:examples]
    return dict(n_claim=nc, n_truth=nt, matched=inter, precision=p, recall=r, f1=f1,
                passed=(nc == nt == inter), extra_examples=[repr(x) for x in extra],
                missing_examples=[repr(x) for x in missing])


def value_compare(names, claim, truth, tol, nan_equal=True, examples=5):
    """Elementwise numeric comparison. NaN==NaN counts as a match iff nan_equal."""
    atol, rtol = TOL[tol] if isinstance(tol, str) else tol
    a = np.asarray(claim, dtype=np.float64)
    b = np.asarray(truth, dtype=np.float64)
    if a.shape != b.shape:
        return dict(n=int(b.size), matched=0, rate=0.0, passed=False, error=f'shape {a.shape} vs {b.shape}')
    na, nb = np.isnan(a), np.isnan(b)
    both = ~na & ~nb
    err = np.zeros_like(b)
    err[both] = np.abs(a[both] - b[both])
    ok = np.zeros(b.shape, bool)
    ok[both] = err[both] <= atol + rtol * np.abs(b[both])
    if nan_equal:
        ok |= na & nb
    n = int(b.size)
    m = int(ok.sum())
    rel = np.zeros_like(b)
    nz = both & (np.abs(b) > 0)
    rel[nz] = err[nz] / np.abs(b[nz])
    bad = np.flatnonzero(~ok)[:examples]
    return dict(n=n, matched=m, rate=(m / n if n else 1.0), passed=(m == n),
                max_abs=float(err[both].max()) if both.any() else 0.0,
                max_rel=float(rel[both].max()) if both.any() else 0.0,
                nan_mismatch=int((na ^ nb).sum()), tol=[atol, rtol],
                examples=[dict(name=repr(names[i]) if names is not None else int(i),
                               claim=None if math.isnan(a.flat[i]) else float(a.flat[i]),
                               truth=None if math.isnan(b.flat[i]) else float(b.flat[i])) for i in bad])


def bool_compare(names, claim, truth, examples=5):
    a = np.asarray(claim, bool); b = np.asarray(truth, bool)
    ok = a == b
    bad = np.flatnonzero(~ok)[:examples]
    return dict(n=int(b.size), matched=int(ok.sum()), rate=float(ok.mean()) if b.size else 1.0,
                passed=bool(ok.all()), examples=[dict(name=repr(names[i]), claim=bool(a[i]), truth=bool(b[i])) for i in bad])


def summarize(groups):
    """groups: {name: result-dict with 'passed'} -> overall verdict."""
    failed = sorted(k for k, v in groups.items() if v.get('applicable', True) and not v.get('passed'))
    return dict(passed=not failed, failed_groups=failed, n_groups=len(groups))
