"""Signoff gate before dataset construction (failure-patterns.md #34).

A 6_final.def alone is NOT sign-off: DRC/LVS run in a separate post-finish
step, route/antenna residuals survive a "completed" flow, and an aborted ORFS
can leave a plausible DEF behind. The gate (scripts/flow/signoff_gate.py):

  - required, fail-closed: drc.json in {clean,clean_beol}; lvs.json in
    {clean,skipped}; ORFS complete (stage_log.jsonl 'finish' status 0, or
    run-meta.json make_status==0); route residuals == 0 when provable.
    A MISSING drc/lvs report blocks in enforce mode — the old vacuous pass
    (no report -> no check) is the exact trap this replaces.
  - advisory, recorded only: timing (negative slack is a valid training label).
  - modes: enforce (run_graphs.sh default) / warn (run_labels.sh +
    run_features.sh default) / off; R2G_DEF override downgrades to warn.
  - verdict always written to reports/signoff_gate.json; build_graphs.py embeds
    it as the manifest's signoff_health; the verifier fails a dataset whose
    provenance is unrecorded or whose gate verdict is dirty.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_GATE = os.path.join(_FLOW, "signoff_gate.py")
_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "tools")

_spec = importlib.util.spec_from_file_location("signoff_gate", _GATE)
sg = importlib.util.module_from_spec(_spec)
sys.modules["signoff_gate"] = sg
_spec.loader.exec_module(sg)

_gsm_spec = importlib.util.spec_from_file_location(
    "graph_skip_manifest", os.path.join(_FLOW, "graph_skip_manifest.py"))
gsm = importlib.util.module_from_spec(_gsm_spec)
sys.modules["graph_skip_manifest"] = gsm
_gsm_spec.loader.exec_module(gsm)

_vspec = importlib.util.spec_from_file_location(
    "verify_graph_dataset", os.path.join(_TOOLS, "verify_graph_dataset.py"))
vgd = importlib.util.module_from_spec(_vspec)
sys.modules["verify_graph_dataset"] = vgd
_vspec.loader.exec_module(vgd)

CLEAN_STAGES = [{"stage": s, "status": 0, "elapsed_s": 1}
                for s in ("synth", "floorplan", "place", "cts", "route", "finish")]


def _proj(tmp_path, *, drc="clean", lvs="clean", route=0, stage_log=CLEAN_STAGES,
          run_meta=None, ppa=None, timing_check=None, route_rpt=None,
          drc_categories=None, antenna_marker=None, route_json=None):
    """Minimal project fixture. Pass None for an artifact to omit it; `route`
    is total_violations for reports/route.json (None omits the file).
    `drc_categories` injects a KLayout-style {class:{count}} breakdown into
    drc.json; `antenna_marker` writes reports/antenna_nonconverged.json;
    `route_json` overrides the whole route.json dict (for the status-vs-count
    honesty tests)."""
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    run = proj / "backend" / "RUN_2026-07-01_00-00-00"
    run.mkdir(parents=True, exist_ok=True)
    if drc is not None:
        _drc = {"status": drc, "total_violations": 0 if drc.startswith("clean") else 7}
        if drc_categories is not None:
            _drc["categories"] = drc_categories
        json.dump(_drc, open(rep / "drc.json", "w"))
    if antenna_marker is not None:
        json.dump(antenna_marker, open(rep / "antenna_nonconverged.json", "w"))
    if lvs is not None:
        json.dump({"status": lvs, "mismatch_count": 0 if lvs in ("clean", "skipped") else 3},
                  open(rep / "lvs.json", "w"))
    if route_json is not None:
        json.dump(route_json, open(rep / "route.json", "w"))
    elif route is not None:
        json.dump({"status": "clean" if route == 0 else "fail",
                   "total_violations": route}, open(rep / "route.json", "w"))
    if stage_log is not None:
        with open(run / "stage_log.jsonl", "w") as f:
            for rec in stage_log:
                f.write(json.dumps(rec) + "\n")
    if run_meta is not None:
        json.dump(run_meta, open(run / "run-meta.json", "w"))
    if ppa is not None:
        json.dump(ppa, open(rep / "ppa.json", "w"))
    if timing_check is not None:
        json.dump(timing_check, open(rep / "timing_check.json", "w"))
    if route_rpt is not None:
        rdir = run / "reports_orfs"
        rdir.mkdir(exist_ok=True)
        (rdir / "5_route_drc.rpt").write_text(route_rpt, encoding="utf-8")
    return str(proj), str(run)


def _cli(proj, run, mode="enforce", extra=()):
    r = subprocess.run([sys.executable, _GATE, proj, "--run-dir", run,
                        "--mode", mode, *extra],
                       capture_output=True, text=True, timeout=60)
    verdict = json.load(open(os.path.join(proj, "reports", "signoff_gate.json")))
    return r.returncode, verdict, r.stderr


# ---- the gate's verdict logic ------------------------------------------------

def test_all_clean_pass(tmp_path):
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass" and not v["blockers"] and not v["caveats"], v


def test_def_bound_to_run_passes(tmp_path):
    """P0-17 (2026-07-15): a DEF that lives UNDER the reports' run dir is bound to it
    -> clean pass, and the verdict records a def_fingerprint for provenance."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    def_path = os.path.join(run, "results", "6_final.def")
    os.makedirs(os.path.dirname(def_path), exist_ok=True)
    open(def_path, "w").write("VERSION 5.8 ;\n")
    v = sg.evaluate(proj, run, def_path)
    assert v["status"] == "pass" and not v["blockers"], v
    assert v["checks"]["binding"]["status"] == "bound"
    assert v["checks"]["binding"]["def_fingerprint"]["path"].endswith("6_final.def")


