"""Cross-stage Physical Effect Memory (design doc 26 Phase 11, test list 27.1).

Records the physical deltas (ΔWNS/ΔTNS/ΔArea/ΔPower/ΔCongestion/ΔDRC) of
executed actions and aggregates them per action. The predict is the EMPIRICAL
mean + support — no differentiable gradient claimed; missing metrics are never
fabricated.
"""
from __future__ import annotations

import pytest

from tehm.physical.effects import extract_deltas
from tehm.physical.calibration import calibrate_retrieval
from tehm.physical.memory import PhysicalEffectMemory


def _ppa(wns=-1.0, tns=-3.0, area=28000.0, power=0.013, drc=4):
    return {"summary": {
        "timing": {"setup_wns": wns, "setup_tns": tns},
        "area": {"design_area_um2": area},
        "power": {"total_power_w": power},
        "drc": {"drc_violations": drc},
    }}


def test_extract_deltas_computes_all_metrics():
    before = _ppa(wns=-1.2, tns=-4.0, area=28000.0, power=0.013, drc=4)
    after = _ppa(wns=-0.7, tns=-2.0, area=28200.0, power=0.014, drc=0)
    deltas = extract_deltas(before, after)
    assert deltas["wns_ns"] == pytest.approx(0.5)      # improved (less negative)
    assert deltas["tns_ns"] == pytest.approx(2.0)
    assert deltas["area_um2"] == pytest.approx(200.0)
    assert deltas["power_w"] == pytest.approx(0.001)
    assert deltas["drc_violations"] == pytest.approx(-4.0)


def test_missing_metric_never_fabricated():
    deltas = extract_deltas({"summary": {"timing": {"setup_wns": -1.0}}}, {})
    assert deltas["wns_ns"] is None
    assert deltas["area_um2"] is None


