"""2026-06-24: the campaign characterizes each design's best closing period (Fmax) and
flows at it. `engineer_loop fmax-drain` runs the proxy fmax_search per pending design and
rewrites its SDC to the winner period. These tests stub the fmax_search CLI (via the
R2G_LOOP_FMAX override) so no ORFS runs."""
import json
import os
from pathlib import Path

import engineer_loop


def _stub_fmax(tmp_path: Path, status="ok", period=2.5) -> Path:
    """A stand-in fmax_search.py CLI: writes reports/fmax_search.json for argv[1]."""
    body = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "proj=Path(sys.argv[1]); rep=proj/'reports'; rep.mkdir(parents=True,exist_ok=True)\n"
        f"d={{'design':proj.name,'status':{status!r}}}\n"
        f"if {status!r}=='ok': d['winner']={{'period':{period}}}\n"
        "(rep/'fmax_search.json').write_text(json.dumps(d))\n")
    stub = tmp_path / "fmax_stub.py"
    stub.write_text(body)
    return stub


def _mk_design(tmp_path: Path, name: str, clk: float = 1.0) -> Path:
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    (p / "constraints" / "constraint.sdc").write_text(
        f"create_clock -name clk -period {clk} [get_ports clk]\n"
        f"set clk_period {clk}\n", encoding="utf-8")
    return p


def test_fmax_drain_stamps_winner_period(tmp_path, monkeypatch):
    p = _mk_design(tmp_path, "d0")
    monkeypatch.setenv("R2G_LOOP_FMAX", str(_stub_fmax(tmp_path, "ok", 2.5)))
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "d0", "project_path": str(p), "platform": "nangate45"})
    n = engineer_loop.fmax_drain(tmp_path / "l.jsonl")
    assert n == 1
    sdc = (p / "constraints" / "constraint.sdc").read_text()
    assert "set clk_period 2.5" in sdc and "set clk_period 1.0" not in sdc
    assert json.loads((p / "reports" / "fmax_search.json").read_text())["status"] == "ok"


def test_period_stamped_is_g_format_aware():
    """fmax bug #1 (2026-06-26): rewrite_clk_period writes {period:g} (6 sig-figs), so the
    read-back equals the %g value, not the full-precision winner. _period_stamped compares
    against the formatted value -> a correct stamp counts; a wrong/absent one does not."""
    assert engineer_loop._period_stamped(0.6918, 0.69180034521) is True
    assert engineer_loop._period_stamped(2.5, 2.5) is True
    assert engineer_loop._period_stamped(2.5, 0.69180034521) is False
    assert engineer_loop._period_stamped(None, 1.0) is False


def test_fmax_drain_counts_high_precision_stamp(tmp_path, monkeypatch):
    """The end-to-end regression: a high-precision winner (0.69180034521 -> '0.6918') was
    falsely rejected by the old full-precision `abs(cur-period)<1e-9` verify, so the drain
    returned None and UNDER-COUNTED ~28% of correct stamps. It must now count + stamp it."""
    p = _mk_design(tmp_path, "hp", clk=10.0)
    monkeypatch.setenv("R2G_LOOP_FMAX", str(_stub_fmax(tmp_path, "ok", 0.69180034521)))
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "hp", "project_path": str(p), "platform": "nangate45"})
    n = engineer_loop.fmax_drain(tmp_path / "l.jsonl")
    assert n == 1                                            # counted, not a false no-op
    sdc = (p / "constraints" / "constraint.sdc").read_text()
    assert "set clk_period 0.6918" in sdc and "set clk_period 10" not in sdc


