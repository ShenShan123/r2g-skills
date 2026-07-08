"""Integration test: suggest_config.py prefers learned heuristics when present."""
from __future__ import annotations

import json
from pathlib import Path

import suggest_config


def _make_fake_project(tmp_path: Path) -> Path:
    project = tmp_path / "aes_run"
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "synth").mkdir()
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = aes128_core\n"
        "export PLATFORM = nangate45\n"
        "export VERILOG_FILES = /tmp/fake.v\n"
        "export SDC_FILE = /tmp/fake.sdc\n"
    )
    (project / "rtl" / "aes128_core.v").write_text(
        "module aes128_core(input clk); endmodule\n"
    )
    (project / "synth" / "synth.log").write_text("Number of cells: 12412\n")
    return project


def test_suggest_config_uses_learned_heuristics(tmp_path, tmp_knowledge_dir,
                                                monkeypatch):
    project = _make_fake_project(tmp_path)

    # Pre-populate heuristics.json. Use CU median=22 which is BELOW the
    # hard-coded crypto clamp of min(cu, 25) — that way we exercise the
    # "learned value wins" path cleanly, without colliding with the
    # design-type safety rail. A separate test below covers the clamp.
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10,
                        "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 24, "median": 22},
                        "place_density_lb_addon": {"min_safe": 0.15,
                                                    "max_safe": 0.25,
                                                    "median": 0.22},
                    },
                },
            },
        },
    }))

    # Only the explicit `heuristics_path=HEURISTICS_PATH` argument matters
    # because suggest_config.recommend passes it explicitly; no need to
    # monkeypatch query_knowledge.DEFAULT_HEURISTICS_PATH.
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    result = suggest_config.recommend(project)

    assert result["design_name"] == "aes128_core"
    # Path documentation — exercises the crypto code path intentionally.
    assert result["design_type"] == "crypto"
    assert result["size_class"] == "medium"
    # Learned median 22 survives the crypto clamp (min(22, 25) == 22).
    assert result["recommendations"]["CORE_UTILIZATION"] == 22
    assert abs(result["recommendations"]["PLACE_DENSITY_LB_ADDON"] - 0.22) < 1e-9
    assert any("learned" in e.lower() for e in result["explanations"])
    assert result.get("learned_source") == "aes_xcrypt/nangate45"


def test_design_type_clamp_still_fires_over_learned_value(
    tmp_path, tmp_knowledge_dir, monkeypatch,
):
    """Safety rail test: a too-aggressive learned CU is clamped by crypto rule."""
    project = _make_fake_project(tmp_path)
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 3,
                        "success_count": 3,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 30, "max_safe": 40, "median": 35},
                    },
                },
            },
        },
    }))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    result = suggest_config.recommend(project)
    # Crypto safety clamp min(35, 25) == 25, NOT the learned 35.
    assert result["recommendations"]["CORE_UTILIZATION"] == 25
    assert result.get("learned_source") == "aes_xcrypt/nangate45"


def test_suggest_config_falls_back_without_heuristics(tmp_path, tmp_knowledge_dir,
                                                      monkeypatch):
    project = _make_fake_project(tmp_path)
    # Non-existent heuristics file
    heur_path = tmp_knowledge_dir / "heuristics.json"
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    result = suggest_config.recommend(project)
    # Document the path: crypto/medium means base 25, crypto clamp min(25, 25) == 25.
    assert result["design_type"] == "crypto"
    assert result["size_class"] == "medium"
    assert result["recommendations"]["CORE_UTILIZATION"] == 25
    assert result.get("learned_source") is None


def test_learned_median_float_rounded_to_int(tmp_path, tmp_knowledge_dir, monkeypatch):
    """A learned CU median of 22.5 should be rounded to 23 (int), not leak a float."""
    project = _make_fake_project(tmp_path)
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 4, "success_count": 4, "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 25, "median": 22.5},
                    },
                },
            },
        },
    }))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    result = suggest_config.recommend(project)
    cu = result["recommendations"]["CORE_UTILIZATION"]
    # Python's round() uses banker's rounding, so round(22.5) == 22.
    # The key invariant is that the result is an int, not a float.
    assert cu == 22
    assert isinstance(cu, int)


