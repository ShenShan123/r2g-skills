"""The four-stage visibility contract binds on stage-input CONTENT, not paths.

E12 class C8b: a later stage's DEF bytes written to an earlier stage's own
path, with the config key, the raw-manifest path, its semantics token and even
its sha256 all still claiming the earlier stage. The path comparison in
``validate_four_stage.py`` passes that dataset by construction. These tests pin
the fix: the digest of the bytes stage 02 actually parsed must equal the DEF
re-derived from the ``.odb`` digest the FLOW recorded when it produced the
stage -- a reference that lives outside the dataset tree the substitution
rewrites. If the binding is removed or weakened, the C8b test below fails.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
R2G2_DIR = SKILL_ROOT / "scripts" / "r2g2"
ADAPT_DIR = SKILL_ROOT / "scripts" / "stage_dataset"

FLOW_ODBS = {
    "floorplan": "2_floorplan.odb",
    "place": "3_place.odb",
    "cts": "4_cts.odb",
    "route": "5_route.odb",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator():
    pytest.importorskip("torch")
    return _load(R2G2_DIR / "checks" / "validate_four_stage.py", "t_validate_four_stage")


@pytest.fixture(scope="module")
def stage02():
    pytest.importorskip("torch")
    return _load(R2G2_DIR / "02_extract_features.py", "t_r2g2_features_digest")


@pytest.fixture(scope="module")
def config_mod():
    return _load(ADAPT_DIR / "make_sample_config.py", "t_make_sample_config_digest")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_export(odb: Path, out_def: Path) -> None:
    # Deterministic stand-in for odb_to_def.py (read_db + write_def): a DEF is a
    # pure function of the .odb bytes, which is the property the check relies on.
    out_def.write_bytes(b"DEF exported from " + odb.read_bytes())


@pytest.fixture
def sample(tmp_path: Path, validator, monkeypatch) -> dict[str, Any]:
    """A flow run with its stage manifest, and a four-stage sample exported from it."""
    monkeypatch.setattr(validator, "export_def", _fake_export)
    run_dir = tmp_path / "backend" / "RUN_2026-01-01_00-00-00_000000_abcd"
    (run_dir / "results").mkdir(parents=True)
    rows = []
    for stage, odb_name in FLOW_ODBS.items():
        odb = run_dir / "results" / odb_name
        odb.write_bytes(f"odb bytes of {stage}\n".encode() * 50)
        rows.append({"stage": stage, "status": 0, "artifact": odb_name,
                     "sha256": _sha(odb)})
    (run_dir / "stage_artifact_manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    out = tmp_path / "sample"
    (out / "stage_defs").mkdir(parents=True)
    artifacts = {}
    cfg: dict[str, Any] = {"orfs_run_dir": str(run_dir)}
    for field, odb_name, def_name, artifact, semantics in [
        ("floorplan_def", "2_floorplan.odb", "2_floorplan.def", "floorplan_def",
         "post_floorplan_snapshot"),
        ("place_def", "3_place.odb", "3_place.def", "placement_def",
         "post_placement_snapshot"),
        ("cts_def", "4_cts.odb", "4_cts.def", "cts_def", "post_cts_snapshot"),
        ("route_def", "5_route.odb", "5_route.def", "routing_def",
         "post_routing_snapshot"),
    ]:
        target = out / "stage_defs" / def_name
        _fake_export(run_dir / "results" / odb_name, target)
        artifacts[artifact] = {"path": f"stage_defs/{def_name}",
                               "sha256": _sha(target), "semantics": semantics}
        cfg[field] = str(target)
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    cfg["raw_manifest"] = str(manifest)
    config = out / "sample.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    return {"run_dir": run_dir, "out": out, "cfg": cfg, "config": config,
            "manifest": manifest}


def _metadata(stage02, path: Path) -> dict[str, str]:
    """What stage 02 writes into metadata.csv for a stage that parsed ``path``."""
    return {"feature_source_path": str(path),
            "feature_source_sha256": stage02.sha256_file(path)}


@pytest.mark.parametrize("stage,key,flow_odb", [
    ("placement", "floorplan_def", "2_floorplan.odb"),
    ("cts", "place_def", "3_place.odb"),
    ("route", "cts_def", "4_cts.odb"),
])
def test_genuine_stage_input_is_bound_to_the_flow_record(
        validator, stage02, sample, stage, key, flow_odb):
    binding = validator.verify_stage_input_digest(
        sample["config"], sample["cfg"], stage,
        _metadata(stage02, Path(sample["cfg"][key])))
    assert binding["flow_artifact"] == flow_odb
    assert binding["flow_sha256"] == _sha(sample["run_dir"] / "results" / flow_odb)


def test_c8b_byte_substitution_is_rejected_although_the_declaration_is_correct(
        validator, stage02, sample):
    """The E12 C8b defect, reproduced exactly as inject_fourstage.py applies it."""
    victim = Path(sample["cfg"]["floorplan_def"])
    shutil.copyfile(sample["cfg"]["route_def"], victim)
    manifest = json.loads(sample["manifest"].read_text(encoding="utf-8"))
    manifest["artifacts"]["floorplan_def"]["sha256"] = _sha(victim)
    sample["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

    # Every declaration-level gate still passes: stage 02's own manifest gate
    # (path + semantics) accepts the file, the manifest digest matches the
    # substituted bytes, and the path is not the route DEF's path.
    declared = stage02.validate_manifest_stage(
        sample["config"], sample["cfg"], "floorplan_def", victim.resolve(), "floorplan")
    assert "floorplan" in declared["semantics"]
    assert manifest["artifacts"]["floorplan_def"]["sha256"] == _sha(victim)
    assert victim.resolve() != Path(sample["cfg"]["route_def"]).resolve()

    with pytest.raises(ValueError, match="不是流程记录的2_floorplan.odb"):
        validator.verify_stage_input_digest(
            sample["config"], sample["cfg"], "placement", _metadata(stage02, victim))


def test_a_stage_odb_mutated_after_the_flow_recorded_it_is_rejected(
        validator, stage02, sample):
    # Rewriting the .odb and re-exporting from it keeps DEF and ODB mutually
    # consistent; only the flow's own record can tell.
    odb = sample["run_dir"] / "results" / "3_place.odb"
    shutil.copyfile(sample["run_dir"] / "results" / "5_route.odb", odb)
    victim = Path(sample["cfg"]["place_def"])
    _fake_export(odb, victim)
    with pytest.raises(ValueError, match="流程记录的sha256不一致"):
        validator.verify_stage_input_digest(
            sample["config"], sample["cfg"], "cts", _metadata(stage02, victim))


def test_no_flow_record_fails_closed(validator, stage02, sample):
    manifest = sample["run_dir"] / "stage_artifact_manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    for row in rows:
        if row["stage"] == "cts":
            row["sha256"] = None  # what run_orfs.sh records for an absent artifact
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    metadata = _metadata(stage02, Path(sample["cfg"]["cts_def"]))
    with pytest.raises(ValueError, match="没有流程记录的4_cts.odb"):
        validator.verify_stage_input_digest(
            sample["config"], sample["cfg"], "route", metadata)

    cfg = dict(sample["cfg"])
    del cfg["orfs_run_dir"]
    with pytest.raises(ValueError, match="缺少orfs_run_dir"):
        validator.verify_stage_input_digest(sample["config"], cfg, "route", metadata)


def test_features_built_without_a_content_digest_do_not_pass(validator, sample):
    # A dataset extracted before the digest existed has only the path; it must
    # not be graded as content-bound.
    with pytest.raises(ValueError, match="未记录feature_source_sha256"):
        validator.verify_stage_input_digest(
            sample["config"], sample["cfg"], "placement",
            {"feature_source_path": sample["cfg"]["floorplan_def"]})


def test_resume_generation_binds_through_the_recorded_parent_lineage(
        validator, stage02, sample):
    """A repair run reruns only late stages; floorplan comes from its parent."""
    run_dir = sample["run_dir"]
    manifest = run_dir / "stage_artifact_manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    floorplan = next(r for r in rows if r["stage"] == "floorplan")
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows
                                if r["stage"] != "floorplan"), encoding="utf-8")
    (run_dir / "resume_meta.json").write_text(json.dumps({"parent_lineage": {
        "floorplan": {"artifact": "2_floorplan.odb", "sha256": floorplan["sha256"],
                      "parent_run": "RUN_2025-12-31_00-00-00_000000_0000"}}}),
        encoding="utf-8")
    validator.verify_stage_input_digest(
        sample["config"], sample["cfg"], "placement",
        _metadata(stage02, Path(sample["cfg"]["floorplan_def"])))


def test_every_def_reading_stage_is_bound_to_the_odb_its_def_was_exported_from(
        validator, stage02, config_mod):
    """Stage 02's input key -> make_sample_config's .odb must be the validator's map.

    A drifted map would compare a stage's features against another stage's
    flow record and either reject every clean dataset or bind the wrong stage.
    """
    exported_from = {field: odb for field, odb, *_ in config_mod.STAGE_ARTIFACTS}
    def_stages = {stage: key for stage, (key, _, _) in stage02.STAGE_INPUTS.items()
                  if key is not None}
    assert set(validator.STAGE_INPUT_ODB) == set(def_stages)
    for stage, key in def_stages.items():
        assert validator.STAGE_INPUT_ODB[stage][1] == exported_from[key]


def test_validator_and_extractor_are_wired_to_the_binding():
    # The unit tests above prove the check; these prove the pipeline runs it:
    # stage 02 digests the bytes BEFORE parsing them, and the validator's main
    # loop calls the binding for every DEF-reading stage.
    extractor = (R2G2_DIR / "02_extract_features.py").read_text(encoding="utf-8")
    digest_at = extractor.index("snapshot_sha256 = sha256_file(snapshot_path)")
    assert digest_at < extractor.index("snapshot = parse_def(snapshot_path)", digest_at)
    assert '"feature_source_sha256": snapshot_sha256,' in extractor
    checker = (R2G2_DIR / "checks" / "validate_four_stage.py").read_text(encoding="utf-8")
    main_body = checker[checker.index("def main() -> None:"):]
    assert "verify_stage_input_digest(" in main_body
    assert "if stage in STAGE_INPUT_ODB:" in main_body
