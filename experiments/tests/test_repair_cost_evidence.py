import hashlib
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('cost_evidence', Path(__file__).parents[1] / 'export_repair_cost_evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def test_closed_evidence_skips_large_hashes_but_detects_replacement(tmp_path, monkeypatch):
    folder = tmp_path / 'cases/nangate45_C00001'
    project = tmp_path / 'runtime/workspace/job1/design'
    inp = tmp_path / 'inputs/design'
    inp.mkdir(parents=True)
    (inp / 'rtl.v').write_text('original')
    save(folder / 'summary.json', {'status': 'complete', 'session_id': 'session1',
         'designs': [{'project': str(project), 'job_id': 'job1', 'name': 'design'}]})
    save(folder / 'input_receipt.json', {'input': str(inp), 'sha256': {'rtl.v': 'expected'}})
    save(project / 'reports/drc.json', {'run_tag': 'RUN_1'})
    save(project / 'metadata.json', {})
    result = project / 'backend/RUN_1/results'
    result.mkdir(parents=True)
    gds = result / '6_final.gds'
    gds.write_bytes(b'original GDS')
    (result / '6_final.def').write_bytes(b'original DEF')
    original_digest = evidence.digest
    hashed = []
    def track(path):
        hashed.append(Path(path))
        return original_digest(path)
    monkeypatch.setattr(evidence, 'digest', track)
    assert evidence.export(tmp_path) == 1
    receipt = (folder / 'signoff_evidence.json').read_bytes()
    hashed.clear()
    assert evidence.export(tmp_path) == 1
    assert gds not in hashed
    assert (folder / 'signoff_evidence.json').read_bytes() == receipt
    stamp = gds.stat()
    gds.write_bytes(b'modified GDS')  # same length
    os.utime(gds, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    hashed.clear()
    assert evidence.export(tmp_path) == 1
    assert gds in hashed
    updated = json.loads((folder / 'signoff_evidence.json').read_text())
    assert updated['projects'][0]['artifact_sha256']['gds'] == hashlib.sha256(b'modified GDS').hexdigest()
