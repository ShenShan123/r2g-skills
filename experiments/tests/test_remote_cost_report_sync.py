import base64
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cost_sync', Path(__file__).parents[1] / 'sync_remote_cost_reports.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_report_sync_installs_selected_receipts_atomically(tmp_path):
    sync.install_receipts(tmp_path, {'cases/sky130hd_C00001/state.json': base64.b64encode(b'{"platform":"sky130hd"}').decode()})
    assert (tmp_path / 'cases/sky130hd_C00001/state.json').read_bytes() == b'{"platform":"sky130hd"}'


@pytest.mark.parametrize('name', ['../credentials.yaml', '/tmp/outside.json'])
def test_remote_paths_cannot_escape_experiment(tmp_path, name):
    with pytest.raises(ValueError, match='path'):
        sync.install_receipts(tmp_path, {name: 'e30='})


def test_remote_paths_cannot_follow_local_symlink(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    local = tmp_path / 'local'
    local.mkdir()
    (local / 'cases').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='escape'):
        sync.install_receipts(local, {'cases/receipt.json': 'e30='})