def test_fmax_drain_degenerate_design_does_not_abort(tmp_path, monkeypatch):
    """A no_clock_constraint (degenerate) design is skipped honestly and does NOT count
    nor crash the drain (fmax bug #9)."""
    good = _mk_design(tmp_path, "good")
    bad = _mk_design(tmp_path, "bad")
    # stub: 'bad' yields no winner (status only), 'good' yields a winner.
    stub = tmp_path / "fmax_stub2.py"
    stub.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "proj=Path(sys.argv[1]); rep=proj/'reports'; rep.mkdir(parents=True,exist_ok=True)\n"
        "ok = proj.name=='good'\n"
        "d={'design':proj.name,'status':'ok' if ok else 'no_clock_constraint'}\n"
        "if ok: d['winner']={'period':3.3}\n"
        "(rep/'fmax_search.json').write_text(json.dumps(d))\n")
    monkeypatch.setenv("R2G_LOOP_FMAX", str(stub))
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    for d, pth in (("good", good), ("bad", bad)):
        led.add({"design": d, "project_path": str(pth), "platform": "nangate45"})
    n = engineer_loop.fmax_drain(tmp_path / "l.jsonl")
    assert n == 1                                         # only 'good' counted
    assert "set clk_period 3.3" in (good / "constraints" / "constraint.sdc").read_text()
    assert "set clk_period 1.0" in (bad / "constraints" / "constraint.sdc").read_text()


def test_fmax_drain_idempotent_when_sdc_already_stamped(tmp_path, monkeypatch):
    """A design whose SDC is ALREADY at the report winner is NOT re-searched/re-stamped
    (idempotency keys on the SDC stamp). The stub would write 9.9 if it re-ran."""
    p = _mk_design(tmp_path, "d1", clk=4.0)               # SDC already == winner
    (p / "reports").mkdir()
    (p / "reports" / "fmax_search.json").write_text(
        json.dumps({"status": "ok", "winner": {"period": 4.0}}))
    monkeypatch.setenv("R2G_LOOP_FMAX", str(_stub_fmax(tmp_path, "ok", 9.9)))
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "d1", "project_path": str(p), "platform": "nangate45"})
    n = engineer_loop.fmax_drain(tmp_path / "l.jsonl")
    assert n == 1
    assert "set clk_period 4" in (p / "constraints" / "constraint.sdc").read_text()
    assert "9.9" not in (p / "constraints" / "constraint.sdc").read_text()


def test_fmax_drain_restamps_when_report_exists_but_sdc_unstamped(tmp_path, monkeypatch):
    """Review L4-02: a report can exist while the canonical SDC was never stamped (the
    broken-window state). The drain MUST re-stamp from the existing report — without
    re-running the search — never falsely skip on report existence."""
    p = _mk_design(tmp_path, "d2", clk=1.0)              # SDC NOT yet at the winner
    (p / "reports").mkdir()
    (p / "reports" / "fmax_search.json").write_text(
        json.dumps({"status": "ok", "winner": {"period": 2.5}}))
    # stub period differs (9.9): if it re-RAN we'd see 9.9; we must see 2.5 from the report.
    monkeypatch.setenv("R2G_LOOP_FMAX", str(_stub_fmax(tmp_path, "ok", 9.9)))
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": "d2", "project_path": str(p), "platform": "nangate45"})
    n = engineer_loop.fmax_drain(tmp_path / "l.jsonl")
    assert n == 1
    sdc = (p / "constraints" / "constraint.sdc").read_text()
    assert "set clk_period 2.5" in sdc and "9.9" not in sdc   # re-stamped from report


def test_fmax_drain_production_cli_no_conftest_path(tmp_path):
    """Review L4-01 regression: run the REAL engineer_loop.py CLI in a SUBPROCESS so it
    does NOT inherit conftest's scripts/reports sys.path injection — proving the module
    self-bootstraps that path and the SDC stamp actually lands in production (the bug my
    in-process test masked). Asserts the CLI stamps the SDC AND counts the design."""
    import subprocess
    import sys as _sys
    skill = Path(engineer_loop.__file__).resolve().parents[2]
    p = _mk_design(tmp_path, "prod0", clk=1.0)
    stub = _stub_fmax(tmp_path, "ok", 2.5)
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({
        "design": "prod0", "project_path": str(p), "platform": "nangate45",
        "kind": "normal", "state": "pending", "ts": "2026-06-24T00:00:00Z"}) + "\n")
    env = dict(os.environ)
    env["R2G_LOOP_FMAX"] = str(stub)
    env.pop("PYTHONPATH", None)                           # no test-harness path help
    r = subprocess.run(
        [_sys.executable, str(skill / "scripts" / "loop" / "engineer_loop.py"),
         "fmax-drain", "--ledger", str(ledger)],
        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "characterized 1 design(s)" in r.stdout, (r.stdout, r.stderr)
    assert "set clk_period 2.5" in (p / "constraints" / "constraint.sdc").read_text()
