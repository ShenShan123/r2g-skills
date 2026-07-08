"""FLW-0024 (place density > 1.0) is a RECOVERABLE 'die too small' over-pack, not
an unseen crash. The loop must auto-size the die (DIE_AREA -> CORE_UTILIZATION) and
retry; only if it STILL over-packs does it escalate, with an honest reason.

Root cause (2026-06-23): setup_rtl_designs.py sized the die from RTL LINE COUNT, not
gate count, so a compact-but-dense design got a fixed DIE_AREA (50x50) far too small
for its synthesized cells -> [ERROR FLW-0024] at global placement -> escalated as
`unseen_crash` (the dominant unseen_crash bucket: ~38 of 81). Same class as the
sky130 mk_*_project sizing bug. Validated live: dma_controller's 50x50 die held
6442um^2 of cells (2.6x too big); CORE_UTILIZATION=30 auto-sized to 31% util -> placed.
"""
import json

import engineer_loop as el


def _mk_project(tmp_path, *, die=True):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    cfg = p / "constraints" / "config.mk"
    if die:
        cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
                       "export DIE_AREA  = 0 0 50 50\nexport CORE_AREA = 2 2 48 48\n"
                       "export PLACE_DENSITY_LB_ADDON = 0.2\n")
    else:
        cfg.write_text("export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
                       "export CORE_UTILIZATION = 25\n")
    return p


def _seed_backend(p, *, flw0024=True, stage="place"):
    run = p / "backend" / "RUN_2026-06-23_00-00-00"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        '{"stage":"synth","status":0}\n{"stage":"floorplan","status":0}\n'
        f'{{"stage":"{stage}","status":2}}\n')
    (run / "flow.log").write_text(
        "[ERROR FLW-0024] Place density exceeds 1.0\n" if flw0024
        else "[ERROR GPL-0001] NesterovSolve diverged\n")


# ── _config_knob (regex-free config parse) ───────────────────────────────────
def test_config_knob_parses_export_lines():
    assert el._config_knob("export DIE_AREA  = 0 0 50 50") == "DIE_AREA"
    assert el._config_knob("export CORE_UTILIZATION = 30") == "CORE_UTILIZATION"
    assert el._config_knob("export DIE_AREA=0 0 5 5") == "DIE_AREA"   # no space before =
    assert el._config_knob("# a comment") == ""
    assert el._config_knob("") == ""


# ── _resize_to_core_util ─────────────────────────────────────────────────────
def test_resize_converts_die_area_and_preserves_other_knobs(tmp_path):
    p = _mk_project(tmp_path, die=True)
    assert el._resize_to_core_util({"project_path": str(p)}, util=30) is True
    txt = (p / "constraints" / "config.mk").read_text()
    assert "CORE_UTILIZATION = 30" in txt
    assert "DIE_AREA" not in txt and "CORE_AREA" not in txt
    # untouched knobs survive; PLACE_DENSITY_LB_ADDON never relaxed (hard rule)
    assert "PLACE_DENSITY_LB_ADDON = 0.2" in txt
    assert "DESIGN_NAME = demo" in txt


def test_resize_is_noop_when_already_core_util(tmp_path):
    # already auto-sized -> nothing to relax; a retry would be pointless
    p = _mk_project(tmp_path, die=False)
    assert el._resize_to_core_util({"project_path": str(p)}) is False
    assert "CORE_UTILIZATION = 25" in (p / "constraints" / "config.mk").read_text()


def test_resize_is_noop_when_no_config(tmp_path):
    assert el._resize_to_core_util({"project_path": str(tmp_path / "absent")}) is False


# ── _is_flw0024 (distinguishes recoverable over-pack from divergence) ─────────
def test_is_flw0024_true_on_density_overflow(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, flw0024=True)
    assert el._is_flw0024({"project_path": str(p)}) is True


def test_is_flw0024_false_on_nesterov_divergence(tmp_path):
    p = _mk_project(tmp_path)
    _seed_backend(p, flw0024=False)              # GPL divergence != FLW-0024
    assert el._is_flw0024({"project_path": str(p)}) is False


# ── process_one: resize + retry recovers; honest reason if it can't ──────────
class _Led:
    def __init__(self): self.states = []
    def set_state(self, design, state, **k): self.states.append((state, k.get("reason")))


def test_process_one_resizes_and_retries_then_recovers(tmp_path, monkeypatch):
    p = _mk_project(tmp_path, die=True)
    _seed_backend(p, flw0024=True)
    entry = {"design": "demo", "project_path": str(p), "platform": "nangate45"}

    flow_calls = {"n": 0}
    def fake_flow(e):
        flow_calls["n"] += 1
        return 2 if flow_calls["n"] == 1 else 0   # FLW-0024 first, clean after resize
    monkeypatch.setattr(el, "_run_flow", fake_flow)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "place")
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    cleaned = {}
    monkeypatch.setattr(el, "_mark_clean",
                        lambda led, conn, d, note: cleaned.setdefault(d, note))

    el.process_one(_Led(), entry, None)
    assert flow_calls["n"] == 2                                  # retried once
    assert "CORE_UTILIZATION" in (p / "constraints" / "config.mk").read_text()
    assert "demo" in cleaned                                     # recovered -> clean
    # bug #6: the resize must leave a LEARNABLE fix_log row so the next ingest
    # projects it into a fix_event (keyed to the place symptom af17c0ba) -> recipe.
    # This makes the recovery VISIBLE to learning (not A/B-promoted — the place-resize
    # is hard-coded; promotion is deferred, see the _record_resize_fix scope note).
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    resize = [r for r in rows if r.get("strategy") == "core_util_relief"]
    assert resize, "resize recovery left no fix_log row (invisible to learning)"
    assert resize[0]["check"] == "orfs_stage"
    assert resize[0]["violation_class"] == "place"
    assert resize[0]["verdict"] == "cleared"                     # honest: it cleared


def test_resize_that_does_not_recover_records_no_change(tmp_path, monkeypatch):
    """A resize whose retry STILL over-packs must record verdict=no_change (negative
    learning), not a fabricated win, AND escalate place_density_residual."""
    p = _mk_project(tmp_path, die=True)
    _seed_backend(p, flw0024=True)
    entry = {"design": "demo", "project_path": str(p), "platform": "nangate45"}
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)            # never recovers
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "place")
    led = _Led()
    el.process_one(led, entry, None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["place_density_residual"]
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    resize = [r for r in rows if r.get("strategy") == "core_util_relief"]
    assert resize and resize[0]["verdict"] == "no_change"       # honest negative


def test_process_one_escalates_honestly_when_resize_insufficient(tmp_path, monkeypatch):
    # config ALREADY CORE_UTILIZATION (resize is a no-op) and it still FLW-0024s:
    # an honest place_density_residual, never `unseen_crash`.
    p = _mk_project(tmp_path, die=False)
    _seed_backend(p, flw0024=True)
    entry = {"design": "demo", "project_path": str(p), "platform": "nangate45"}
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "place")

    led = _Led()
    el.process_one(led, entry, None)
    esc = [r for (s, r) in led.states if s == "escalated"]
    assert esc == ["place_density_residual"]                    # honest, not unseen_crash