def test_learned_density_never_violates_010_floor(tmp_path, tmp_knowledge_dir,
                                                   monkeypatch):
    """A learned PLACE_DENSITY_LB_ADDON median below 0.10 must be clamped up.

    CLAUDE.md hard rule: never recommend PLACE_DENSITY_LB_ADDON below 0.10 —
    placer divergence is irrecoverable. This locks in the post-filter floor.
    """
    project = _make_fake_project(tmp_path)
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 5, "success_count": 5, "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 24, "median": 22},
                        "place_density_lb_addon": {"min_safe": 0.05,
                                                   "max_safe": 0.08,
                                                   "median": 0.05},
                    },
                },
            },
        },
    }))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    result = suggest_config.recommend(project)
    assert result["recommendations"]["PLACE_DENSITY_LB_ADDON"] >= 0.10


def _write_aes_heuristics(tmp_knowledge_dir: Path) -> Path:
    """Pre-populate a learned heuristic that survives the crypto clamp
    (CU median 22 < 25) so the learned-vs-naive difference is observable."""
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10,
                        "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 24, "median": 22},
                        "place_density_lb_addon": {"min_safe": 0.15,
                                                   "max_safe": 0.25,
                                                   "median": 0.22},
                    },
                },
            },
        },
    }))
    return heur_path


def test_no_learned_arm_returns_naive_baseline(tmp_path, tmp_knowledge_dir,
                                               monkeypatch):
    """use_learned=False yields the params_by_size baseline (no learned_source),
    while use_learned=True applies the learned median — and BOTH still obey the
    crypto design-type clamp and the 0.10 floor. The ONLY difference is config
    provenance.
    """
    project = _make_fake_project(tmp_path)
    heur_path = _write_aes_heuristics(tmp_knowledge_dir)
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH",
                        tmp_knowledge_dir / "families.json")

    learned = suggest_config.recommend(project, use_learned=True)
    naive = suggest_config.recommend(project, use_learned=False)

    # Learned arm: median 22 survives the crypto clamp min(22, 25)==22.
    assert learned["learned_source"] == "aes_xcrypt/nangate45"
    assert learned["recommendations"]["CORE_UTILIZATION"] == 22

    # Naive arm: no learned override; params_by_size medium=25, crypto clamp
    # min(25, 25)==25. learned_source must be None.
    assert naive["learned_source"] is None
    assert naive["recommendations"]["CORE_UTILIZATION"] == 25

    # Everything else identical — same size classification + design type.
    assert learned["size_class"] == naive["size_class"] == "medium"
    assert learned["design_type"] == naive["design_type"] == "crypto"

    # Both obey the 0.10 floor.
    assert learned["recommendations"]["PLACE_DENSITY_LB_ADDON"] >= 0.10
    assert naive["recommendations"]["PLACE_DENSITY_LB_ADDON"] >= 0.10


def test_no_learned_arm_still_clamps_and_floors(tmp_path, tmp_knowledge_dir,
                                                monkeypatch):
    """Even on the naive arm, an aggressive baseline is bounded by the crypto
    clamp and the 0.10 density floor — clamps/floor are arm-independent."""
    project = _make_fake_project(tmp_path)
    # A learned heuristic that WOULD push CU to 35 — but the naive arm ignores
    # it entirely, so we land on params_by_size medium=25 then crypto clamp 25.
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 3, "success_count": 3, "success_rate": 1.0,
                        "core_utilization": {"min_safe": 30, "max_safe": 40,
                                             "median": 35},
                        "place_density_lb_addon": {"min_safe": 0.05,
                                                   "max_safe": 0.08,
                                                   "median": 0.05},
                    },
                },
            },
        },
    }))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH",
                        tmp_knowledge_dir / "families.json")

    naive = suggest_config.recommend(project, use_learned=False)
    assert naive["learned_source"] is None
    # Crypto clamp still fires on the naive baseline (25), not the learned 35.
    assert naive["recommendations"]["CORE_UTILIZATION"] == 25
    # 0.10 floor still applies even though learned density (0.05) was ignored.
    assert naive["recommendations"]["PLACE_DENSITY_LB_ADDON"] >= 0.10


def test_malformed_heuristics_falls_back_silently(tmp_path, tmp_knowledge_dir, monkeypatch):
    """Shape-broken heuristics.json must not crash recommend() — fall back instead."""
    project = _make_fake_project(tmp_path)
    heur_path = tmp_knowledge_dir / "heuristics.json"
    # Root is a list instead of a dict — breaks data.get("families")
    heur_path.write_text(json.dumps(["not", "a", "dict"]))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)
    monkeypatch.setattr(suggest_config, "FAMILIES_PATH", tmp_knowledge_dir / "families.json")

    # Must not raise
    result = suggest_config.recommend(project)
    # Falls through to hard-coded crypto/medium default
    assert result["recommendations"]["CORE_UTILIZATION"] == 25
    assert result.get("learned_source") is None
