from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
from provider_clients import flash_key


def test_key_selector_is_standalone_and_cannot_select_pro(tmp_path):
    path = tmp_path / "fixture.env"
    path.write_text('export DEEPSEEK_API_KEY="TEST-PRO"\nexport DEEPSEEK_MODEL="deepseek-v4-pro"\n'
                    'export DEEPSEEK_API_KEY="TEST-FLASH"\nexport DEEPSEEK_MODEL="deepseek-v4.1-flash"\n')
    assert flash_key(path) == "TEST-FLASH"
    assert "r2g_memory_eval" not in sys.modules
