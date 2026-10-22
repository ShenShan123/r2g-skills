"""T2a fairness: the executable knob permissions equal the published task contract (2026-10-16).

Defect: every T2a task manifest publishes an allowlist of 25 knobs (the 15 knob_policy bounds plus
10 enum knobs of the common catalogue), and the prompt shows the model that allowlist, but the LLM
executor checked proposals against knob_policy.KNOB_BOUNDS only and refused the 10 enum knobs as
``non_whitelisted``. In every NB run on riscv_core/u19 (R4 CORE, FULL, seven-arm DEV) 2 of NB's 3
repair turns were spent on such a refusal; no other arm hit it. One permission set for all arms:
the manifest (validated with the same check_config as acceptance) is what the executor enforces,
and memory edits share the same enum knobs through knob_policy.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory" / "scripts"))
sys.path.insert(0, str(ROOT / "r2g-skills" / "signoff-loop" / "knowledge"))
import knob_policy  # noqa: E402
import r2g_memory_eval as H  # noqa: E402
import t2a_task_manifests as TM  # noqa: E402

INITIAL = {"DESIGN_NAME": "riscv_core", "PLATFORM": "sky130hs", "CORE_UTILIZATION": "19",
           "PLACE_DENSITY_LB_ADDON": "0.2", "ABC_AREA": "1"}
CATALOGUE_ENUMS = ("ABC_AREA", "ABC_CLOCK_PERIOD_IN_PS", "ENABLE_PLACE_REPAIR_TIMING",
                   "MAX_REPAIR_ANTENNAS_ITER_DRT", "MAX_REPAIR_ANTENNAS_ITER_GRT", "R2G_ROUTE_CAP_MARGIN",
                   "R2G_ROUTE_SLEW_MARGIN", "SETUP_SLACK_MARGIN", "SKIP_ANTENNA_REPAIR", "SYNTH_HIERARCHICAL")


def _manifest():
    allow = TM.allowlist(INITIAL)
    return {"task": "riscv_core/u19", "initial_config": dict(INITIAL), "allowlist": allow,
            "immutable": {"knobs": sorted(set(INITIAL) - set(allow)), "files": {}}}


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints/config.mk").write_text(
        "".join("export %s = %s\n" % kv for kv in INITIAL.items()))
    return tmp_path


def _proposal(sets=None, unsets=None):
    return json.dumps({"set": sets or {}, "unset": unsets or [], "rerun_from": "place", "rationale": "x"})


def test_the_refused_nb_proposal_is_executable_under_its_published_contract(proj):
    # The exact knobs NB proposed in turns 2-3 of every riscv_core/u19 run (refused before the fix).
    rf, applied = H.apply_llm(proj, _proposal({"ENABLE_PLACE_REPAIR_TIMING": "1", "SETUP_SLACK_MARGIN": "0.2"}),
                              manifest=_manifest())
    assert rf == "place" and applied["set"] == {"ENABLE_PLACE_REPAIR_TIMING": "1", "SETUP_SLACK_MARGIN": "0.2"}
    text = (proj / "constraints/config.mk").read_text()
    assert "export ENABLE_PLACE_REPAIR_TIMING = 1" in text and "export SETUP_SLACK_MARGIN = 0.2" in text


@pytest.mark.parametrize("sets,reason", [
    ({"SETUP_SLACK_MARGIN": "0.5"}, "contract_violation"),          # enum value not published
    ({"CORE_UTILIZATION": "95"}, "contract_violation"),             # outside the published number range
    ({"PLACE_DENSITY_LB_ADDON": "0.05"}, "contract_violation"),     # hard rule kept (min 0.10)
    ({"VERILOG_FILES": "x.v"}, "non_whitelisted"),                  # RTL is never an edit
    ({"DESIGN_NAME": "other"}, "non_whitelisted"),                  # immutable / not in the contract
])
def test_the_contract_still_refuses_what_it_does_not_publish(proj, sets, reason):
    before = (proj / "constraints/config.mk").read_bytes()
    rf, why = H.apply_llm(proj, _proposal(sets), manifest=_manifest())
    assert rf is None and why.startswith(reason)
    assert (proj / "constraints/config.mk").read_bytes() == before


def test_without_a_task_manifest_the_legacy_whitelist_is_unchanged(proj):
    rf, why = H.apply_llm(proj, _proposal({"SETUP_SLACK_MARGIN": "0.2"}))
    assert rf is None and why.startswith("non_whitelisted")
    assert H.apply_llm(proj, _proposal({"CORE_UTILIZATION": "30"}))[0] == "place"


def test_executor_permissions_equal_the_published_allowlist():
    man = _manifest()
    assert set(H.executable_knobs(man)) == set(man["allowlist"]) - set(man["immutable"]["knobs"])
    assert set(CATALOGUE_ENUMS) <= set(H.executable_knobs(man))


def test_the_prompt_lists_exactly_the_executable_knobs():
    man = _manifest()
    system = H.llm_system("sky130hs", man)
    listed = system.split("Allowed knobs: ", 1)[1].split(".", 1)[0].split(", ")
    assert sorted(listed) == sorted(H.executable_knobs(man))
    assert H.llm_system("sky130hs") == H.LLM_SYSTEM.replace("{platform}", "sky130hs")


def test_memory_edits_share_the_catalogue_enum_permissions():
    for knob in CATALOGUE_ENUMS:
        values = TM.allowlist(INITIAL)[knob]["values"]
        assert knob_policy.edit_violation(knob, values[-1]) is None
    assert "not one of" in knob_policy.edit_violation("SETUP_SLACK_MARGIN", "0.5")
    assert knob_policy.edit_violation("VERILOG_FILES", "x.v") is not None
    # One table: the manifest builder publishes exactly the policy's enum values.
    for knob, values in knob_policy.KNOB_ENUMS.items():
        assert set(TM.allowlist({})[knob]["values"]) == set(values)
