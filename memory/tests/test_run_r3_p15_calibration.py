from __future__ import annotations

import pytest

from scripts.run_r3_p15_calibration import (
    CAMPAIGN_ID,
    DEFAULT_CASE_COUNT,
    _campaign_identity,
    _case_identity,
    _normalise_campaign_tag,
)


def test_untagged_identity_preserves_historical_receipts():
    assert _campaign_identity(DEFAULT_CASE_COUNT, None) == CAMPAIGN_ID
    assert _campaign_identity(41, None) == f"{CAMPAIGN_ID}-n41"
    assert _case_identity(0, {"key": "send"}, "state_shift", None) == (
        "r3-p15-calibration-00-send-state_shift")


def test_tagged_identity_is_disjoint_from_untagged_namespace():
    tagged = _campaign_identity(DEFAULT_CASE_COUNT, "rerun-a")
    case = _case_identity(0, {"key": "send"}, "state_shift", "rerun-a")
    lineage = "lineage-r3-p15-rerun-a-00"
    assert tagged == f"{CAMPAIGN_ID}-rerun-a"
    assert case == "r3-p15-calibration-rerun-a-00-send-state_shift"
    assert case != "r3-p15-calibration-00-send-state_shift"
    assert lineage != "lineage-r3-p15-00"


@pytest.mark.parametrize("tag", ["", "/tmp", "a" * 49, "with space", "-bad"])
def test_campaign_tag_rejects_path_or_ambiguous_names(tag):
    with pytest.raises(ValueError, match="campaign_tag"):
        _normalise_campaign_tag(tag)


def test_campaign_tag_accepts_reproducible_slug():
    assert _normalise_campaign_tag("rtl-rerun_20260904") == "rtl-rerun_20260904"
