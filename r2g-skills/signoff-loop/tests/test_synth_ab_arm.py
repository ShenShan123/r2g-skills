"""synth_memory_relax must be A/B-PROMOTABLE: a synth backend-abort arm, judged on
'synth cleared' (the thing the recipe fixes), mirroring the place/route/timing arms.

Without this (iter-2 state) the auto-enqueued synth_memory_relax candidate routed to
`--check both` (the signoff fixer), whose arms can't diverge on a design that aborts at
SYNTH before signoff -> inconclusive forever -> coverage-gap skip -> never promoted. The
recipe works + is applied in-loop, but never reaches `promoted` status.

Fix (mirrors the place backend-abort arm + the timing metric):
  - _symptom_check routes synth_memory_relax -> 'synth' (apply-then-flow arm).
  - process_one routes a check='synth' ab_arm through _process_backend_ab_arm.
  - _apply_recipe_strategy(synth) applies the SAME recovery as the in-loop fix
    (raise SYNTH_MEMORY_MAX_BITS + pair with CORE_UTILIZATION).
  - _arm_metric(synth=True) judges on 'synth cleared' (the FF-expanded design may carry
    downstream DRC/LVS residuals that would tie both arms on is_success), like timing.
"""
import json

import engineer_loop as el


def _mk(tmp_path, *, mem_line=None, die=False):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    body = "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
    if die:
        body += "export DIE_AREA = 0 0 120 120\nexport CORE_AREA = 2 2 118 118\n"
    if mem_line:
        body += mem_line + "\n"
    (p / "constraints" / "config.mk").write_text(body)
    return p


def _seed_stage(p, *, synth_status):
    run = p / "backend" / "RUN_2026-06-28_00-00-00"
    run.mkdir(parents=True)
    rows = [{"stage": "synth", "status": synth_status, "elapsed_s": 3}]
    if synth_status in (0, "0", "pass"):
        rows.append({"stage": "floorplan", "status": 0, "elapsed_s": 5})
    run.joinpath("stage_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")


# ── _symptom_check routes synth_memory_relax to the 'synth' apply-then-flow arm ──
def test_symptom_check_routes_synth_strategy(tmp_path):
    assert el._symptom_check(None, None, strategy="synth_memory_relax") == "synth"
    assert "synth_memory_relax" in el._SYNTH_STRATEGIES


# ── _apply_recipe_strategy(synth) applies the in-loop recovery to arm B's config ──
def test_apply_recipe_strategy_synth_raises_cap_and_sizes_die(tmp_path):
    p = _mk(tmp_path, die=True)            # fixed die -> must convert + raise cap
    el._apply_recipe_strategy({"project_path": str(p), "strategy": "synth_memory_relax",
                               "arm": "B"})
    cfg = (p / "constraints" / "config.mk").read_text()
    assert "SYNTH_MEMORY_MAX_BITS = 65536" in cfg
    assert "CORE_UTILIZATION = 20" in cfg
    assert "DIE_AREA" not in cfg


# ── _synth_cleared_ondisk reads the arm's stage_log ──────────────────────────
def test_synth_cleared_ondisk(tmp_path):
    a = _mk(tmp_path / "a"); _seed_stage(a, synth_status=2)      # arm A control: memcap abort
    b = _mk(tmp_path / "b"); _seed_stage(b, synth_status=0)      # arm B: synth cleared
    assert el._synth_cleared_ondisk(str(a)) is False
    assert el._synth_cleared_ondisk(str(b)) is True
    assert el._synth_cleared_ondisk(str(tmp_path / "absent")) is False


# ── _arm_metric(synth=True) judges on synth-cleared, not generic is_success ──
def test_arm_metric_synth_mode(tmp_path, monkeypatch):
    import knowledge_db
    # generic is_success would read the run row; synth mode must IGNORE it and use the
    # stage_log so arm B (synth cleared, maybe DRC-dirty) still beats arm A (synth abort).
    monkeypatch.setattr(knowledge_db, "is_success", lambda r: False)   # would tie both arms

    class _Conn:
        def execute(self, *a):
            class C:
                def fetchone(self):
                    return (10.0, 1, "clean", "clean", None, None, "fail", 0.5, None, None)
            return C()
    b = _mk(tmp_path / "b"); _seed_stage(b, synth_status=0)
    m = el._arm_metric(_Conn(), str(b), synth=True)
    assert m["is_success"] is True            # judged synth-cleared, not is_success=False