def test_def_from_other_run_is_blocked(tmp_path):
    """P0-17: a clean report bundle from one run must NOT certify a DEF from ANOTHER run
    (design/platform names unchanged). The DEF is UNBOUND -> a hard block, even though
    every report reads clean."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    other = tmp_path / "other_run" / "results"
    other.mkdir(parents=True)
    def_path = other / "6_final.def"
    def_path.write_text("VERSION 5.8 ;\n")
    v = sg.evaluate(proj, run, str(def_path))
    assert v["status"] == "dirty" and "binding" in v["blockers"], v
    assert v["checks"]["binding"]["status"] == "unbound"


def test_no_def_supplied_makes_no_binding_claim(tmp_path):
    """A legacy 2-arg evaluate (no DEF) makes no binding claim -> stays a clean pass
    (binding is 'unknown' but not a caveat), preserving backward compatibility."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass" and not v["caveats"], v
    assert v["checks"]["binding"]["status"] == "unknown"


def test_no_timing_report_is_caveat_only(tmp_path):
    proj, run = _proj(tmp_path)   # everything clean, timing unrecorded
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass_with_caveats" and "timing=unknown" in v["caveats"]


def test_dirty_drc_blocks_enforce(tmp_path):
    proj, run = _proj(tmp_path, drc="fail")
    rc, verdict, err = _cli(proj, run)
    assert rc == 3
    assert verdict["status"] == "dirty" and "drc" in verdict["blockers"]
    assert "NOT SIGNED OFF" in err


def test_missing_drc_fail_closed(tmp_path):
    """The old vacuous-pass trap: DRC never ran -> no report -> must BLOCK,
    not sail through."""
    proj, run = _proj(tmp_path, drc=None)
    rc, verdict, _ = _cli(proj, run)
    assert rc == 3
    assert verdict["checks"]["drc"]["status"] == "missing"
    assert "drc" in verdict["blockers"]


def test_missing_lvs_fail_closed(tmp_path):
    proj, run = _proj(tmp_path, lvs=None)
    rc, verdict, _ = _cli(proj, run)
    assert rc == 3 and "lvs" in verdict["blockers"]


def test_warn_mode_records_and_proceeds(tmp_path):
    proj, run = _proj(tmp_path, drc="fail")
    rc, verdict, err = _cli(proj, run, mode="warn")
    assert rc == 0
    assert verdict["status"] == "dirty" and verdict["mode"] == "warn"
    assert "proceeding anyway" in err


def test_def_override_downgrades_enforce(tmp_path):
    """An explicit R2G_DEF override is a deliberate operator decision (e.g. the
    no-backend verifier flows) — enforce degrades to warn, recorded."""
    proj, run = _proj(tmp_path, drc="fail")
    rc, verdict, _ = _cli(proj, run, mode="enforce", extra=("--def-overridden",))
    assert rc == 0
    assert verdict["mode"] == "warn" and verdict["def_overridden"] is True


