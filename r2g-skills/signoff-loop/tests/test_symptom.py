import importlib
symptom = importlib.import_module("symptom")  # knowledge/ is on sys.path in conftest


def test_symptom_id_is_stable_and_predicate_order_independent():
    sig_a = symptom.canonical_signature(
        "lvs", "symmetric_matcher",
        {"nets_balanced": True, "device_mismatch_present": False})
    sig_b = symptom.canonical_signature(
        "lvs", "symmetric_matcher",
        {"device_mismatch_present": False, "nets_balanced": True})
    assert symptom.symptom_id(sig_a) == symptom.symptom_id(sig_b)
    # false predicates are dropped (sparse, true-only)
    assert sig_a["predicates"] == {"nets_balanced": True}


def test_distinct_predicates_make_distinct_symptoms():
    base = symptom.canonical_signature("lvs", "generic", {})
    swapped = symptom.canonical_signature("lvs", "generic", {"same_cell_swap_present": True})
    assert symptom.symptom_id(base) != symptom.symptom_id(swapped)


def test_predicates_for_lvs_derives_balance_and_device():
    report = {"net_mismatches_schematic_only": 4, "net_mismatches_layout_only": 4,
              "device_mismatches": 0, "circuit_swaps": 2}
    p = symptom.predicates_for("lvs", report)
    assert p["nets_balanced"] is True
    assert "device_mismatch_present" not in p
    assert p["same_cell_swap_present"] is True


def test_from_fix_log_row_uses_check_class_predicates():
    row = {"check": "drc", "violation_class": "METAL1_ANTENNA",
           "predicates": {"beol_only": True}}
    sig, sid = symptom.from_fix_log_row(row)
    assert sig["check"] == "drc" and sig["class"] == "METAL1_ANTENNA"
    assert sig["predicates"] == {"beol_only": True}
    assert len(sid) == 16
