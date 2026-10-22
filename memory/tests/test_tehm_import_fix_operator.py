"""The correction operator must preserve failed preparations and refuse stale receipts."""
import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/t2_tehm_import_fix.py"
spec = importlib.util.spec_from_file_location("tehm_import_fix_operator", SCRIPT)
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


def test_export_never_overwrites_previous_attempt(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "original"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="no overwrite"):
        op.prepare_export(tmp_path / "missing", out)
    assert sentinel.read_text() == "keep"


def test_build_never_overwrites_existing_store(tmp_path):
    out = tmp_path / "store"
    out.mkdir()
    with pytest.raises(ValueError, match="no overwrite"):
        op.build_store(tmp_path / "missing", out, tmp_path)


def test_prior_receipt_requires_exact_annotation_and_source_hashes(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source_export.jsonl"
    ann = tmp_path / "ann_v2_dev.jsonl"
    for p in (source, ann):
        p.write_text('{"unit_id":"unit"}\n')
    units = package / "shared_units.jsonl"
    units.write_text("")
    (package / "manifest.json").write_text(json.dumps({
        "units_sha256": op.sha(units), "inputs": {str(source): op.sha(source), str(ann): op.sha(ann)}}))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "independent-verifier-0.json").write_text(json.dumps({
        "gate": "PASS", "critical": 0, "records": 1,
        "inputs": {"annotation": str(ann), "annotation_sha256": "wrong",
                   "source_export_sha256": op.sha(source)}}))
    with pytest.raises(ValueError, match="not bound"):
        op.prepare_export(package, tmp_path / "out", receipts)


def test_launch_never_restarts_existing_launch(tmp_path):
    receipt = tmp_path / "launch-receipt.json"
    receipt.write_text("keep")
    with pytest.raises(FileExistsError):
        op.run(tmp_path)
    assert receipt.read_text() == "keep"


def dispatcher_sources():
    prefix = 'import argparse\n\nARMS = ("NB", "RECIPE", "TM0")\n\n'
    base = prefix + 'def main(argv=None):\n        root = a.root.resolve()\n        B.batch(root, "DEV")\n'
    candidate = prefix.replace('import argparse\n', 'import argparse\nimport fcntl\nimport subprocess\n')
    candidate += '# ---------------------------------------------------------------------------------- dispatcher\n'
    candidate += 'def dispatch(root):\n    return {}\n\n'
    candidate += ('def main(argv=None):\n        st = dispatch(a.root.resolve())\n'
                  '        print(json.dumps({k: st.get(k) for k in ("status", "reason", "next_wave", "flows_charged")}))\n')
    return base, candidate


def test_dispatcher_delta_preserves_existing_functions():
    base, candidate = dispatcher_sources()
    assert op.validated_dispatcher_source(base, candidate) == candidate


def test_dispatcher_delta_rejects_unrelated_runtime_changes():
    base, candidate = dispatcher_sources()
    candidate = candidate.replace('ARMS = ("NB", "RECIPE", "TM0")', 'ARMS = ("TM0",)')
    with pytest.raises(ValueError, match="unrelated dispatcher"):
        op.validated_dispatcher_source(base, candidate)


def test_missing_private_dispatcher_fails_before_launch_receipt(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 't2_dev3', SimpleNamespace())
    with pytest.raises(RuntimeError, match='dependency missing'):
        op.run(tmp_path)
    assert not (tmp_path / 'launch-receipt.json').exists()
