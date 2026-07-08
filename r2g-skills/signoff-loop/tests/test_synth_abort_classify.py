"""Synth aborts are deterministic, classifiable conditions -- NOT 'unseen crashes'.

Root cause (2026-06-28 unseen_crash audit): process_one collapsed EVERY early backend
abort that was not place-FLW-0024 / place-PPL-0024 / route into reason='unseen_crash'.
That bucket (79 nangate45 designs) was really, by parsing each run's synth log:
    48  synth_missing_header   -- a `include the RTL never shipped (incomplete upstream)
    15  synth_memory_cap       -- inferred RAM exceeds Yosys' default 4096-bit cap
    10  synth_timeout          -- yosys canonicalize hit the 7200s wrapper timeout
     6  genuine downstream (floorplan/cts/synth rc2) -- the ONLY real "unseen" residue
So ~73/79 were deterministic synth conditions misfiled as mysteries, blinding the
learner and -- for the 15 memory-cap designs -- skipping a MECHANICAL, documented fix
(SKILL.md:395 / failure-patterns.md:1149: raise SYNTH_MEMORY_MAX_BITS, re-flow).

Fix mirrored on the FLW-0024 recovery (test_flw0024_recovery.py):
  * RECOVER the memory-cap case in-loop -- raise SYNTH_MEMORY_MAX_BITS and retry ONCE,
    recorded as a learnable fix_log row (strategy 'synth_memory_relax') so ingest
    projects it into a fix_event -> Tier-3 recipe (VISIBLE to learning).
  * Escalate the rest under HONEST, actionable reasons -- synth_memory_residual /
    incomplete_missing_header / synth_timeout -- never 'unseen_crash'.
"""
import json

import engineer_loop as el


def _mk_project(tmp_path, *, mem_line=None):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    body = "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
    if mem_line:
        body += mem_line + "\n"
    (p / "constraints" / "config.mk").write_text(body)
    return p


_LOGS = {
    "memcap": "Used SYNTH_MEMORY_MAX_BITS: 4096\n"
              "Error: Synthesized memory size 8192 exceeds SYNTH_MEMORY_MAX_BITS\n",
    "header": "3. Executing Verilog-2005 frontend: rv32i_alu.v\n"
              "ERROR: Can't open include file `rv32i_header.vh'!\n",
    "timeout": "make: *** [Makefile:273: do-yosys-canonicalize] Terminated\n"
               "ERROR: Stage 'synth' failed (exit code 124) after 7200s\n",
}


def _seed_synth_fail(p, *, kind, status=2):
    run = p / "backend" / "RUN_2026-06-28_00-00-00"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text(
        '{"stage":"synth","status":%s}\n' % status)
    (run / "flow.log").write_text(_LOGS[kind])


# ── detectors (signature-keyed, read the newest backend flow.log) ─────────────
def test_is_synth_memory_cap_true_on_cap_overflow(tmp_path):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="memcap")
    assert el._is_synth_memory_cap({"project_path": str(p)}) is True


def test_is_synth_memory_cap_false_on_other_synth_error(tmp_path):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="header")
    assert el._is_synth_memory_cap({"project_path": str(p)}) is False


def test_is_synth_missing_header_true(tmp_path):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="header")
    assert el._is_synth_missing_header({"project_path": str(p)}) is True


def test_is_synth_timeout_true(tmp_path):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="timeout", status=124)
    assert el._is_synth_timeout({"project_path": str(p)}) is True


def test_detectors_false_when_no_backend(tmp_path):
    e = {"project_path": str(tmp_path / "absent")}
    assert el._is_synth_memory_cap(e) is False
    assert el._is_synth_missing_header(e) is False
    assert el._is_synth_timeout(e) is False


# ── _raise_synth_memory_cap (rewrites constraints/config.mk) ──────────────────
def test_raise_cap_appends_when_absent(tmp_path):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="memcap")
    assert el._raise_synth_memory_cap({"project_path": str(p)}) is True
    txt = (p / "constraints" / "config.mk").read_text()
    assert "SYNTH_MEMORY_MAX_BITS = %d" % el._SYNTH_MEM_BITS_RETRY in txt
    assert "DESIGN_NAME = demo" in txt          # other knobs untouched


def test_raise_cap_replaces_a_lower_value(tmp_path):
    p = _mk_project(tmp_path, mem_line="export SYNTH_MEMORY_MAX_BITS = 4096")
    _seed_synth_fail(p, kind="memcap")
    assert el._raise_synth_memory_cap({"project_path": str(p)}) is True
    txt = (p / "constraints" / "config.mk").read_text()
    assert "SYNTH_MEMORY_MAX_BITS = 4096" not in txt
    assert "SYNTH_MEMORY_MAX_BITS = %d" % el._SYNTH_MEM_BITS_RETRY in txt


