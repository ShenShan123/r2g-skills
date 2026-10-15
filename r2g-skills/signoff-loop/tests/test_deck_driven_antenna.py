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