def test_lvs_skipped_is_caveat_not_blocker(tmp_path):
    """`skipped` is an EXPLICIT decision recorded by the signoff step (portless
    design / no platform deck) — unlike a missing report."""
    proj, run = _proj(tmp_path, lvs="skipped")
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass_with_caveats" and "lvs=skipped" in v["caveats"]


def test_drc_clean_beol_is_caveat(tmp_path):
    proj, run = _proj(tmp_path, drc="clean_beol")
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass_with_caveats" and "drc=clean_beol" in v["caveats"]


def test_orfs_stage_failure_blocks(tmp_path):
    proj, run = _proj(tmp_path, stage_log=[{"stage": "synth", "status": 0},
                                           {"stage": "floorplan", "status": 2}])
    v = sg.evaluate(proj, run)
    assert "orfs" in v["blockers"] and v["checks"]["orfs"]["status"] == "fail"


def test_orfs_missing_finish_blocks(tmp_path):
    """A run that stopped mid-flow (crash/kill) leaves a clean-so-far stage_log
    with no 'finish' entry — that is incomplete, not complete."""
    proj, run = _proj(tmp_path, stage_log=CLEAN_STAGES[:-1])
    v = sg.evaluate(proj, run)
    assert "orfs" in v["blockers"] and v["checks"]["orfs"]["status"] == "incomplete"


def test_orfs_runmeta_fallback(tmp_path):
    proj, run = _proj(tmp_path, stage_log=None, run_meta={"make_status": 0})
    assert sg.evaluate(proj, run)["checks"]["orfs"]["status"] == "complete"
    proj2, run2 = _proj(tmp_path / "b", stage_log=None, run_meta={"make_status": 2})
    v = sg.evaluate(proj2, run2)
    assert "orfs" in v["blockers"] and v["checks"]["orfs"]["status"] == "fail"


def test_orfs_unverifiable_blocks(tmp_path):
    """No stage_log.jsonl AND no run-meta make_status: completion is
    unverifiable -> fail-closed in enforce mode."""
    proj, run = _proj(tmp_path, stage_log=None)
    v = sg.evaluate(proj, run)
    assert "orfs" in v["blockers"] and v["checks"]["orfs"]["status"] == "unknown"


def test_route_json_violations_block(tmp_path):
    proj, run = _proj(tmp_path, route=4)
    v = sg.evaluate(proj, run)
    assert "route" in v["blockers"] and v["checks"]["route"]["violations"] == 4


def test_route_rpt_fallback(tmp_path):
    proj, run = _proj(tmp_path, route=None,
                      route_rpt="  violation type: Short\n  violation type: AntennaRatio\n")
    v = sg.evaluate(proj, run)
    assert "route" in v["blockers"] and v["checks"]["route"]["violations"] == 2
    proj2, run2 = _proj(tmp_path / "b", route=None, route_rpt="")
    v2 = sg.evaluate(proj2, run2)
    assert v2["checks"]["route"]["status"] == "clean"


def test_route_unknown_is_caveat(tmp_path):
    """Neither route.json nor a 5_route_drc.rpt: recorded, not a block — a
    clean full DRC deck already covers routed geometry."""
    proj, run = _proj(tmp_path, route=None)
    v = sg.evaluate(proj, run)
    assert "route" not in v["blockers"] and "route=unknown" in v["caveats"]


# ---- route.json status-vs-count honesty (failure-patterns.md #38) --------------

def test_route_status_clean_but_count_positive_is_dirty(tmp_path):
    """A foreign route.json claiming status='clean' with total_violations>0 must
    NOT read clean via short-circuit — gate on the COUNT."""
    proj, run = _proj(tmp_path, route=None,
                      route_json={"status": "clean", "total_violations": 5})
    v = sg.evaluate(proj, run)
    assert v["checks"]["route"]["status"] == "dirty" and "route" in v["blockers"]
    assert v["checks"]["route"]["violations"] == 5


def test_route_status_unknown_is_caveat_not_dirty(tmp_path):
    """route.json status='unknown' (route stage never reached) is 'unknown' (a
    caveat), not a spurious 'dirty' blocker."""
    proj, run = _proj(tmp_path, route=None,
                      route_json={"status": "unknown", "total_violations": None})
    v = sg.evaluate(proj, run)
    assert v["checks"]["route"]["status"] == "unknown"
    assert "route" not in v["blockers"] and "route=unknown" in v["caveats"]