def test_raise_cap_noop_when_already_at_or_above_retry(tmp_path):
    # already raised as high as the loop would set it -> a retry is pointless
    big = el._SYNTH_MEM_BITS_RETRY
    p = _mk_project(tmp_path, mem_line="export SYNTH_MEMORY_MAX_BITS = %d" % big)
    _seed_synth_fail(p, kind="memcap")
    assert el._raise_synth_memory_cap({"project_path": str(p)}) is False


def test_raise_cap_noop_when_no_config(tmp_path):
    assert el._raise_synth_memory_cap({"project_path": str(tmp_path / "absent")}) is False


# ── process_one: recover the memory-cap case, escalate the rest honestly ──────
class _Led:
    def __init__(self): self.states = []
    def set_state(self, design, state, **k): self.states.append((state, k.get("reason")))


def _entry(p):
    return {"design": "demo", "project_path": str(p), "platform": "nangate45"}


def test_process_one_raises_cap_and_recovers(tmp_path, monkeypatch):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="memcap")
    flow = {"n": 0}

    def fake_flow(e):
        flow["n"] += 1
        return 2 if flow["n"] == 1 else 0       # memcap first, clean after cap raise
    monkeypatch.setattr(el, "_run_flow", fake_flow)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    # synth fails before the retry; after the cap raise the flow is clean (no failing stage)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth" if flow["n"] <= 1 else None)
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    cleaned = {}
    monkeypatch.setattr(el, "_mark_clean",
                        lambda led, conn, d, note: cleaned.setdefault(d, note))

    el.process_one(_Led(), _entry(p), None)
    assert flow["n"] == 2                                            # retried once
    assert "SYNTH_MEMORY_MAX_BITS" in (p / "constraints" / "config.mk").read_text()
    assert "demo" in cleaned                                        # recovered -> clean
    # learnable trace: a fix_log row keyed to the synth symptom (orfs_stage / synth)
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    relax = [r for r in rows if r.get("strategy") == "synth_memory_relax"]
    assert relax, "memcap recovery left no fix_log row (invisible to learning)"
    assert relax[0]["check"] == "orfs_stage"
    assert relax[0]["violation_class"] == "synth"
    assert relax[0]["verdict"] == "cleared"


def test_memcap_recovery_pairs_cap_raise_with_die_autosize(tmp_path, monkeypatch):
    """A memcap design with a FIXED DIE_AREA: the recovery must raise the cap AND convert the
    fixed die to CORE_UTILIZATION so the FF-expanded memory does not over-pack at place
    (axis_fifo went to 3072% util -> FLW-0024 with the cap raise alone; 2026-06-28 pilot)."""
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
        "export DIE_AREA  = 0 0 120 120\nexport CORE_AREA = 2 2 118 118\n")
    _seed_synth_fail(p, kind="memcap")
    flow = {"n": 0}
    monkeypatch.setattr(el, "_run_flow", lambda e: (flow.__setitem__("n", flow["n"] + 1)
                                                    or (2 if flow["n"] == 1 else 0)))
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth" if flow["n"] <= 1 else None)
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(el, "_mark_clean", lambda led, conn, d, note: None)
    el.process_one(_Led(), {"design": "demo", "project_path": str(p), "platform": "nangate45"}, None)
    cfg = (p / "constraints" / "config.mk").read_text()
    assert "SYNTH_MEMORY_MAX_BITS = 65536" in cfg          # cap raised
    assert "CORE_UTILIZATION = 20" in cfg                  # die auto-sized for FF memory
    assert "DIE_AREA" not in cfg                           # fixed die dropped


