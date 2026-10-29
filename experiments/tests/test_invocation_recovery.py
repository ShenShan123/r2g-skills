"""Runner-level recovery: a transport-voided attempt gets a fresh complete attempt."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_agent_repair_cost as m  # noqa: E402


def _closed(folder, chat_errors=(), incomplete=False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({"status": "complete", "chat_errors": list(chat_errors)}))
    calls = [{"call_id": "a", "usage_reported": True, "stream_complete": True},
             {"call_id": "b", "usage_reported": not incomplete, "stream_complete": not incomplete}]
    (folder / "summary.json").write_text(json.dumps({"status": "complete", "usage": {"calls": calls}}))


def test_invocation_failure_detects_transport_evidence_only(tmp_path):
    ok, bad, partial = tmp_path / "ok", tmp_path / "bad", tmp_path / "partial"
    _closed(ok)
    _closed(bad, chat_errors=['HTTP 502: {"error":"upstream unavailable"}'])
    _closed(partial, incomplete=True)
    assert m.invocation_failure(ok) is None
    assert m.invocation_failure(bad)["errors"]
    assert m.invocation_failure(partial)["incomplete_calls"] == ["b"]
    ambiguous = tmp_path / "ambiguous"
    ambiguous.mkdir()
    (ambiguous / "state.json").write_text(json.dumps({"status": "incomplete", "error": "No session receipt"}))
    assert m.invocation_failure(ambiguous) is None


def test_recovery_runs_fresh_attempts_in_separate_cohorts(tmp_path, monkeypatch):
    (tmp_path / "launch_parameters.json").write_text(json.dumps({"cores_per_design": 8, "fixed_cores": True}))
    seen = []

    def fake_run_case(case, args):
        seen.append(args.output)
        folder = args.output / "cases" / case["id"]
        # first attempt voided by the relay; the fresh one completes cleanly
        _closed(folder, chat_errors=["HTTP 502"] if len(seen) == 1 else [])
        return {"status": "complete", "output": str(args.output)}

    monkeypatch.setattr(m, "run_case", fake_run_case)
    args = argparse.Namespace(output=tmp_path, invocation_retries=2, dry_run=False)
    result = m.run_case_with_recovery({"id": "nangate45_C1"}, args)
    assert seen == [tmp_path, tmp_path / "retries" / "r1"]
    assert result["output"] == str(tmp_path / "retries" / "r1")
    launch = json.loads((tmp_path / "retries/r1/launch_parameters.json").read_text())
    assert launch["fixed_cores"] is True and launch["retry_index"] == 1
    assert (tmp_path / "retries/r1/cases/nangate45_C1/retry_reason.json").is_file()
    assert (tmp_path / "cases/nangate45_C1/state.json").is_file()      # voided attempt kept


def test_recovery_is_off_by_default(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "run_case", lambda case, args: calls.append(1) or _closed(
        args.output / "cases" / case["id"], chat_errors=["HTTP 502"]))
    m.run_case_with_recovery({"id": "c"}, argparse.Namespace(output=tmp_path, invocation_retries=0, dry_run=False))
    assert calls == [1]


def test_exporter_and_ledger_see_retry_cohorts(tmp_path):
    import export_repair_cost_evidence as e
    for rel in ("cases/a", "retries/r1/cases/a", "retries/r2/cases/b"):
        (tmp_path / rel).mkdir(parents=True)
    assert [p.relative_to(tmp_path).as_posix() for p in e.case_folders(tmp_path)] == [
        "cases/a", "retries/r1/cases/a", "retries/r2/cases/b"]
