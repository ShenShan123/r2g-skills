"""deck_driven_antenna_diode: the strategy that repairs what OpenROAD cannot see.

On nangate45 the router and the signoff deck do not compute the same quantity.
For one net on metal4 the FreePDK45 deck reads 306.47 (metal area over ONE
minimum gate, agate 0.02625um^2) while `check_antennas` reads 1.49 (metal over
the whole driven cell, split per layer). On that layout `check_antennas -verbose`
reports 0 net and 0 pin violations while the deck reports 16: OpenROAD's
violating set is EMPTY, so antenna_diode_repair is a structural no-op there and
no threshold or margin reaches those nets. These tests pin the three properties
that make the replacement safe rather than merely effective.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "scripts", "reports"))
import diagnose_signoff_fix as d


CFG = {"CORE_UTILIZATION": "10", "PLATFORM": "nangate45"}


def _antenna_drc():
    return {"status": "fail", "total_violations": 7,
            "categories": {"METAL5_ANTENNA": {"count": 7}}}


def test_ranked_ahead_of_the_measured_no_op(monkeypatch):
    monkeypatch.setattr(d, "_antenna_precondition", lambda p: (True, "fixture"))
    plan = d.build_plan(_antenna_drc(), {}, CFG, check="drc")
    assert [s["id"] for s in plan["strategies"]][0] == "deck_driven_antenna_diode"


def test_unusable_antenna_model_blocks_it_too(monkeypatch):
    """It inserts the same ANTENNA_X1 the probe reports unusable, so the
    GRT-0246 gate must drop it as well. The old gate matched on the id prefix
    `antenna_diode`, which this id does not start with -- a strategy slipping
    past a safety check because of how it is spelled."""
    monkeypatch.setattr(d, "_antenna_precondition", lambda p: (False, "no diode"))
    plan = d.build_plan(_antenna_drc(), {}, CFG, check="drc")
    assert "deck_driven_antenna_diode" not in [s["id"] for s in plan["strategies"]]
    assert "antenna_diode_repair" not in [s["id"] for s in plan["strategies"]]


def test_inserts_diodes_is_membership_not_prefix():
    assert d._inserts_diodes({"id": "deck_driven_antenna_diode"})
    assert d._inserts_diodes({"id": "antenna_diode_repair"})
    assert not d._inserts_diodes({"id": "antenna_density_relief"})


def test_inputs_absent_means_not_materializable(tmp_path):
    assert d._deck_antenna_inputs_present(tmp_path) is False
    (tmp_path / "drc").mkdir()
    (tmp_path / "drc" / "6_drc.lyrdb").write_text("<report/>")
    assert d._deck_antenna_inputs_present(tmp_path) is False, \
        "a lyrdb alone cannot attribute a polygon to a net"
    r = tmp_path / "backend" / "RUN_x" / "results"
    r.mkdir(parents=True)
    (r / "6_final.def").write_text("DESIGN t ;\n")
    assert d._deck_antenna_inputs_present(tmp_path) is True


def test_materialize_refuses_without_a_verdict_to_read(tmp_path):
    """Fail CLOSED: nothing written when the deck's verdict cannot be mapped, so
    a zero-effect apply is never recorded as evidence against the recipe."""
    ok, why = d._materialize_deck_antenna_diodes(tmp_path)
    assert ok is False and "lyrdb" in why
    assert not (tmp_path / "signoff_fix").exists()


def test_the_hook_never_touches_the_stage_artifact():
    """ORFS sources the hook AFTER load_design and writes the stage ODB itself.
    A read_db or write_db here would either reload over the open design or write
    a stage artifact out of band -- what the stage-digest guard exists to catch."""
    here = os.path.join(os.path.dirname(__file__), "..",
                        "scripts", "flow", "orfs_hooks",
                        "deck_antenna_diodes.tcl")
    body = "\n".join(l for l in open(here) if not l.lstrip().startswith("#"))
    assert "read_db" not in body and "write_db" not in body


def test_the_hook_reads_its_net_list_from_beside_itself():
    """Not from the environment: ORFS erases non-stage variables before the hook
    runs, so a variable that survives today is not a contract."""
    here = os.path.join(os.path.dirname(__file__), "..",
                        "scripts", "flow", "orfs_hooks",
                        "deck_antenna_diodes.tcl")
    body = open(here).read()
    assert "info script" in body and "antenna_nets.txt" in body


# --- rung r2: the net list ACCUMULATES across applications ---------------------

def _deck_project(tmp_path, nets, monkeypatch):
    """A project whose (stubbed) mapper reports `nets` for the latest verdict."""
    (tmp_path / "drc").mkdir(exist_ok=True)
    (tmp_path / "drc" / "6_drc.lyrdb").write_text("<report/>")
    r = tmp_path / "backend" / "RUN_x" / "results"
    r.mkdir(parents=True, exist_ok=True)
    (r / "6_final.def").write_text("DESIGN t ;\n")

    class _R:
        returncode = 0

    def fake_run(cmd, **kw):
        out = cmd[cmd.index("--json-out") + 1]
        with open(out, "w") as f:
            json.dump({"nets": nets}, f)
        return _R()
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    return tmp_path


def test_second_application_keeps_the_first_rounds_nets(tmp_path, monkeypatch):
    """A residual-only second round dropped the first round's diodes and went
    from 4 violations back to 203 on the repair arm; the union cleared it."""
    p = _deck_project(tmp_path, ["n1", "n2", "n3"], monkeypatch)
    ok, info = d._materialize_deck_antenna_diodes(p)
    assert ok and "nets=3" in info
    _deck_project(tmp_path, ["n3", "n4"], monkeypatch)          # residual verdict
    ok, info = d._materialize_deck_antenna_diodes(p)
    assert ok and "accumulated: 3 prior + 1 new" in info
    nets = (p / "signoff_fix" / "antenna_nets.txt").read_text().split()
    assert nets == ["n1", "n2", "n3", "n4"]


def test_a_round_with_no_new_net_is_refused(tmp_path, monkeypatch):
    """Re-running the same list cannot change the layout -- refuse, so the
    ladder moves on to the next rung instead of spending an iteration."""
    p = _deck_project(tmp_path, ["n1", "n2"], monkeypatch)
    assert d._materialize_deck_antenna_diodes(p)[0]
    _deck_project(tmp_path, ["n2"], monkeypatch)
    ok, why = d._materialize_deck_antenna_diodes(p)
    assert not ok and why == "deck_flags_no_net_beyond_the_accumulated_list"
    assert (p / "signoff_fix" / "antenna_nets.txt").read_text().split() == ["n1", "n2"]


# --- rung r3: the ROUTER's tech LEF at ratio 10, the grader untouched ----------

def _fake_platform(tmp_path, lef_text, monkeypatch):
    plat = tmp_path / "flow" / "platforms" / "nangate45"
    (plat / "lef").mkdir(parents=True)
    (plat / "config.mk").write_text(
        "export TECH_LEF = $(PLATFORM_DIR)/lef/NangateOpenCellLibrary.tech.lef\n")
    (plat / "lef" / "NangateOpenCellLibrary.tech.lef").write_text(lef_text)
    monkeypatch.setattr(d, "_find_flow_dir", lambda: str(tmp_path / "flow"))
    return plat


def test_ratio_lef_rewrites_every_layer_rule(tmp_path, monkeypatch):
    lef = "".join("LAYER metal%d\n  ANTENNAAREARATIO 300 ;\nEND metal%d\n" % (i, i)
                  for i in range(1, 11))
    plat = _fake_platform(tmp_path, lef, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    ok, info = d._materialize_antenna_ratio_lef(proj, CFG)
    assert ok and "10 layer rules" in info
    out = (proj / "signoff_fix" / "antenna_ratio10.tech.lef").read_text()
    assert out.count("ANTENNAAREARATIO 10 ;") == 10 and "300" not in out
    # the platform's own LEF -- and with it anything the grader could read -- is untouched
    assert (plat / "lef" / "NangateOpenCellLibrary.tech.lef").read_text() == lef


def test_ratio_lef_refuses_a_lef_with_nothing_to_tighten(tmp_path, monkeypatch):
    """The stock nangate45 tech LEF ships no ANTENNAAREARATIO at all; a copy of
    it would be a no-op recorded as evidence against the recipe."""
    _fake_platform(tmp_path, "LAYER metal1\nEND metal1\n", monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    ok, why = d._materialize_antenna_ratio_lef(proj, CFG)
    assert not ok and why == "tech_lef_has_no_antenna_area_ratio"
    assert not (proj / "signoff_fix").exists()


def test_unusable_diode_blocks_the_ratio_rung(monkeypatch):
    monkeypatch.setattr(d, "_antenna_precondition", lambda p: (False, "no diode"))
    plan = d.build_plan(_antenna_drc(), {}, CFG, check="drc")
    assert "antenna_ratio_overconstrain" not in [s["id"] for s in plan["strategies"]]



def test_accumulate_rung_waits_for_a_first_round(tmp_path, monkeypatch):
    """r2 is offered only after r1 wrote its net list: the driver never re-selects
    a tried strategy, so without its own id r2 could not run at all -- the ladder
    stopped after r1 on C03155 (622 -> 38) with 'no_auto_strategy'."""
    monkeypatch.setattr(d, "_antenna_precondition", lambda p: (True, "fixture"))
    monkeypatch.setattr(d, "_antenna_ratio_inputs_present", lambda cfg: True)
    proj = tmp_path / "proj"
    (proj / "drc").mkdir(parents=True)
    (proj / "drc" / "6_drc.lyrdb").write_text("<report/>")
    r = proj / "backend" / "RUN_x" / "results"
    r.mkdir(parents=True)
    (r / "6_final.def").write_text("DESIGN t ;\n")
    plan = d.build_plan(_antenna_drc(), {}, CFG, check="drc")
    d._drop_unmaterializable(plan, proj, CFG)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[:2] == ["deck_driven_antenna_diode", "antenna_ratio_overconstrain"]
    assert plan["unmaterializable"] == ["deck_driven_antenna_diode_accumulate"]
    (proj / "signoff_fix").mkdir()
    (proj / "signoff_fix" / "antenna_nets.txt").write_text("n1\n")
    plan = d.build_plan(_antenna_drc(), {}, CFG, check="drc")
    d._drop_unmaterializable(plan, proj, CFG)
    assert [s["id"] for s in plan["strategies"]][:3] == [
        "deck_driven_antenna_diode", "deck_driven_antenna_diode_accumulate",
        "antenna_ratio_overconstrain"]
