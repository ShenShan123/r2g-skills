"""Regression coverage for fixed-target timing in the engineer-loop clean gate."""

import json

import engineer_loop as el


def _entry(tmp_path):
    project = tmp_path / "project"
    (project / "reports").mkdir(parents=True)
    return {
        "design": "timing_subject",
        "project_path": str(project),
        "platform": "sky130hd",
        "kind": "normal",
    }


def _global_status(timing="severe"):
    return {
        "drc": "clean",
        "lvs": "clean",
        "route": "clean",
        "rcx": "clean",
        "timing": timing,
    }


def test_signoff_status_includes_severe_timing_and_physical_checks(tmp_path):
    entry = _entry(tmp_path)
    reports = tmp_path / "project/reports"
    (reports / "drc.json").write_text('{"status":"clean"}')
    (reports / "lvs.json").write_text('{"status":"clean"}')
    (reports / "route.json").write_text('{"status":"clean"}')
    (reports / "rcx.json").write_text('{"status":"complete"}')
    (reports / "timing_check.json").write_text(
        '{"tier":"severe","wns_ns":-5.07195}'
    )

    status = el._signoff_status(entry)

    assert status == _global_status("severe")
    assert el._physical_signoff_clean(status) is True
    assert el._all_signoff_clean(status) is False


def test_contradictory_clean_tier_with_negative_wns_fails_closed(tmp_path):
    entry = _entry(tmp_path)
    reports = tmp_path / "project/reports"
    for name in ("drc", "lvs", "route"):
        (reports / f"{name}.json").write_text('{"status":"clean"}')
    (reports / "rcx.json").write_text('{"status":"complete"}')
    (reports / "timing_check.json").write_text('{"tier":"clean","wns_ns":-0.01}')

    assert el._signoff_status(entry)["timing"] == "fail"


def test_process_one_routes_physical_clean_timing_failure_to_timing_fix(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    led = el.Ledger(tmp_path / "ledger.jsonl")
    led.add(entry)
    statuses = iter([
        {"drc": "unknown", "lvs": "unknown", "route": "unknown", "rcx": "unknown", "timing": "unknown"},
        _global_status("severe"),
        _global_status("clean"),
    ])
    checks = []
    monkeypatch.setattr(el, "_run_flow", lambda unused: 0)
    monkeypatch.setattr(el, "_signoff_status", lambda unused: next(statuses))
    monkeypatch.setattr(el, "_run_fix", lambda item: checks.append(item.get("check", "both")) or 0)
    monkeypatch.setattr(el, "_ingest", lambda unused: None)

    result = el.process_one(led, led.pending()[0], conn=None)

    assert result == "clean"
    assert led.state(entry["design"]) == "clean"
    assert checks == ["both", "timing"]


def test_process_one_never_marks_timing_residual_clean(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    led = el.Ledger(tmp_path / "ledger.jsonl")
    led.add(entry)
    statuses = iter([
        {"drc": "unknown", "lvs": "unknown", "route": "unknown", "rcx": "unknown", "timing": "unknown"},
        _global_status("severe"),
        _global_status("severe"),
    ])
    checks = []
    monkeypatch.setattr(el, "_run_flow", lambda unused: 0)
    monkeypatch.setattr(el, "_signoff_status", lambda unused: next(statuses))
    monkeypatch.setattr(
        el,
        "_run_fix",
        lambda item: checks.append(item.get("check", "both")) or (1 if item.get("check") == "timing" else 0),
    )
    monkeypatch.setattr(el, "_ingest", lambda unused: None)

    result = el.process_one(led, led.pending()[0], conn=None)

    assert result == "escalated"
    assert led.state(entry["design"]) == "escalated"
    assert checks == ["both", "timing"]
