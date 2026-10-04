import importlib.util
from pathlib import Path


PATH = Path(__file__).parents[1] / 'recover_n45_input_roles.py'
SPEC = importlib.util.spec_from_file_location('recover_n45_input_roles', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_corrected_task_moves_header_out_of_compilation_units():
    source = {'candidate': {
        'rtl_files': ['rtl/top.v', 'include/defs.vh'],
        'header_files': [],
        'readmem_files': [],
    }}
    fixed = MODULE.corrected_task(source)
    assert fixed['candidate']['rtl_files'] == ['rtl/top.v']
    assert fixed['candidate']['header_files'] == ['include/defs.vh']
    assert source['candidate']['rtl_files'] == ['rtl/top.v', 'include/defs.vh']


def test_corrected_task_preserves_existing_dependencies():
    source = {'candidate': {
        'rtl_files': ['top.sv', 'defs.svh'],
        'header_files': ['other.vh'],
        'readmem_files': ['init.mem'],
    }}
    fixed = MODULE.corrected_task(source)
    assert fixed['candidate']['rtl_files'] == ['top.sv']
    assert fixed['candidate']['header_files'] == ['defs.svh', 'init.mem', 'other.vh']


def test_corrected_task_rejects_unneeded_recovery():
    source = {'candidate': {'rtl_files': ['top.v'], 'header_files': [], 'readmem_files': []}}
    try:
        MODULE.corrected_task(source)
    except ValueError as exc:
        assert 'no misclassified dependency' in str(exc)
    else:
        raise AssertionError('expected ValueError')