# ---- antenna as its OWN decoupled dimension (codex #5, failure-patterns #38) ----

def test_antenna_clean_when_full_drc_clean(tmp_path):
    proj, run = _proj(tmp_path)  # drc=clean, no categories
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "clean"
    assert not any(c.startswith("antenna=") for c in v["caveats"])


def test_antenna_fail_from_drc_categories(tmp_path):
    """Routing-DRC clean but an antenna-class DRC violation present: antenna is
    its own 'fail' dimension, recorded as a caveat (drc already blocks)."""
    proj, run = _proj(tmp_path, drc="fail",
                      drc_categories={"Antenna_ratio": {"count": 3},
                                      "met1.spacing": {"count": 4}})
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "fail"
    assert v["checks"]["antenna"]["violations"] == 3
    assert "antenna=fail" in v["caveats"]


def test_antenna_clean_when_drc_fails_on_non_antenna(tmp_path):
    """The decoupling: a shorts-only DRC failure leaves the antenna metric clean."""
    proj, run = _proj(tmp_path, drc="fail",
                      drc_categories={"met1.spacing": {"count": 9}})
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "clean"


def test_antenna_nonconverged_marker(tmp_path):
    """The suggestion's exact stall example rides the gate as its own dimension."""
    proj, run = _proj(tmp_path, drc="clean_beol",
                      antenna_marker={"class": "antenna", "residual_count": 2,
                                      "fix_iters": 6, "strategies_tried": ["antenna_a", "antenna_b"]})
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "nonconverged"
    assert v["checks"]["antenna"]["residual_count"] == 2
    assert "antenna=nonconverged" in v["caveats"]


def test_antenna_not_covered_under_clean_beol(tmp_path):
    """clean_beol disables the ANTENNA rule group — antenna is genuinely NOT
    verified, must read not_covered (not a false clean)."""
    proj, run = _proj(tmp_path, drc="clean_beol")
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "not_covered"
    assert "antenna=not_covered" in v["caveats"]


def test_antenna_unknown_when_no_drc(tmp_path):
    proj, run = _proj(tmp_path, drc=None)
    v = sg.evaluate(proj, run)
    assert v["checks"]["antenna"]["status"] == "unknown"


