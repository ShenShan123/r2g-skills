"""The engineer-loop NORMAL path fixes a known backend-abort (route congestion)
IN-LOOP rather than blind-escalating it as an unseen crash (2026-06-17, user
directive: always run the loop's fixer on a failure case).

A route-stage abort is a known, promoted-recipe symptom (route_relief), so
process_one should: ingest -> detect fail_stage == 'route' -> run the route fixer
(fix_signoff --check route) -> on success fall THROUGH to the signoff path (the
re-flow built a fresh GDS, so DRC/LVS must still run before clean -- 2026-07-02:
short-circuiting to clean fabricated 2 sky130hd cleans with NO drc.json/lvs.json
on disk), escalate only if the route fix fails. A non-route crash (synth/place/
cts) is genuinely unhandled and still escalates, WITHOUT invoking the route fixer.
"""
import json
from pathlib import Path

import engineer_loop


def _mk_proj(tmp_path: Path, name: str, last_stage: str, last_status: int) -> Path:
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    run = p / "backend" / "RUN_2026-06-17_00-00-00"
    run.mkdir(parents=True)
    rows = [{"stage": s, "status": 0} for s in ("synth", "floorplan", "place", "cts")
            if s != last_stage]
    rows.append({"stage": last_stage, "status": last_status})
    (run / "stage_log.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def _led(tmp_path, name, proj):
    led = engineer_loop.Ledger(tmp_path / "l.jsonl")
    led.add({"design": name, "project_path": str(proj), "platform": "sky130hd"})
    return led


def test_route_abort_fixed_in_loop(tmp_path, monkeypatch):
    p = _mk_proj(tmp_path, "crypto_x", "route", 124)
    led = _led(tmp_path, "crypto_x", p)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 124)   # route abort
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    calls = []
    monkeypatch.setattr(engineer_loop, "_run_fix",
                        lambda e: (calls.append(e.get("check")) or 0))
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    # Route fixer first, then the SIGNOFF fixer on the fresh GDS -- a cleared
    # route abort is "the flow completes", NOT the platform's clean state.
    assert calls[0] == "route"                  # loop drove the route fixer
    assert len(calls) == 2                      # ... then real signoff ran
    assert led.state("crypto_x") == "clean"     # clean only AFTER signoff cleared


def test_route_abort_clear_still_requires_signoff(tmp_path, monkeypatch):
    """Route fix clears the abort but signoff finds a residual: the design must
    NOT be marked clean (the 2026-07-02 fabricated-clean: ifft_core + bgm went
    ledger-clean with no drc.json/lvs.json on disk because a cleared route abort
    short-circuited past DRC/LVS entirely)."""
    p = _mk_proj(tmp_path, "crypto_s", "route", 124)
    led = _led(tmp_path, "crypto_s", p)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 124)
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    calls = []
    def fake_fix(e):
        calls.append(e.get("check"))
        return 0 if e.get("check") == "route" else 1   # route clears; signoff can't
    monkeypatch.setattr(engineer_loop, "_run_fix", fake_fix)
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert calls == ["route", None]                    # signoff fixer DID run
    assert led.state("crypto_s") == "escalated"        # honest residual, not clean
    entry = next(e for e in led.entries() if e["design"] == "crypto_s")
    assert entry.get("reason") == "catalog_exhausted", entry


def test_route_abort_escalates_if_fix_fails(tmp_path, monkeypatch):
    p = _mk_proj(tmp_path, "crypto_y", "route", 124)
    led = _led(tmp_path, "crypto_y", p)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 124)
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    monkeypatch.setattr(engineer_loop, "_run_fix", lambda e: 1)      # route fix fails
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert led.state("crypto_y") == "escalated"
    # A route abort whose route_relief fixer is exhausted/inapplicable is a KNOWN
    # backend residual, NOT an "unseen crash" — label it honestly (2026-06-17).
    entry = next(e for e in led.entries() if e["design"] == "crypto_y")
    assert entry.get("reason") == "route_congestion_residual", entry


def test_nonroute_crash_escalates_without_route_fixer(tmp_path, monkeypatch):
    p = _mk_proj(tmp_path, "crypto_z", "place", 1)                   # place crash
    led = _led(tmp_path, "crypto_z", p)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 1)
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: None)
    called = {"fix": False}
    monkeypatch.setattr(engineer_loop, "_run_fix",
                        lambda e: (called.update(fix=True) or 0))
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert called["fix"] is False               # route fixer NOT invoked
    assert led.state("crypto_z") == "escalated"
    # A genuine non-route (place) crash stays labeled unseen_crash.
    entry = next(e for e in led.entries() if e["design"] == "crypto_z")
    assert entry.get("reason") == "unseen_crash", entry
