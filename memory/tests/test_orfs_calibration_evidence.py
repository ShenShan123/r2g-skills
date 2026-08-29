from __future__ import annotations

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_orfs_calibration_evidence import _conformal_for_sample  # noqa: E402


def test_row_conformal_coverage_is_derived_from_finite_metrics():
    sample = {
        "case_id": "sky130hs:crc:0",
        "predicted": {"wns_ns": 0.0, "area_um2": 2.0, "congestion": None},
        "observed_deltas": {"wns_ns": 0.0, "area_um2": 2.1, "congestion": None},
    }
    assert _conformal_for_sample(
        sample, {"area_um2": 0.2, "wns_ns": 0.0, "congestion": 1.0}) == {
            "covered": 2, "total": 2, "coverage": 1.0}


def test_row_conformal_coverage_fails_closed_without_finite_metric():
    sample = {
        "case_id": "sky130hs:empty:0",
        "predicted": {"wns_ns": None},
        "observed_deltas": {"wns_ns": None},
    }
    with pytest.raises(ValueError, match="no finite metric"):
        _conformal_for_sample(sample, {"wns_ns": 0.0})