def test_timing_violated_never_blocks(tmp_path):
    """Negative slack is a legitimate training label — recorded, never a block."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": -0.42}}})
    v = sg.evaluate(proj, run)
    assert v["status"] == "pass_with_caveats"
    assert "timing=violated" in v["caveats"] and "timing" not in v["blockers"]
    assert v["checks"]["timing"]["setup_wns"] == -0.42


def test_timing_from_timing_check_tier(tmp_path):
    proj, run = _proj(tmp_path, timing_check={"tier": "minor"})
    v = sg.evaluate(proj, run)
    assert v["checks"]["timing"]["status"] == "met"


def test_gate_off_records(tmp_path):
    proj, run = _proj(tmp_path, drc="fail")
    rc, verdict, _ = _cli(proj, run, mode="off")
    assert rc == 0 and verdict["status"] == "gate_off"


# ---- wiring: one shared copy in all three stage runners ------------------------

def test_gate_wired_once_into_all_three_runners():
    """Same rule as the #30 provenance guard: the gate must be the SHARED
    helper in every stage script, never a worker-local inline copy; and the
    dataset builder (run_graphs.sh) enforces while the standalone extractors
    default to warn."""
    defaults = {"run_graphs.sh": "enforce", "run_labels.sh": "warn",
                "run_features.sh": "warn"}
    for script, dflt in defaults.items():
        src = open(os.path.join(_FLOW, script), encoding="utf-8").read()
        assert "signoff_gate.py" in src, f"{script} lost the #34 gate"
        assert f'R2G_SIGNOFF_GATE:-{dflt}' in src, \
            f"{script} default gate mode drifted from {dflt}"
        assert "stage_log.jsonl" not in src, f"{script} re-inlined the gate"


def test_all_three_runners_bind_the_def():
    """FIX A (agent-logic #5, 2026-07-16): EVERY stage that gates must pass --def, or
    a sub-stage re-gating without it overwrites run_graphs.sh's binding=bound verdict
    with 'unknown' and drops the fingerprint before build_graphs.py embeds it."""
    for script in ("run_graphs.sh", "run_features.sh", "run_labels.sh"):
        src = open(os.path.join(_FLOW, script), encoding="utf-8").read()
        assert '--def "$DEF"' in src, f"{script} does not bind the DEF to the gate (#5)"


def test_run_graphs_passes_verdict_to_manifest():
    src = open(os.path.join(_FLOW, "run_graphs.sh"), encoding="utf-8").read()
    assert "--signoff-health" in src and "signoff_gate.json" in src


# ---- FIX A: DEF binding survives the extractor re-gate (agent-logic #5) ---------

def test_def_fingerprint_includes_sha256(tmp_path):
    """The def_fingerprint now carries a full sha256 content digest (binds the
    manifest to the EXACT bytes certified), keeping path/size/mtime."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    def_path = os.path.join(run, "results", "6_final.def")
    os.makedirs(os.path.dirname(def_path), exist_ok=True)
    content = b"VERSION 5.8 ;\nCOMPONENTS 0 ;\nEND COMPONENTS\n"
    with open(def_path, "wb") as f:
        f.write(content)
    v = sg.evaluate(proj, run, def_path)
    fp = v["checks"]["binding"]["def_fingerprint"]
    import hashlib
    assert fp["sha256"] == hashlib.sha256(content).hexdigest()
    assert fp["size"] == len(content)
    assert fp["path"].endswith("6_final.def")


def test_extractor_regate_retains_binding(tmp_path):
    """The overwrite sequence: run_graphs.sh gates WITH --def (binding=bound +
    fingerprint), then an extractor sub-stage re-gates and OVERWRITES
    reports/signoff_gate.json. With the FIX A --def wiring the rewritten verdict must
    STILL read binding=bound with a fingerprint, not degrade to 'unknown'."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    def_path = os.path.join(run, "results", "6_final.def")
    os.makedirs(os.path.dirname(def_path), exist_ok=True)
    open(def_path, "w").write("VERSION 5.8 ;\n")
    # run_graphs.sh: enforce + --def
    rc1, v1, _ = _cli(proj, run, mode="enforce", extra=("--def", def_path))
    assert rc1 == 0 and v1["checks"]["binding"]["status"] == "bound"
    # extractor (run_features/run_labels): warn + --def (the FIX A wiring) overwrites
    rc2, v2, _ = _cli(proj, run, mode="warn", extra=("--def", def_path))
    assert rc2 == 0
    assert v2["checks"]["binding"]["status"] == "bound"
    assert v2["checks"]["binding"]["def_fingerprint"]["sha256"]


def test_extractor_regate_without_def_would_degrade(tmp_path):
    """Documents the pre-fix defect the wiring closes: re-gating WITHOUT --def loses
    the binding (this is exactly what the extractor used to do)."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    def_path = os.path.join(run, "results", "6_final.def")
    os.makedirs(os.path.dirname(def_path), exist_ok=True)
    open(def_path, "w").write("VERSION 5.8 ;\n")
    _cli(proj, run, mode="enforce", extra=("--def", def_path))
    rc2, v2, _ = _cli(proj, run, mode="warn")   # no --def -> the old behavior
    assert v2["checks"]["binding"]["status"] == "unknown"
    assert "def_fingerprint" not in v2["checks"]["binding"]


# ---- the verifier side: fail-closed provenance ---------------------------------

def _run_group(fn, *a):
    vgd.RESULTS.clear()
    vgd.SKIPPED.clear()
    fn(*a)
    return [r["check"] for r in vgd.RESULTS if not r["ok"]]


@pytest.fixture()
def _empty_dirs(tmp_path):
    feat = tmp_path / "features"
    labs = tmp_path / "labels"
    feat.mkdir()
    labs.mkdir()
    return str(feat), str(labs)


def test_verifier_provenance_fail_closed(tmp_path, monkeypatch, _empty_dirs):
    """No drc/lvs reports AND no gate verdict in the manifest -> the dataset's
    sign-off provenance is unknown and the verifier must FAIL, not vacuously
    pass (the pre-#34 trap)."""
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    case.mkdir()
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert "signoff.provenance recorded (drc/lvs reports or manifest signoff_health)" in fails


def test_verifier_provenance_via_reports(tmp_path, monkeypatch, _empty_dirs):
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    (case / "reports").mkdir(parents=True)
    json.dump({"status": "clean"}, open(case / "reports" / "drc.json", "w"))
    json.dump({"status": "clean"}, open(case / "reports" / "lvs.json", "w"))
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert "signoff.provenance recorded (drc/lvs reports or manifest signoff_health)" not in fails


def test_verifier_lvs_skipped_accepted(tmp_path, monkeypatch, _empty_dirs):
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    (case / "reports").mkdir(parents=True)
    json.dump({"status": "clean"}, open(case / "reports" / "drc.json", "w"))
    json.dump({"status": "skipped"}, open(case / "reports" / "lvs.json", "w"))
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert "signoff.lvs clean (dataset built on a signed-off design)" not in fails


def test_verifier_gate_verdict_dirty_fails(tmp_path, monkeypatch, _empty_dirs):
    """A warn-mode build on a dirty design records signoff_health=dirty in the
    manifest — the verifier must fail that dataset."""
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    (case / "dataset").mkdir(parents=True)
    json.dump({"signoff_health": {"status": "dirty", "blockers": ["drc"],
                                  "checks": {"drc": {"status": "fail"}}}},
              open(case / "dataset" / "graph_manifest.json", "w"))
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert "signoff.gate verdict pass (manifest signoff_health)" in fails
    # provenance IS recorded (the gate ran) — only the verdict is dirty
    assert "signoff.provenance recorded (drc/lvs reports or manifest signoff_health)" not in fails


def test_verifier_gate_verdict_pass_ok(tmp_path, monkeypatch, _empty_dirs):
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    (case / "dataset").mkdir(parents=True)
    json.dump({"signoff_health": {"status": "pass_with_caveats", "blockers": [],
                                  "checks": {"drc": {"status": "clean"}}}},
              open(case / "dataset" / "graph_manifest.json", "w"))
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert not [f for f in fails if f.startswith("signoff.gate") or f.startswith("signoff.provenance")]


# ---- FIX A verifier side: a lost DEF binding is a verification failure ----------

def _manifest_with_binding(case, binding_status, *, overridden=False):
    (case / "dataset").mkdir(parents=True)
    sh = {"status": "pass_with_caveats", "blockers": [],
          "checks": {"drc": {"status": "clean"},
                     "binding": {"status": binding_status}}}
    if overridden:
        sh["def_overridden"] = True
    json.dump({"signoff_health": sh}, open(case / "dataset" / "graph_manifest.json", "w"))


def test_verifier_binding_unknown_fails_when_not_overridden(tmp_path, monkeypatch, _empty_dirs):
    """A DEF-aware gate whose binding degraded to 'unknown' (extractor overwrote the
    verdict without --def) must FAIL — the DEF binding was lost."""
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    _manifest_with_binding(case, "unknown")
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert any(f.startswith("signoff.binding") for f in fails)


def test_verifier_binding_unknown_ok_when_overridden(tmp_path, monkeypatch, _empty_dirs):
    """A deliberate R2G_DEF override legitimately records binding=unknown -> no fail."""
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    _manifest_with_binding(case, "unknown", overridden=True)
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert not any(f.startswith("signoff.binding") for f in fails)


def test_verifier_binding_bound_passes(tmp_path, monkeypatch, _empty_dirs):
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    _manifest_with_binding(case, "bound")
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert not any(f.startswith("signoff.binding") for f in fails)


def test_verifier_no_binding_key_grandfathered(tmp_path, monkeypatch, _empty_dirs):
    """Older manifests with NO binding key must still pass (pre-P0-17)."""
    feat, labs = _empty_dirs
    case = tmp_path / "case"
    (case / "dataset").mkdir(parents=True)
    json.dump({"signoff_health": {"status": "pass", "blockers": [],
                                  "checks": {"drc": {"status": "clean"}}}},
              open(case / "dataset" / "graph_manifest.json", "w"))
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda c: {})
    fails = _run_group(vgd.signoff_report_checks, str(case), "dz", feat, labs, {})
    assert not any(f.startswith("signoff.binding") for f in fails)