def test_record_and_count(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    effect = mem.record(
        transition_id="t1", action_domain="signoff.REPAIR_ACTION",
        transformation_family="DENSITY_RELIEF",
        before_ppa=_ppa(), after_ppa=_ppa(wns=-0.5, drc=0))
    assert mem.count() == 1
    assert effect.deltas["wns_ns"] == pytest.approx(0.5)
    conn.close()


def test_idempotent_record_does_not_turn_empty_context_into_support(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    kwargs = dict(
        transition_id="t1", action_domain="signoff.REPAIR_ACTION",
        transformation_family="DENSITY_RELIEF",
        before_ppa=_ppa(), after_ppa=_ppa(wns=-0.5, drc=0))
    mem.record(**kwargs)
    mem.record(**kwargs)
    row = conn.execute(
        "SELECT graph_context_json,graph_context_digest "
        "FROM tehm_physical_effects WHERE transition_id='t1'").fetchone()
    assert row["graph_context_json"] == "{}"
    assert row["graph_context_digest"] == ""
    assert mem.profile(family="DENSITY_RELIEF")["graph_context_support"] == 0
    conn.close()


def test_record_rejects_conflicting_evidence_without_replacing_row(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    mem.record(
        transition_id="immutable", action_domain="flow",
        transformation_family="DENSITY_RELIEF", before_ppa=_ppa(),
        after_ppa=_ppa(wns=-0.5))
    with pytest.raises(ValueError, match="immutable and conflicts"):
        mem.record(
            transition_id="immutable", action_domain="flow",
            transformation_family="DENSITY_RELIEF", before_ppa=_ppa(),
            after_ppa=_ppa(wns=-0.1))
    row = conn.execute(
        "SELECT after_ppa_json FROM tehm_physical_effects "
        "WHERE transition_id='immutable'").fetchone()
    assert '"setup_wns":-0.5' in row[0]
    conn.close()


def test_profile_aggregates_mean_and_support(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for i, wns in enumerate((-0.4, -0.2, -0.6)):
        mem.record(transition_id=f"t{i}",
                   action_domain="signoff.REPAIR_ACTION",
                   transformation_family="DENSITY_RELIEF",
                   before_ppa=_ppa(wns=-1.0), after_ppa=_ppa(wns=wns))
    profile = mem.profile(family="DENSITY_RELIEF")
    assert profile["support"] == 3
    assert profile["mean_deltas"]["wns_ns"] == pytest.approx(0.6)  # (0.6+0.8+0.4)/3
    assert profile["min_deltas"]["wns_ns"] == pytest.approx(0.4)
    assert profile["max_deltas"]["wns_ns"] == pytest.approx(0.8)
    assert profile["note"]  # honest: no gradient claim


def test_profile_harmful_signals(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    mem.record(transition_id="t1", action_domain="x",
               transformation_family="BROKEN_ACTION",
               before_ppa=_ppa(wns=-1.0, area=28000.0, drc=0),
               after_ppa=_ppa(wns=-1.5, area=28500.0, drc=8))
    profile = mem.profile(family="BROKEN_ACTION")
    assert "wns_ns" in profile["harmful_metrics"]   # WNS got worse
    assert "area_um2" in profile["harmful_metrics"]
    assert "drc_violations" in profile["harmful_metrics"]


def test_predict_unknown_action_honest(tmp_path):
    conn = _open(tmp_path)
    profile = PhysicalEffectMemory(conn).predict(family="NO_SUCH_ACTION")
    assert profile["support"] == 0
    assert profile["mean_deltas"] == {}
    assert profile["harmful_metrics"] == []


def test_family_scoped_profile(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    mem.record(transition_id="a", action_domain="x",
               transformation_family="FAM_A", before_ppa=_ppa(), after_ppa=_ppa(wns=0.1))
    mem.record(transition_id="b", action_domain="x",
               transformation_family="FAM_B", before_ppa=_ppa(), after_ppa=_ppa(wns=-0.9))
    assert mem.profile(family="FAM_A")["support"] == 1
    assert mem.profile(family="FAM_A")["mean_deltas"]["wns_ns"] == pytest.approx(1.1)
    assert mem.profile(family="FAM_B")["mean_deltas"]["wns_ns"] == pytest.approx(0.1)


def test_ppa_backfill_updates_only_empirical_observation(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    mem.record(transition_id="t", action_domain="x",
               transformation_family="DENSITY_RELIEF",
               before_ppa={}, after_ppa={})
    deltas = mem.backfill_ppa(
        "t", before_ppa=_ppa(area=100.0), after_ppa=_ppa(area=125.0),
        evidence_refs=[{"oracle": "ppa", "sha256": "abc"}])
    assert deltas["area_um2"] == 25.0
    assert mem.profile(family="DENSITY_RELIEF")["mean_deltas"]["area_um2"] == 25.0
    row = conn.execute(
        "SELECT evidence_refs_json FROM tehm_physical_effects WHERE transition_id='t'"
    ).fetchone()
    assert "abc" in row[0]
    conn.close()


def _graph_context(cells, *, platform="sky130hd", tier="research", nets=None):
    return {
        "extractor_version": "test-graph-v1", "design": f"d{cells}",
        "platform": platform, "status": "complete", "dataset_tier": tier,
        "graph_features": {"num_cells": cells, "num_nets": nets or cells * 2,
                           "avg_fanout": 2.0, "core_area": cells * 10},
        "topology_rows": {"nodes_gate": cells, "nodes_net": nets or cells * 2,
                          "edges_pin_net": cells * 3},
        "feature_health": {}, "signoff_health": {"status": "pass"},
        "def_sha256": f"def-{cells}", "feature_digests": {},
    }


def _record_graph(mem, tid, cells, delta, **context_kwargs):
    mem.record(
        transition_id=tid, action_domain="flow",
        transformation_family="DENSITY_RELIEF",
        before_ppa=_ppa(wns=-1.0), after_ppa=_ppa(wns=-1.0 + delta),
        graph_context=_graph_context(cells, **context_kwargs))


def test_similar_graph_predicts_with_uncertainty(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells, delta in (("a", 90, 0.4), ("b", 100, 0.6),
                              ("c", 110, 0.8), ("d", 300, -0.2)):
        _record_graph(mem, tid, cells, delta)
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(105),
        k=3, min_unique_contexts=3)
    assert result["abstained"] is False
    assert result["neighbour_contexts"] == 3
    assert result["mean_deltas"]["wns_ns"] is not None
    interval = result["uncertainty_95"]["wns_ns"]
    assert interval["context_support"] == 3
    assert interval["lower_95"] < result["mean_deltas"]["wns_ns"] < interval["upper_95"]
    assert result["gradient_claimed"] is False


def test_similar_graph_duplicate_observations_do_not_fake_context_support(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    context = _graph_context(100)
    for i in range(5):
        mem.record(transition_id=f"repeat-{i}", action_domain="flow",
                   transformation_family="DENSITY_RELIEF",
                   before_ppa=_ppa(wns=-1), after_ppa=_ppa(wns=-0.5),
                   graph_context=context)
    result = mem.predict(family="DENSITY_RELIEF", graph_context=_graph_context(102))
    assert result["abstained"] is True
    assert result["abstain_reasons"] == ["insufficient_unique_contexts"]
    assert result["unique_contexts"] == 1


def test_similar_graph_abstains_across_platform_or_tier(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for i, cells in enumerate((90, 100, 110)):
        _record_graph(mem, f"p{i}", cells, 0.5, platform="gf180")
    platform = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100, platform="sky130hd"))
    assert platform["abstain_reasons"] == ["no_platform_compatible_contexts"]

    for i, cells in enumerate((90, 100, 110)):
        _record_graph(mem, f"t{i}", cells, 0.5, tier="strict_clean")
    tier = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100, tier="research"))
    assert tier["abstain_reasons"] == ["no_dataset_tier_compatible_contexts"]


def test_similar_graph_abstains_out_of_distribution(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for i, cells in enumerate((98, 100, 102)):
        _record_graph(mem, f"x{i}", cells, 0.5)
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(1000000),
        max_distance=2.0)
    assert result["abstained"] is True
    assert result["abstain_reasons"] == ["out_of_distribution"]
    assert result["nearest_distance"] > 2.0


def test_log_count_scale_floor_prevents_topology_spread_blowup(tmp_path):
    """Near-constant training counts must not turn a modest unseen graph OOD."""
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for i, cells in enumerate((90, 100, 110)):
        _record_graph(mem, f"stable-{i}", cells, 0.5)
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(10),
        max_distance=3.0)
    assert result["abstained"] is False
    assert result["nearest_distance"] < 3.0


def _insert_transition_action(conn, transition_id, action):
    from tehm.ids import stable_dumps
    conn.execute(
        """INSERT INTO tehm_transitions (
               transition_id, source_state_id, target_state_id,
               action_domain, action_json, observation_delta_json,
               verifier_json, primary_effect_key, outcome,
               created_regressions_json, newly_observed_json,
               provenance_json, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (transition_id, f"source-{transition_id}", f"target-{transition_id}",
         action["domain"], stable_dumps(action), stable_dumps({}),
         stable_dumps({}), None, "PASS", stable_dumps([]), stable_dumps([]),
         stable_dumps({}), "tehm-v1"))
    conn.commit()


def test_action_conditioned_prediction_filters_incompatible_knobs(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    compatible = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    incompatible = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"PLACE_DENSITY": "0.55"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    for index, cells in enumerate((90, 100, 110)):
        for prefix, action, delta in (
            ("compatible", compatible, 0.6),
            ("incompatible", incompatible, -0.7),
        ):
            tid = f"{prefix}-{index}"
            _record_graph(mem, tid, cells, delta)
            _insert_transition_action(conn, tid, action)
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(102),
        action=compatible, k=3, min_unique_contexts=3)
    assert result["abstained"] is False
    assert result["action_conditioned"] is True
    assert result["action_signature"]["config_edit_keys"] == ["CORE_UTILIZATION"]
    assert result["incompatible_action"] == 3
    assert result["mean_deltas"]["wns_ns"] == pytest.approx(0.6)
    conn.close()


def test_action_conditioned_prediction_abstains_without_compatible_metadata(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    # The physical row is real graph evidence, but has no transition action
    # metadata.  Supplying an action must not silently use it.
    for index, cells in enumerate((90, 100, 110)):
        _record_graph(mem, f"orphan-{index}", cells, 0.5)
    action = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(102),
        action=action, min_unique_contexts=3)
    assert result["abstained"] is True
    assert result["abstain_reasons"] == ["no_action_compatible_contexts"]
    assert result["unknown_action_metadata"] == 3
    conn.close()


def test_action_conditioned_prediction_does_not_mix_numeric_knob_values(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    action_22 = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    action_40 = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "40"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    for index, cells in enumerate((90, 100, 110)):
        for prefix, action, delta in (
            ("value22", action_22, 0.6), ("value40", action_40, -0.6)):
            tid = f"{prefix}-{index}"
            _record_graph(mem, tid, cells, delta)
            _insert_transition_action(conn, tid, action)
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(102),
        action=action_22, k=3, min_unique_contexts=3)
    assert result["abstained"] is False
    assert result["action_signature"]["config_edit_values"] == {
        "CORE_UTILIZATION": "22"
    }
    assert result["incompatible_action"] == 3
    assert result["mean_deltas"]["wns_ns"] == pytest.approx(0.6)
    conn.close()


def test_typed_action_signature_requires_complete_numeric_action_type():
    from tehm.physical.memory import typed_action_signature, _action_signature
    legacy = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    typed = {
        **legacy,
        "payload": {
            **legacy["payload"],
            "knob": "CORE_UTILIZATION",
            "direction": "increase",
            "relative_change": 0.1,
            "operation_point": {"platform": "sky130hs", "tier": "research"},
        },
    }
    assert typed_action_signature(legacy) is None
    signature = typed_action_signature(typed)
    assert signature["knob"] == "CORE_UTILIZATION"
    assert signature["relative_change"] == 0.1
    assert _action_signature(typed)["typed_action"] == signature
    assert _action_signature({**legacy, "payload": {
        **legacy["payload"], "direction": "increase"}}) is None


def test_calibration_policy_binds_exact_action_signature(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    action_22 = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    action_40 = {
        "domain": "flow.CONFIG_DELTA",
        "payload": {"config_edits": {"CORE_UTILIZATION": "40"}},
        "transformation_family": "DENSITY_RELIEF",
    }
    for index, cells in enumerate((90, 100, 110)):
        tid = f"cal-{index}"
        _record_graph(mem, tid, cells, 0.6)
        _insert_transition_action(conn, tid, action_22)
    samples = [
        {**_heldout(cells, 0.6, f"heldout:{index}"), "action": action_22}
        for index, cells in enumerate((95, 100, 105))
    ]
    policy = calibrate_retrieval(
        mem, family="DENSITY_RELIEF", heldout_samples=samples,
        training_lineages=["train:a"], target_coverage=0.8)
    assert policy["status"] == "ready"
    assert policy["firewall"]["action_signature_bound"] is True
    assert policy["action_signature"]["config_edit_values"] == {
        "CORE_UTILIZATION": "22"
    }
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        action=action_40, calibration_policy=policy)
    assert result["abstain_reasons"] == [
        "calibration_action_signature_mismatch"
    ]
    conn.close()


def test_calibration_rejects_mixed_action_signatures(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    samples = [
        {**_heldout(95, 0.6, "heldout:a"), "action": {
            "domain": "flow.CONFIG_DELTA",
            "payload": {"config_edits": {"CORE_UTILIZATION": "22"}},
            "transformation_family": "DENSITY_RELIEF",
        }},
        {**_heldout(100, 0.6, "heldout:b"), "action": {
            "domain": "flow.CONFIG_DELTA",
            "payload": {"config_edits": {"CORE_UTILIZATION": "40"}},
            "transformation_family": "DENSITY_RELIEF",
        }},
    ]
    policy = calibrate_retrieval(
        mem, family="DENSITY_RELIEF", heldout_samples=samples)
    assert policy["status"] == "firewall_failed"
    assert policy["reason"] == "mixed_action_signatures"
    assert policy["action_signature"] is None
    conn.close()


def test_calibration_rejects_weak_or_nonfinite_thresholds(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for value in (True, "0.8", float("nan"), float("inf")):
        with pytest.raises(ValueError, match="in \[0, 1\]"):
            calibrate_retrieval(
                mem, family="DENSITY_RELIEF", heldout_samples=[],
                target_coverage=value)
    conn.close()


def _heldout(cells, observed, lineage="heldout:independent"):
    return {
        "lineage_id": lineage,
        "family": "DENSITY_RELIEF",
        "graph_context": _graph_context(cells),
        "observed_deltas": {"wns_ns": observed},
    }


def test_heldout_calibration_is_read_only_and_gates_prediction(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells, delta in (("a", 90, 0.4), ("b", 100, 0.6),
                              ("c", 110, 0.8)):
        _record_graph(mem, tid, cells, delta)
    before = mem.count()
    policy = calibrate_retrieval(
        mem, family="DENSITY_RELIEF",
        heldout_samples=[_heldout(95, 0.6), _heldout(100, 0.6),
                         _heldout(105, 0.6)],
        training_lineages=["train:a", "train:b"], target_coverage=0.8)
    assert policy["status"] == "ready"
    assert policy["firewall"]["disjoint"] is True
    assert policy["thresholds"]["max_distance"] is not None
    assert policy["calibration"]["empirical_coverage"] == 1.0
    curve = policy["calibration"]["selective_risk_coverage"]
    assert curve
    assert curve[-1]["interval_coverage"] == 1.0
    assert curve[-1]["risk"] == 0.0
    assert mem.count() == before

    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=policy)
    assert result["abstained"] is False
    assert result["calibration"]["status"] == "ready"
    assert result["mean_deltas"]["wns_ns"] is not None
    assert result["mean_deltas"]["area_um2"] is None


def test_insufficient_or_overlapping_heldout_calibration_abstains(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells, delta in (("a", 90, 0.4), ("b", 100, 0.6),
                              ("c", 110, 0.8)):
        _record_graph(mem, tid, cells, delta)
    sparse = calibrate_retrieval(
        mem, family="DENSITY_RELIEF", heldout_samples=[_heldout(100, 0.6)])
    assert sparse["status"] == "insufficient_support"
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=sparse)
    assert result["abstain_reasons"] == ["heldout_calibration_not_ready"]

    overlap = calibrate_retrieval(
        mem, family="DENSITY_RELIEF",
        heldout_samples=[_heldout(95, 0.6, "shared"),
                         _heldout(100, 0.6, "shared"),
                         _heldout(105, 0.6, "shared")],
        training_lineages=["shared"])
    assert overlap["status"] == "firewall_failed"
    assert overlap["firewall"]["disjoint"] is False


def test_heldout_coverage_and_uncertainty_fail_closed(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells, delta in (("a", 90, 0.4), ("b", 100, 0.6),
                              ("c", 110, 0.8)):
        _record_graph(mem, tid, cells, delta)
    failed = calibrate_retrieval(
        mem, family="DENSITY_RELIEF",
        heldout_samples=[_heldout(95, 10.0), _heldout(100, 10.0),
                         _heldout(105, 10.0)], target_coverage=0.8)
    assert failed["status"] == "coverage_failed"
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=failed)
    assert result["abstain_reasons"] == ["heldout_calibration_not_ready"]

    ready = calibrate_retrieval(
        mem, family="DENSITY_RELIEF",
        heldout_samples=[_heldout(95, 0.6), _heldout(100, 0.6),
                         _heldout(105, 0.6)])
    ready["thresholds"]["max_uncertainty_widths"]["wns_ns"] = 0.0
    uncertain = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=ready)
    assert uncertain["abstain_reasons"] == [
        "prediction_uncertainty_above_threshold"]


def test_calibration_requires_per_metric_coverage(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells in (("a", 90), ("b", 100), ("c", 110)):
        mem.record(
            transition_id=tid, action_domain="flow",
            transformation_family="DENSITY_RELIEF",
            before_ppa=_ppa(wns=-1.0, tns=-3.0, area=28000.0,
                            power=0.013, drc=4),
            after_ppa=_ppa(wns=-0.4, tns=-2.4, area=28100.0,
                           power=0.014, drc=4),
            graph_context=_graph_context(cells))
    samples = []
    for index, observed in enumerate((0.6, 0.6, 0.6)):
        sample = _heldout(95 + index * 5, observed, f"per-metric:{index}")
        sample["observed_deltas"].update({
            "tns_ns": 0.6, "area_um2": 100.0 if index == 0 else 100.0,
            "power_w": 0.001, "drc_violations": 0.0,
        })
        if index == 0:
            sample["observed_deltas"]["area_um2"] = 1000.0
        samples.append(sample)
    policy = calibrate_retrieval(
        mem, family="DENSITY_RELIEF", heldout_samples=samples,
        target_coverage=0.8)
    assert policy["calibration"]["empirical_coverage"] >= 0.8
    assert policy["calibration"]["per_metric_coverage_failures"] == [
        "area_um2"]
    assert policy["status"] == "coverage_failed"
    assert policy["reason"] == "per_metric_coverage_below_target"
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=policy)
    assert result["abstain_reasons"] == ["heldout_calibration_not_ready"]
    conn.close()


def test_split_conformal_calibration_is_explicit_and_applied_read_only(tmp_path):
    conn = _open(tmp_path)
    mem = PhysicalEffectMemory(conn)
    for tid, cells, delta in (("a", 90, 0.4), ("b", 100, 0.6),
                              ("c", 110, 0.8)):
        _record_graph(mem, tid, cells, delta)
    before = mem.count()
    policy = calibrate_retrieval(
        mem, family="DENSITY_RELIEF",
        heldout_samples=[_heldout(95, 0.6), _heldout(100, 0.6),
                         _heldout(105, 0.6)],
        training_lineages=["train:a", "train:b"],
        interval_method="split_conformal_residual_v1")
    assert policy["interval_method"] == "split_conformal_residual_v1"
    assert policy["thresholds"]["conformal_quantiles"]["wns_ns"] >= 0.0
    result = mem.predict(
        family="DENSITY_RELIEF", graph_context=_graph_context(100),
        calibration_policy=policy)
    assert result["abstained"] is False
    assert result["uncertainty_95"]["wns_ns"]["interval_method"] == (
        "split_conformal_residual_v1")
    assert mem.count() == before
    conn.close()


def _open(tmp_path):
    from tehm import db as tehm_db
    conn = tehm_db.connect(tmp_path / "tehm.sqlite")
    tehm_db.ensure_schema(conn)
    return conn
