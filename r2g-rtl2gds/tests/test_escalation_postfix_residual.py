"""A catalog_exhausted escalation must record the POST-fix residual, not the pre-fix snapshot.

Root cause (2026-06-28 audit): process_one captured `status = _signoff_status(entry)` BEFORE
`_run_fix` ran, then escalated with `notes=json.dumps(status)`. On a first signoff pass no
DRC/LVS report exists yet, so that snapshot is {drc:unknown,lvs:unknown} -- which made all 184
`{unknown,unknown}` catalog_exhausted escalations look identical while their genuine residuals
were diverse (80 drc=stuck / 67 lvs=fail / 29 both). The escalation queue (the operator's primary
view) was uninformative. Fix: re-read _signoff_status AFTER _run_fix so the notes carry the real
residual the fixer could not clear.
"""
import json

import engineer_loop as el


def _entry(tmp_path):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "constraints" / "config.mk").write_text("export DESIGN_NAME = demo\n")
    return {"design": "demo", "project_path": str(p), "platform": "nangate45"}


class _Led:
    def __init__(self): self.states = []
    def set_state(self, design, state, **k): self.states.append((state, k.get("reason")))


def test_escalation_notes_carry_postfix_residual(tmp_path, monkeypatch):
    import escalations
    entry = _entry(tmp_path)
    # _signoff_status is read twice: pre-fix (unknown -- no report yet) then post-fix (real residual)
    seq = [{"drc": "unknown", "lvs": "unknown"}, {"drc": "stuck", "lvs": "fail"}]
    calls = {"n": 0}

    def fake_signoff(e):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]
    monkeypatch.setattr(el, "_run_flow", lambda e: 0)          # flow ok -> reach signoff
    monkeypatch.setattr(el, "_signoff_status", fake_signoff)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_run_fix", lambda e: 1)           # fixer cannot clear
    captured = {}
    monkeypatch.setattr(escalations, "open_escalation",
                        lambda conn, **kw: captured.update(kw))

    result = el.process_one(_Led(), entry, conn=object())
    assert result == "escalated"
    notes = json.loads(captured["notes"])
    assert notes == {"drc": "stuck", "lvs": "fail"}            # POST-fix residual, not unknown
    # 2026-07-05: a stuck residual routes to its OWN reason (scan-bound runbook),
    # no longer the generic catalog_exhausted (see _signoff_escalation_reason).
    assert captured["reason"] == "signoff_stuck_scan"


def test_clean_first_pass_never_escalates(tmp_path, monkeypatch):
    # guard: a genuinely-clean first signoff pass still short-circuits to clean (no regression)
    entry = _entry(tmp_path)
    monkeypatch.setattr(el, "_run_flow", lambda e: 0)
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    cleaned = {}
    monkeypatch.setattr(el, "_mark_clean", lambda led, conn, d, note: cleaned.setdefault(d, note))
    assert el.process_one(_Led(), entry, conn=None) == "clean"
    assert "demo" in cleaned
