import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'memory/evaluation/tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('learning_package_test', TOOLS / 'learning_runtime_package.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_recursive_evaluation_exclusion_breaks_real_isolated_import(tmp_path):
    code = tmp_path / 'code'
    shutil.copytree(ROOT / 'memory', code / 'memory', ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'docs', 'tests', 'evaluation', '.pytest_cache'))
    command = [sys.executable, '-I', '-c',
               'import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); '
               'from tehm.evolution.anti_forgetting import evaluate_anti_forgetting', str(code / 'memory')]
    # Assert failure on the old packaging, without borrowing modules from main.
    before = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert before.returncode != 0 and "No module named 'tehm.evaluation'" in before.stderr
    added = P.add_runtime_evaluation(ROOT, code)
    assert str(ROOT / 'memory/tehm/evaluation/candidate_executor.py') in added
    # Import the actual package entrypoint instead of relying on mock imports.
    command[3] = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0,sys.argv[1]); '
                  'import tehm.evolution.anti_forgetting; import tehm.evaluation.candidate_executor; '
                  'print(tehm.evaluation.candidate_executor.__file__)')
    after = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert after.returncode == 0, after.stderr
    assert str(code) in after.stdout and str(ROOT) not in after.stdout
    assert not (code / 'memory/evaluation').exists()
    with pytest.raises(FileExistsError):
        P.add_runtime_evaluation(ROOT, code)
