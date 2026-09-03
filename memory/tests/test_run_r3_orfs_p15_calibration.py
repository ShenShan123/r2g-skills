from scripts.run_r3_orfs_p15_calibration import _strata


def test_orfs_calibration_strata_are_backend_explicit():
    strata = _strata({
        "project_dir": "/tmp/sky130hs_uart_base_0",
        "platform": "sky130hs",
    })
    assert strata["mechanism_family"] == "ORFS_DENSITY_RELIEF"
    assert strata["design"] == "uart_base"
    assert strata["platform"] == "sky130hs"
    assert strata["flow_regime"] == "orfs_route_real"
    assert strata["model_identity"] == "typed-paired-oracle-v1"
    assert strata["state_shift_dimension"] == "none"
