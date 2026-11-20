"""Meaningful split-leakage and source-preservation guards for the Exp4 runner."""
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from select_cohort import PLATFORMS, family_components, select, validate
from stage_inputs import audit_inputs, stage_bundle


def pool():
    rows = []
    for pi, platform in enumerate(PLATFORMS):
        for si, cells in enumerate((500, 3000, 15000)):
            for i in range(20):
                n = 10000 + pi * 1000 + si * 100 + i
                rows.append({"design": f"C{n}__top__repo{n}__{platform}__p2", "platform": platform,
                             "cells": cells + i, "drc_status": "clean", "lvs_status": "clean", "missing": [],
                             "source_repositories": [f"R{n}__owner__repo{n}"], "source_dir": f"/existing/{n}"})
    return {"rows": rows}


def old():
    return {"candidate_ids": [], "repository_slugs": [], "designs": [], "sources": {}}


def test_quota_determinism_and_family_isolation():
    inv = pool()
    # One repo occurs in two technologies and multiple size buckets; all its
    # members must still be confined to the same split if selected.
    for idx in (0, 20, 60, 80):
        inv["rows"][idx]["source_repositories"] = ["R99999__owner__shared"]
    a = select(inv, old()); b = select(inv, old())
    assert a == b
    assert len(a["splits"]["dev"]) == 18 and len(a["splits"]["test"]) == 60
    validate(a)
    assert not {r["family"] for r in a["splits"]["dev"]} & {r["family"] for r in a["splits"]["test"]}


def test_transitive_source_family_union():
    rows = pool()["rows"][:3]
    rows[0]["source_repositories"] = ["repo-a"]
    rows[1]["source_repositories"] = ["repo-a", "repo-b"]
    rows[2]["source_repositories"] = ["repo-b"]
    groups = family_components(rows)
    assert len(set(groups.values())) == 1


def test_old_identity_excluded_across_technologies():
    inv = pool()
    first = inv["rows"][0]
    clone = dict(first, design=first["design"].replace("nangate45", "sky130hd"), platform="sky130hd")
    inv["rows"].append(clone)
    history = old(); history["candidate_ids"] = ["C10000"]
    result = select(inv, history)
    assert all(not r["design"].startswith("C10000__") for split in result["splits"].values() for r in split)


def test_no_silent_quota_or_cell_range_relaxation():
    inv = pool()
    inv["rows"] = [r for r in inv["rows"] if r["platform"] != "gf180" or r["cells"] < 10000]
    with pytest.raises(ValueError, match="Insufficient gf180/large"):
        select(inv, old())


def test_validation_rejects_tampered_family():
    result = select(pool(), old())
    result["splits"]["test"][0]["family"] = result["splits"]["dev"][0]["family"]
    with pytest.raises(AssertionError, match="Family leakage"):
        validate(result)


def test_copy_never_hardlinks_or_chmods_sources(tmp_path):
    source = tmp_path / "source.def"; source.write_text("original physical design\n"); source.chmod(0o640)
    before = source.stat()
    destination = tmp_path / "inputs"
    rec, status = stage_bundle({"6_final.def": source}, destination, {"design": "a"})
    assert status == "copied"
    assert source.stat().st_ino != (destination / "6_final.def").stat().st_ino
    assert stat.S_IMODE(source.stat().st_mode) == stat.S_IMODE(before.st_mode)
    assert stat.S_IMODE((destination / "6_final.def").stat().st_mode) == 0o444
    source.write_text("changed source\n")
    assert (destination / "6_final.def").read_text() == "original physical design\n"
    with pytest.raises(RuntimeError, match="Stale or modified"):
        stage_bundle({"6_final.def": source}, destination, {"design": "a"})


def test_existing_destination_is_not_overwritten(tmp_path):
    source = tmp_path / "source.def"; source.write_text("raw\n")
    destination = tmp_path / "inputs"; destination.mkdir(); (destination / "keep").write_text("keep")
    with pytest.raises(RuntimeError, match="without provenance"):
        stage_bundle({"6_final.def": source}, destination, {"design": "a"})
    assert (destination / "keep").read_text() == "keep"


def test_audit_rejects_converter_hidden_in_raw_inputs(tmp_path):
    source = tmp_path / "source.def"; source.write_text("raw\n")
    destination = tmp_path / "inputs"
    rec, _ = stage_bundle({"6_final.def": source}, destination, {"design": "a"})
    evidence = {"bundles": [{"destination": str(destination), **rec}]}
    assert audit_inputs(evidence)["status"] == "pass"
    (destination / "converter.py").write_text("# implementation must not enter raw inputs\n")
    with pytest.raises(RuntimeError, match="Unexpected files"):
        audit_inputs(evidence)
