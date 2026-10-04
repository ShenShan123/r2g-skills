import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('pilot_driver', Path(__file__).resolve().parents[1]/'run_downstream_pilots.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_busy_gpu_does_not_start_or_make_output(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(tmp_path/'manifest.json'),
                        '--output', str(tmp_path/'out'), '--gpu', '2'])
    monkeypatch.setattr(mod, 'gpu_info', lambda index: {'memory_mib': 100, 'utilization': 0})
    with pytest.raises(RuntimeError, match='in use'):
        mod.main()
    assert not (tmp_path/'out').exists()


def test_cpu_rejects_formal_manifest_without_probing_gpu(tmp_path, monkeypatch):
    m=tmp_path/'manifest.json';m.write_text(json.dumps({'pilot_subset':False}))
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(m), '--output', str(tmp_path/'out'), '--cpu'])
    monkeypatch.setattr(mod, 'gpu_info', lambda index: pytest.fail('CPU must not probe GPU'))
    with pytest.raises(ValueError, match='bounded pilot'):
        mod.main()
    assert not (tmp_path/'out').exists()


def test_mutually_exclusive_device_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '--manifest', str(tmp_path/'manifest.json'),
                        '--output', str(tmp_path/'out'), '--cpu', '--gpu', '2'])
    with pytest.raises(ValueError, match='exactly one'):
        mod.main()
