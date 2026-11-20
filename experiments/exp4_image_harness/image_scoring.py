#!/usr/bin/env python3
"""Private, per-pixel image scorer with a reference-sized denominator."""
from __future__ import annotations

from pathlib import Path
import zipfile

import numpy as np

FEATURES = ("cell_density", "macro_region", "rudy", "pin_rudy", "pin_density")
LABELS = ("congestion", "congestion_raw")
PRIMARY_CHANNELS = FEATURES + ("congestion_raw",)
TOLERANCES = {**{name: (1e-5, 1e-5) for name in FEATURES},
              "pin_density": (0.0, 0.0),
              "congestion": (1e-6, 1e-5), "congestion_raw": (1e-6, 1e-5)}


def score_design(reference, output_dir, run):
    output_dir = Path(output_dir)
    failures, arrays = [], {}
    run_ok = run.get("exit_code") == 0 and run.get("stop_reason") not in (
        "timeout", "memory_limit", "no_converter", "container_error")
    if not run_ok:
        failures.append("production_failed")
    for filename, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
        try:
            with np.load(output_dir / filename, allow_pickle=False) as archive:
                if sorted(archive.files) != sorted(names):
                    failures.append(f"{filename}:array_names")
                for name in names:
                    if name in archive.files:
                        arrays[name] = archive[name]
        except (OSError, ValueError, TypeError, EOFError, zipfile.BadZipFile) as exc:
            failures.append(f"{filename}:unreadable:{type(exc).__name__}")

    channels = {}
    for name in FEATURES + LABELS:
        truth = np.asarray(reference["arrays"][name], dtype=np.float64)
        claim = arrays.get(name)
        atol, rtol = TOLERANCES[name]
        report = {"expected": int(truth.size), "matched": 0, "rate": 0.0,
                  "passed": False, "atol": atol, "rtol": rtol}
        valid = claim is not None and claim.shape == truth.shape and claim.dtype == np.float32
        if not valid:
            report["error"] = "missing_or_wrong_shape_dtype"
        else:
            finite = np.isfinite(claim)
            domain = finite & (claim >= 0)
            if name == "pin_density":
                domain &= claim == np.rint(claim)
            errors = np.abs(claim.astype(np.float64) - truth)
            matched = domain & (errors <= atol + rtol * np.abs(truth))
            report["matched"] = int(np.count_nonzero(matched)) if run_ok else 0
            report["rate"] = report["matched"] / truth.size
            report["passed"] = bool(run_ok and np.all(matched))
            report["max_abs_error"] = float(np.max(errors[finite])) if np.any(finite) else None
        if not report["passed"]:
            failures.append(name)
        channels[name] = report
    applicable = [name for name in PRIMARY_CHANNELS if np.any(reference["arrays"][name] != 0)]
    mean = lambda names: sum(channels[name]["rate"] for name in names) / len(names) if names else 0.0
    return {"schema": 1, "design": reference["design"], "platform": reference["platform"],
            "grid": reference["grid"], "strict_full_pass": not failures,
            "raster_accuracy": mean(applicable), "feature_accuracy": mean(FEATURES),
            "label_accuracy": mean(LABELS), "channels": channels,
            "labels": {name: channels[name] for name in LABELS},
            "primary_channels": list(PRIMARY_CHANNELS), "applicable_primary_channels": applicable,
            "failures": failures,
            "accuracy_definition": "mean of nonempty-reference channel rates among five features and raw congestion; each rate uses every reference pixel"}
