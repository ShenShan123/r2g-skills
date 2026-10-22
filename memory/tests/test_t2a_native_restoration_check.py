"""Native diagnostics must not collide through ORFS's global variant names."""
import importlib.util
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'evaluation/tools/t2a_native_restoration_check.py'
spec=importlib.util.spec_from_file_location('native_restoration_check',PATH)
N=importlib.util.module_from_spec(spec);spec.loader.exec_module(N)


def test_native_case_and_registration_roots_have_distinct_orfs_variant_names(tmp_path):
    names={N.project_path(tmp_path/root,case).name
           for root in ('first-registration','second-registration') for case in N.CASES}
    assert set(N.CASES)=={'cts_partial','completed_area','place_partial','task_entry'}
    assert len(names)==2*len(N.CASES) and 'project' not in names
    assert all('__' in name for name in names)