def test_memcap_clears_synth_but_fails_place_records_cleared(tmp_path, monkeypatch):
    """A memcap design whose cap-raise CLEARS synth but then over-packs at place (the
    FF-expanded RAM is big): the synth_memory_relax fix must be recorded `cleared` (the
    synth abort IS gone), NOT `no_change` tied to the downstream place failure -- else the
    loop learns the recovery fails when it worked. Verdict = (synth got past), not (flow clean)."""
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="memcap")
    flow = {"n": 0}

    def fake_flow(e):
        flow["n"] += 1
        return 2                                  # aborts twice: synth memcap, then place
    # _fail_stage tracks the backend: synth before the retry, place after the cap raise
    monkeypatch.setattr(el, "_run_flow", fake_flow)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth" if flow["n"] <= 1 else "place")
    el.process_one(_Led(), _entry(p), None)
    rows = [json.loads(l) for l in
            (p / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    relax = [r for r in rows if r.get("strategy") == "synth_memory_relax"]
    assert relax and relax[0]["verdict"] == "cleared"   # synth cleared, despite place fail
    assert relax[0]["after"] == 0


def test_memcap_that_cannot_be_raised_escalates_residual_not_unseen(tmp_path, monkeypatch):
    # cap already at the retry ceiling and it STILL overflows -> honest residual
    big = el._SYNTH_MEM_BITS_RETRY
    p = _mk_project(tmp_path, mem_line="export SYNTH_MEMORY_MAX_BITS = %d" % big)
    _seed_synth_fail(p, kind="memcap")
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth")
    led = _Led()
    el.process_one(led, _entry(p), None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["synth_memory_residual"]


def test_missing_header_escalates_incomplete_not_unseen(tmp_path, monkeypatch):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="header")
    monkeypatch.setattr(el, "_run_flow", lambda e: 2)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth")
    led = _Led()
    el.process_one(led, _entry(p), None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["incomplete_missing_header"]


def test_synth_timeout_escalates_timeout_not_unseen(tmp_path, monkeypatch):
    p = _mk_project(tmp_path)
    _seed_synth_fail(p, kind="timeout", status=124)
    monkeypatch.setattr(el, "_run_flow", lambda e: 124)
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth")
    led = _Led()
    el.process_one(led, _entry(p), None)
    assert [r for (s, r) in led.states if s == "escalated"] == ["synth_timeout"]


# ── memory-size gate: FF-expand modest memories, fakeram (residual) for large ones ──
def _seed_memcap_with_size(p, bits):
    run = p / "backend" / "RUN_2026-06-28_00-00-00"
    run.mkdir(parents=True)
    (run / "stage_log.jsonl").write_text('{"stage":"synth","status":2}\n')
    (run / "flow.log").write_text(
        f"Largest single memory instance: {bits} bits (module m)\n"
        "Error: Synthesized memory size 4096 exceeds SYNTH_MEMORY_MAX_BITS\n")


def test_synth_largest_memory_bits_parses(tmp_path):
    p = _mk_project(tmp_path)
    _seed_memcap_with_size(p, 40960)
    assert el._synth_largest_memory_bits({"project_path": str(p)}) == 40960
    assert el._synth_memory_ff_expandable({"project_path": str(p)}) is False   # > limit


def test_synth_memory_ff_expandable_modest_and_unparseable(tmp_path):
    p = _mk_project(tmp_path)
    _seed_memcap_with_size(p, 8192)
    assert el._synth_memory_ff_expandable({"project_path": str(p)}) is True    # <= limit
    # unparseable size -> default expandable (do not regress the prior FF-expand behavior)
    (p / "backend" / "RUN_2026-06-28_00-00-00" / "flow.log").write_text(
        "Error: Synthesized memory size 4096 exceeds SYNTH_MEMORY_MAX_BITS\n")
    assert el._synth_largest_memory_bits({"project_path": str(p)}) is None
    assert el._synth_memory_ff_expandable({"project_path": str(p)}) is True


def test_large_memcap_escalates_fakeram_not_ff_expand(tmp_path, monkeypatch):
    """A memcap whose memory is too LARGE to FF-expand must NOT raise the cap (FF expansion
    would tail-block on a 4h-LVS route-timeout design); escalate synth_memory_residual
    routed to a fakeram macro -- never FF-expand it (2026-06-28 iter-7)."""
    p = _mk_project(tmp_path)
    _seed_memcap_with_size(p, 40960)
    flows = {"n": 0}
    monkeypatch.setattr(el, "_run_flow", lambda e: (flows.__setitem__("n", flows["n"] + 1) or 2))
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth")
    led = _Led()
    el.process_one(led, _entry(p), None)
    assert flows["n"] == 1                                       # NO recovery retry
    assert [r for (s, r) in led.states if s == "escalated"] == ["synth_memory_residual"]
    assert "SYNTH_MEMORY_MAX_BITS" not in (p / "constraints" / "config.mk").read_text()


def test_modest_memcap_still_ff_expands_and_recovers(tmp_path, monkeypatch):
    """Regression: a MODEST memcap (<= limit) still FF-expands + recovers (the gate must
    not block the cases the recipe was built for)."""
    p = _mk_project(tmp_path)
    _seed_memcap_with_size(p, 8192)
    flows = {"n": 0}
    monkeypatch.setattr(el, "_run_flow",
                        lambda e: (flows.__setitem__("n", flows["n"] + 1) or (2 if flows["n"] == 1 else 0)))
    monkeypatch.setattr(el, "_ingest", lambda e: None)
    monkeypatch.setattr(el, "_fail_stage", lambda e: "synth" if flows["n"] <= 1 else None)
    monkeypatch.setattr(el, "_signoff_status", lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(el, "_mark_clean", lambda led, conn, d, note: None)
    el.process_one(_Led(), _entry(p), None)
    assert flows["n"] == 2                                       # FF-expanded + retried
    assert "SYNTH_MEMORY_MAX_BITS = 65536" in (p / "constraints" / "config.mk").read_text()