# ---- FIX B: the verifier fails a superseded (blocked_unsigned) manifest --------

def test_verifier_fails_blocked_unsigned_manifest(tmp_path):
    """A signoff-gate BLOCK supersedes a stale-green dataset/graph_manifest.json with
    status=blocked_unsigned (full-pipeline #6): the verifier must FAIL it fast, before
    re-deriving checks against the orphaned .pt files."""
    case = tmp_path / "case"
    (case / "dataset").mkdir(parents=True)
    json.dump({"design": "mini", "status": "blocked_unsigned",
               "reason": "signoff gate: not signed off"},
              open(case / "dataset" / "graph_manifest.json", "w"))
    vgd.RESULTS.clear()
    n_fail = vgd.verify_case(str(case))
    assert n_fail >= 1
    fails = [r["check"] for r in vgd.RESULTS if not r["ok"]]
    assert any("blocked_unsigned" in f for f in fails)


# ---- graph_skip_manifest.py: specific upstream reasons (codex #6, #38) ----------

def _skip_upstream(tmp_path, *, gate=None, antenna=None, ppa=None, stage_log=None,
                   run_dir_arg=""):
    """Build a project with the given upstream markers and return the manifest's
    upstream object (or {} if omitted)."""
    proj = tmp_path / "p"
    rep = proj / "reports"
    rep.mkdir(parents=True)
    if gate is not None:
        json.dump(gate, open(rep / "signoff_gate.json", "w"))
    if antenna is not None:
        json.dump(antenna, open(rep / "antenna_nonconverged.json", "w"))
    if ppa is not None:
        json.dump(ppa, open(rep / "ppa.json", "w"))
    if stage_log is not None:
        run = proj / "backend" / "RUN_2026-07-01_00-00-00"
        run.mkdir(parents=True)
        with open(run / "stage_log.jsonl", "w") as f:
            for rec in stage_log:
                f.write(json.dumps(rec) + "\n")
    up = gsm.collect_upstream(str(proj), run_dir_arg)
    return up


