"""Bounded deterministic S2 proposal pools and baseline isolation."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tehm.evaluation import research_s2_proposal as subject


def _cold() -> dict:
    return subject._candidate(
        "cold_start", {"CORE_UTILIZATION": "25"},
        "sha256:" + "a" * 64)


def _advisor(source: str, utilization: str) -> dict:
    return subject._candidate(
        source, {"CORE_UTILIZATION": utilization},
        "sha256:" + "b" * 64)


def test_all_policies_keep_a_real_cold_start_candidate():
    cold = _cold()
    assert subject._pool(cold, None, budget=3)["ordered_candidates"] == [cold]
    pool = subject._pool(cold, _advisor("tehm", "40"), budget=3)
    assert [row["source"] for row in pool["ordered_candidates"]] == [
        "tehm", "cold_start"]
    assert pool["candidate_limit"] == 3


def test_budget_one_cannot_enter_memory_advisor():
    pool = subject._pool(_cold(), _advisor("tehm", "40"), budget=1)
    assert [row["source"] for row in pool["ordered_candidates"]] == ["cold_start"]
    assert pool["advisor_considered"] is False


def test_duplicate_legacy_action_is_not_double_counted():
    pool = subject._pool(_cold(), _advisor("legacy_memory", "25"), budget=3)
    assert [row["source"] for row in pool["ordered_candidates"]] == ["cold_start"]
    assert pool["advisor_considered"] is True
    assert pool["advisor_duplicate_of_cold"] is True


@pytest.mark.parametrize("budget", [0, 4, True])
def test_invalid_candidate_budget_fails_closed(budget):
    with pytest.raises(subject.ResearchS2ProposalError):
        subject._pool(_cold(), None, budget=budget)


def test_advisor_source_and_cold_start_cannot_be_mislabeled():
    with pytest.raises(subject.ResearchS2ProposalError):
        subject._pool(_advisor("tehm", "40"), None, budget=3)
    with pytest.raises(subject.ResearchS2ProposalError):
        subject._pool(_cold(), _advisor("cold_start", "40"), budget=3)


def test_no_memory_recommender_runs_without_learned_override(monkeypatch, tmp_path):
    seen = {}
    report = {"recommendations": {"CORE_UTILIZATION": 25,
                                  "PLACE_DENSITY_LB_ADDON": 0.2,
                                  "ABC_AREA": 1},
              "memory_backend": "none", "learned_source": None,
              "memory_proposal": None, "size_class": "unknown",
              "design_type": "logic", "cell_count": 0}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    result = subject._recommend(tmp_path, "none",
                                tmp_path / "suggest_config.py",
                                tmp_path / "copy.sqlite")
    assert seen["command"][-2:] == ["none", str(tmp_path / "copy.sqlite")]
    assert seen["env"]["R2G_MEMORY_BACKEND"] == "none"
    assert result["config_edits"]["CORE_UTILIZATION"] == "25"
    assert result["learned_source"] is None


def test_no_memory_learned_source_is_rejected(monkeypatch, tmp_path):
    report = {"recommendations": {"CORE_UTILIZATION": 25},
              "memory_backend": "none", "learned_source": "leak",
              "memory_proposal": None}
    monkeypatch.setattr(
        subject.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(report), stderr=""))
    with pytest.raises(subject.ResearchS2ProposalError,
                       match="accessed memory"):
        subject._recommend(tmp_path, "none",
                           tmp_path / "suggest_config.py",
                           tmp_path / "copy.sqlite")


def test_legacy_uncheckpointed_wal_is_rejected(monkeypatch, tmp_path):
    knowledge = tmp_path / "r2g-skills/signoff-loop/knowledge"
    knowledge.mkdir(parents=True)
    files = {"heuristics": knowledge / "heuristics.json",
             "schema": knowledge / "schema.sql",
             "database": knowledge / "knowledge.sqlite"}
    for path in files.values():
        path.write_text(path.name, encoding="utf-8")
    hashes = {name: subject._file_sha(path) for name, path in files.items()}
    manifest = {"heuristics_digest": hashes["heuristics"],
                "schema_digest": hashes["schema"],
                "knowledge_db_fingerprint": {"db_sha256": hashes["database"]}}
    import hashlib
    manifest["manifest_digest"] = hashlib.sha256(
        json.dumps(manifest, indent=2, sort_keys=True).encode()).hexdigest()
    path = tmp_path / "baseline_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(subject, "_repo", lambda: tmp_path)
    assert subject._legacy_files(path) == files
    (knowledge / "knowledge.sqlite-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(subject.ResearchS2ProposalError,
                       match="uncheckpointed"):
        subject._legacy_files(path)
