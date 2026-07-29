"""Semantic RTL readiness gate (RMD-HO-P0-01, held-out V3 P0-HO-01).

The `secworks_sha3` repository states in its own README `Not completed. Does not
work. Do. Not. Use.` The Agent synthesized and promoted it anyway — despite
substantial undriven internal-wire evidence — and burned physical-design
resources until detailed routing reported 8,726 violations. Routing blocked
publication that time, but physical cleanliness is not a functional-correctness
proof: a structurally incomplete design that DID route clean would have entered
the graph corpus as training data.

The gate's whole difficulty is NOT rejecting healthy designs. README keywords
appear in historical notes and third-party attributions; a few undriven wires are
routine in real cores; an undriven top-level INPUT is normal by definition. So
neither signal rejects alone — the acceptance tests below pin exactly that.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from common.rtl_readiness import (  # noqa: E402
    MANUAL_REVIEW,
    READY,
    REJECTED,
    assess,
    blocks_promotion,
    repository_health,
    structural_integrity,
)

SHA3_README = """# SHA-3

## Status
Not completed. Does not work. Do. Not. Use.

## Introduction
A hardware implementation of the SHA-3 hash function.
"""

HEALTHY_README = """# uart-core

A small UART. The legacy `uart_old` module is deprecated and unsupported;
see docs/history.md for why it does not work with the new bus wrapper.
"""

UNDRIVEN_LOG = "\n".join(
    f"Warning: Wire \\sha3_core.\\w{i} is used but has no driver." for i in range(12))
CLEAN_LOG = ("Executing HIERARCHY pass.\nExecuting PROC pass.\n"
             "Warning: Wire \\top.\\unused_status is used but has no driver.\n"
             "End of script.\n")


def _repo(tmp_path: Path, readme: str | None) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    if readme is not None:
        (d / "README.md").write_text(readme, encoding="utf-8")
    return d


# --- repository health -------------------------------------------------------

def test_a_strong_negative_status_is_detected_with_provenance(tmp_path):
    h = repository_health(_repo(tmp_path, SHA3_README))
    assert h["strong_negative"] is True
    assert h["line"] == 4
    assert "Not completed" in h["match"]
    assert h["digest"] and h["file"].endswith("README.md")


def test_historical_notes_do_not_trip_the_status_check(tmp_path):
    h = repository_health(_repo(tmp_path, HEALTHY_README))
    assert h["checked"] is True
    assert h["strong_negative"] is False


def test_a_missing_readme_is_not_a_negative_signal(tmp_path):
    h = repository_health(_repo(tmp_path, None))
    assert h["checked"] is False and h["strong_negative"] is False


# --- structural integrity ----------------------------------------------------

def test_material_undriven_internal_logic_is_detected():
    s = structural_integrity(UNDRIVEN_LOG)
    assert s["undriven_count"] == 12
    assert s["material"] is True


def test_a_handful_of_undriven_wires_is_not_material():
    s = structural_integrity(CLEAN_LOG)
    assert s["undriven_count"] == 1
    assert s["material"] is False


def test_a_declared_dependency_blackbox_is_not_a_defect():
    log = "Warning: module `\\fakeram45_64x32' declared as blackbox.\n"
    assert structural_integrity(log, {"fakeram45_64x32"})["material"] is False
    assert structural_integrity(log, set())["material"] is True


def test_no_log_means_unchecked_not_material():
    s = structural_integrity(None)
    assert s["checked"] is False and s["material"] is False


# --- the combined verdict (the acceptance conditions) ------------------------

def test_the_sha3_fixture_does_not_enter_normal_promotion(tmp_path):
    a = assess(repo_dir=_repo(tmp_path, SHA3_README), synth_log=UNDRIVEN_LOG)
    assert a["rtl_readiness"] == REJECTED
    assert blocks_promotion(a)


def test_a_healthy_repo_with_historical_keywords_is_not_rejected(tmp_path):
    a = assess(repo_dir=_repo(tmp_path, HEALTHY_README), synth_log=CLEAN_LOG)
    assert a["rtl_readiness"] == READY
    assert not blocks_promotion(a)


def test_a_bad_readme_alone_is_manual_review_not_rejection(tmp_path):
    """A textual claim is one input, never a verdict on its own."""
    a = assess(repo_dir=_repo(tmp_path, SHA3_README), synth_log=CLEAN_LOG)
    assert a["rtl_readiness"] == MANUAL_REVIEW
    assert blocks_promotion(a)


def test_structural_warnings_alone_are_manual_review(tmp_path):
    a = assess(repo_dir=_repo(tmp_path, HEALTHY_README), synth_log=UNDRIVEN_LOG)
    assert a["rtl_readiness"] == MANUAL_REVIEW


def test_an_undriven_top_level_input_alone_stays_ready(tmp_path):
    """A design with an undriven top-level INPUT but no undriven internal logic
    is valid — that is what an input port IS."""
    log = "Warning: Wire \\top.\\i_unused_pin is used but has no driver.\n"
    a = assess(repo_dir=_repo(tmp_path, HEALTHY_README), synth_log=log)
    assert a["rtl_readiness"] == READY


def test_absent_functional_evidence_is_reported_not_fabricated(tmp_path):
    a = assess(repo_dir=_repo(tmp_path, HEALTHY_README), synth_log=CLEAN_LOG)
    assert a["functional_evidence"]["status"] == "not_available"


def test_available_testbench_is_recorded_as_not_run(tmp_path):
    repo = _repo(tmp_path, HEALTHY_README)
    (repo / "uart_tb.v").write_text("module uart_tb; endmodule\n", encoding="utf-8")
    a = assess(repo_dir=repo, synth_log=CLEAN_LOG)
    assert a["functional_evidence"]["status"] == "available_not_run"


def test_evidence_is_carried_for_reproducibility(tmp_path):
    a = assess(repo_dir=_repo(tmp_path, SHA3_README), synth_log=UNDRIVEN_LOG)
    assert a["repository_health"]["digest"]
    assert a["structural_integrity"]["undriven_wires"]
    assert a["reason"]