def test_skip_manifest_no_backend_is_empty(tmp_path):
    """A plain no-backend skip carries no upstream — the manifest is unchanged."""
    assert _skip_upstream(tmp_path) == {}


def test_skip_manifest_threads_antenna_nonconverged(tmp_path):
    """The suggestion's exact example: a DEF-missing/blocked skip records WHY."""
    up = _skip_upstream(tmp_path, antenna={"class": "antenna", "residual_count": 2,
                                           "strategies_tried": ["antenna_a"]})
    assert up["antenna_nonconverged"]["residual_count"] == 2


def test_skip_manifest_threads_signoff_blockers(tmp_path):
    up = _skip_upstream(tmp_path, gate={"status": "dirty", "blockers": ["drc", "lvs"],
                                        "checks": {"drc": {"status": "fail", "detail": "7 shorts"},
                                                   "lvs": {"status": "mismatch"}}})
    assert up["signoff_blockers"] == ["drc", "lvs"]
    assert up["signoff_detail"]["drc"] == "7 shorts"


def test_skip_manifest_threads_orfs_fail_stage(tmp_path):
    up = _skip_upstream(tmp_path, ppa={"orfs_status": "fail", "orfs_fail_stage": "route"})
    assert up["orfs_status"] == "fail" and up["orfs_fail_stage"] == "route"


def test_skip_manifest_scans_stage_log_when_rundir_empty(tmp_path):
    """DEF-missing path: run_dir is empty, so the newest backend RUN_* is scanned
    for the first failing stage."""
    up = _skip_upstream(tmp_path, run_dir_arg="",
                        stage_log=[{"stage": "synth", "status": 0},
                                   {"stage": "floorplan", "status": 0},
                                   {"stage": "place", "status": 2}])
    assert up["stage_log_fail_stage"] == "place"


def test_skip_manifest_cli_emits_manifest(tmp_path):
    """End-to-end: the CLI prints a skip manifest with status + reason + upstream."""
    proj = tmp_path / "p"
    (proj / "reports").mkdir(parents=True)
    json.dump({"class": "antenna", "residual_count": 1}, open(proj / "reports" / "antenna_nonconverged.json", "w"))
    r = subprocess.run([sys.executable, os.path.join(_FLOW, "graph_skip_manifest.py"),
                        "mydesign", "sky130hs", "no 6_final.def found", str(proj), ""],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    m = json.loads(r.stdout)
    assert m["status"] == "skipped" and m["design"] == "mydesign"
    assert m["reason"] == "no 6_final.def found"
    assert m["upstream"]["antenna_nonconverged"]["residual_count"] == 1
