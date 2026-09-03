from scripts.aggregate_r3_calibration import _digest


def test_calibration_aggregate_digest_is_content_bound():
    first = _digest({"campaign": "rtl", "cases": 40})
    second = _digest({"campaign": "rtl", "cases": 40})
    changed = _digest({"campaign": "rtl", "cases": 41})
    assert first == second
    assert first != changed
