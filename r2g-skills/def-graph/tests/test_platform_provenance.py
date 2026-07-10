"""Platform provenance guard (failure-patterns.md #30).

A campaign re-point (``setup_rtl_designs.py --platform X --force``) rewrites
EVERY project's constraints/config.mk — including designs whose backend +
dataset were built on the PRIOR platform. cell_type_id and every *_type_id
vocabulary are per-platform, so keying an existing dataset (build or verify)
to the re-pointed config.mk is a silent-value defect, not an error. The
authority order everywhere is:

  explicit arg  >  build provenance (manifest / backend run-meta.json)  >  config.mk

Covers: the shared shell guard ``_provenance.sh`` (used by run_labels /
run_features / run_graphs — one copy, per the techlib worker-local-patch
lesson), and the verifier's ``_platform_provenance`` (manifest > run-meta >
config.mk). The manifest "platform" stamp itself is asserted in
test_corner_case_pipeline.py (the corner fixture passes --platform).
"""
import importlib.util
import json
import os
import subprocess
import sys

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_PROV = os.path.join(_FLOW, "_provenance.sh")
_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "tools")
_spec = importlib.util.spec_from_file_location(
    "verify_graph_dataset", os.path.join(_TOOLS, "verify_graph_dataset.py"))
vgd = importlib.util.module_from_spec(_spec)
sys.modules["verify_graph_dataset"] = vgd
_spec.loader.exec_module(vgd)


def _sh(run_dir, platform):
    r = subprocess.run(["bash", _PROV, run_dir, platform],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip(), r.stderr


def _case(tmp_path, *, cfg=None, meta=None, manifest=None):
    """Build a minimal case dir: config.mk / backend/RUN_1/run-meta.json /
    dataset/graph_manifest.json, each optional."""
    case = tmp_path / "case"
    if cfg is not None:
        (case / "constraints").mkdir(parents=True, exist_ok=True)
        (case / "constraints" / "config.mk").write_text(
            f"export PLATFORM    = {cfg}\n", encoding="utf-8")
    if meta is not None:
        rd = case / "backend" / "RUN_2026-07-01_00-00-00"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "run-meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if manifest is not None:
        (case / "dataset").mkdir(parents=True, exist_ok=True)
        (case / "dataset" / "graph_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
    case.mkdir(exist_ok=True)
    return str(case)


# ---- the shared shell guard (_provenance.sh) --------------------------------

def test_sh_meta_overrides_repointed_config(tmp_path):
    rd = tmp_path / "RUN_1"
    rd.mkdir()
    (rd / "run-meta.json").write_text(json.dumps(
        {"design_name": "iir", "platform": "sky130hd"}), encoding="utf-8")
    out, err = _sh(str(rd), "sky130hs")
    assert out == "sky130hd"
    assert "NOTE" in err and "sky130hd" in err


def test_sh_agreement_is_silent(tmp_path):
    rd = tmp_path / "RUN_1"
    rd.mkdir()
    (rd / "run-meta.json").write_text(json.dumps(
        {"platform": "sky130hs"}), encoding="utf-8")
    out, err = _sh(str(rd), "sky130hs")
    assert out == "sky130hs"
    assert err == ""


def test_sh_no_meta_falls_back_to_config(tmp_path):
    rd = tmp_path / "RUN_1"
    rd.mkdir()
    out, err = _sh(str(rd), "sky130hs")
    assert out == "sky130hs" and err == ""
    out, err = _sh("", "gf180")          # no run dir discovered at all
    assert out == "gf180" and err == ""


def test_sh_malformed_meta_falls_back_to_config(tmp_path):
    rd = tmp_path / "RUN_1"
    rd.mkdir()
    (rd / "run-meta.json").write_text("{not json", encoding="utf-8")
    out, _ = _sh(str(rd), "sky130hs")
    assert out == "sky130hs"


def test_guard_wired_once_into_all_three_stage_scripts():
    """The guard must be the SHARED helper in every stage script — a worker-local
    inline copy is the exact drift mode the techlib lesson forbids."""
    for script in ("run_labels.sh", "run_features.sh", "run_graphs.sh"):
        src = open(os.path.join(_FLOW, script), encoding="utf-8").read()
        assert "_provenance.sh" in src, f"{script} lost the #30 guard"
        assert 'run-meta.json"' not in src.replace(
            "_provenance.sh", ""), f"{script} re-inlined the guard"


# ---- the verifier (_platform_provenance) ------------------------------------

def test_vgd_manifest_wins(tmp_path):
    case = _case(tmp_path, cfg="sky130hs",
                 meta={"platform": "sky130hd"},
                 manifest={"platform": "nangate45"})
    assert vgd._platform_provenance(case) == "nangate45"


def test_vgd_runmeta_beats_repointed_config(tmp_path):
    # The live 2026-07-09 shape: dataset built on sky130hd, config.mk re-pointed
    # to sky130hs by the new round's bootstrap; manifest predates the stamp.
    case = _case(tmp_path, cfg="sky130hs",
                 meta={"platform": "sky130hd"},
                 manifest={"design": "iir"})          # no platform key (pre-#30 .pt)
    assert vgd._platform_provenance(case) == "sky130hd"


def test_vgd_newest_run_meta_wins(tmp_path):
    case = _case(tmp_path, cfg="sky130hs", meta={"platform": "sky130hd"})
    rd = tmp_path / "case" / "backend" / "RUN_2026-07-09_00-00-00"
    rd.mkdir(parents=True)
    (rd / "run-meta.json").write_text(json.dumps({"platform": "sky130hs"}),
                                      encoding="utf-8")
    assert vgd._platform_provenance(case) == "sky130hs"


def test_vgd_config_only_and_empty(tmp_path):
    assert vgd._platform_provenance(_case(tmp_path, cfg="sky130hs")) == "sky130hs"
    assert vgd._platform_provenance(str(tmp_path / "nothing")) == ""