# ── process_one routes a check='synth' ab_arm to the backend-abort arm runner ──
def test_process_one_routes_synth_ab_arm(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(el, "_process_backend_ab_arm",
                        lambda led, e, conn: called.setdefault("hit", e.get("check")))
    monkeypatch.setattr(el, "_journal_ab_launch", lambda e: None)
    entry = {"design": "demo_abB_synth_me_0", "project_path": str(tmp_path),
             "platform": "nangate45", "kind": "ab_arm", "arm": "B",
             "check": "synth", "strategy": "synth_memory_relax"}
    el.process_one(_Led(), entry, None)
    assert called.get("hit") == "synth"       # routed to _process_backend_ab_arm, not signoff


class _Led:
    def __init__(self): self.states = []
    def set_state(self, design, state, **k): self.states.append((state, k.get("reason")))


class _RowConn:
    """conn whose every query returns one non-None run row (so _arm_metric proceeds to the
    synth branch, which reads the stage_log and ignores the row)."""
    def execute(self, *a):
        class C:
            def fetchone(self_inner):
                return (10.0, 1, "clean", "clean", None, None, "fail", 0.5, None, None)
        return C()


# ── synth A/B arm runs SYNTH-ONLY (judged on synth-cleared, not full signoff) ──
def test_synth_arm_runs_synth_only(tmp_path, monkeypatch):
    import subprocess
    captured = {}

    class _R:
        returncode = 0
    def fake_run(cmd, *a, **kw):
        captured["env"] = kw.get("env") or {}
        return _R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    # a SYNTH ab_arm -> synth-only + bounded timeout
    el._run_flow({"project_path": str(tmp_path), "platform": "nangate45",
                  "kind": "ab_arm", "check": "synth", "strategy": "synth_memory_relax"})
    assert captured["env"].get("ORFS_STAGES") == "synth"
    assert captured["env"].get("ORFS_TIMEOUT") == str(el._SYNTH_ARM_TIMEOUT)

    # a NORMAL design (or a place/route arm) -> full flow, no stage restriction
    captured.clear()
    el._run_flow({"project_path": str(tmp_path), "platform": "nangate45"})
    assert "ORFS_STAGES" not in captured["env"]
    captured.clear()
    el._run_flow({"project_path": str(tmp_path), "platform": "nangate45",
                  "kind": "ab_arm", "check": "place", "strategy": "core_util_relief"})
    assert "ORFS_STAGES" not in captured["env"]


# ── end-to-end (flows-free): a synth A/B pair records a WIN so the recipe can promote ──
def test_synth_ab_trial_records_win(tmp_path, monkeypatch):
    import ab_runner
    import knowledge_db
    led = el.Ledger(tmp_path / "l.jsonl")
    key = {"symptom_id": "synthsym", "design_class": "crypto/large",
           "platform": "nangate45", "strategy": "synth_memory_relax"}
    a = _mk(tmp_path / "a"); _seed_stage(a, synth_status=2)      # arm A control: memcap abort
    b = _mk(tmp_path / "b"); _seed_stage(b, synth_status=0)      # arm B: synth cleared
    for d, arm, pp, st in (("d_abA", "A", a, "escalated"), ("d_abB", "B", b, "clean")):
        led.add({"design": d, "project_path": str(pp), "kind": "ab_arm", "arm": arm,
                 "strategy": "synth_memory_relax", "repeat": 0, "state": st,
                 "ab_key": key, "match_level": "exact"})
    # is_success would mark BOTH arms success -> a tie; only the synth metric breaks it.
    monkeypatch.setattr(knowledge_db, "is_success", lambda r: True)
    recorded = []
    monkeypatch.setattr(ab_runner, "record_trial", lambda conn, **kw: recorded.append(kw))
    el.judge_finished_trials(led, _RowConn())
    assert len(recorded) == 1
    assert recorded[0]["verdict"] == "win"      # A not-cleared, B cleared -> decisive win
