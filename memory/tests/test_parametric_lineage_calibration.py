from tehm.parametric.calibration import calibrate_lineage_grouped


def _sample(lineage, wns=-0.02, area=-0.10):
    metrics = {"wns_ns": 0.0, "tns_ns": 0.0, "area_um2": 0.0,
               "power_w": 0.0, "congestion": 0.0, "drc_violations": 0.0}
    observed = dict(metrics)
    observed.update({"wns_ns": wns, "area_um2": area})
    return {"lineage_id": lineage, "predicted": metrics,
            "observed_deltas": observed}


def test_lineage_grouped_conformal_and_safety_are_shadow_only():
    report = calibrate_lineage_grouped(
        [_sample(f"heldout:{idx}") for idx in range(3)],
        training_lineages=["train:a"], min_samples_per_metric=3)
    assert report["status"] == "ready_for_shadow"
    assert report["shadow_only"] is True
    assert report["promotion_eligible"] is False
    assert report["conformal"]["method"].startswith("split_conformal")
    assert report["safety"]["harmful_rate"] == 0.0


def test_lineage_firewall_fails_closed():
    report = calibrate_lineage_grouped(
        [_sample("heldout:a"), _sample("heldout:b"), _sample("heldout:c")],
        training_lineages=["heldout:a"])
    assert report["status"] == "firewall_failed"
    assert report["canonical_memory_mutation"] == "none"
