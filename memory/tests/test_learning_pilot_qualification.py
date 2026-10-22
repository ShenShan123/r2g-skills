"""Learning qualification is isolated, no-model work, not a repair curve."""
import importlib.util
import json
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / 'evaluation/tools/learning_pilot_qualification.py'
spec = importlib.util.spec_from_file_location('learning_qualification_test', FILE)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def test_literal_config_does_not_execute_make_or_shell():
    for bad in ('export ABC_AREA = $(shell touch /tmp/not-allowed)',
                'include other.mk', 'export ABC_AREA = 1; touch /tmp/not-allowed',
                'export ABC_AREA = `id`', 'export SECRET = secret'):
        with pytest.raises(ValueError):
            L.literal_config(bad)


def test_path_rebase_is_confined_to_the_registered_repository(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    f = repo / 'top.v'
    f.write_text('module top; endmodule')
    assert L.old_source('/proj/workarea/yangao/rtl-certified-handoff-0002/sources/repo/top.v',
                        tmp_path, repo) == f
    for bad in ('/etc/passwd', '/old/sources/repo/../../outside', '/old/sources/other/top.v'):
        with pytest.raises(ValueError):
            L.old_source(bad, tmp_path, repo)


def test_fixed_clock_never_becomes_a_virtual_clock():
    sdc = L.resolved_sdc('top', 'clk', 5.0)
    assert 'set clk_period 5' in sdc
    assert 'get_ports -quiet $clk_port_name' in sdc
    assert 'error ' in sdc
    assert 'virtual' not in sdc
    assert 'set_input_delay' not in sdc  # preserve supplied internal-clock-only scope
    with pytest.raises(ValueError):
        L.resolved_sdc('top', 'clk; exec bad', 5.0)


def test_all_selected_lineages_are_disjoint_and_denominators_fixed():
    rows = [{'role': role, 'design_id': str(i), 'group_id': 'g' + str(i),
             'design': 'C' + str(i), 'frontend': 'verilog-native', 'baseline_clock_port': 'clk'}
            for role, indexes in [('LEARN', range(20)), ('PROBE', range(20, 28))] for i in indexes]
    assert len(L.selected_rows({'selected': {'LEARN': rows[:20], 'PROBE': rows[20:]}})) == 28
    rows[-1]['group_id'] = rows[0]['group_id']
    with pytest.raises(ValueError, match='lineage'):
        L.selected_rows({'selected': {'LEARN': rows[:20], 'PROBE': rows[20:]}})


def test_hash_gate_detects_mutated_input(tmp_path):
    path = tmp_path / 'input'
    path.write_text('original')
    hashes = {str(path): L.digest(path)}
    L.verify_hashes(hashes)
    path.write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        L.verify_hashes(hashes)


def test_budget_has_no_model_or_repair_authority():
    budget = L.qualification_budget()
    assert budget['caps']['flows'] == 1
    assert budget['caps']['llm_requests'] == 0
    assert budget['caps']['repair_attempts'] == 0
    assert budget['caps']['tokens_in'] == budget['caps']['tokens_out'] == 0


def test_worker_environment_cannot_inherit_memory_or_payment_authority(tmp_path):
    raw = {'PATH': '/usr/bin', 'R2G_PHASE_LEDGER': '/old/paid', 'TEHM_DB': '/old/store',
           'R2G_MEMORY_BACKEND': 'tehm', 'FROM_STAGE': 'route', 'CUDA_VISIBLE_DEVICES': '0'}
    env = L.clean_environment(raw)
    assert env == {'PATH': '/usr/bin', 'CUDA_VISIBLE_DEVICES': '0'}


def test_existing_attempt_is_never_overwritten_or_retried(tmp_path):
    target = tmp_path / 'result.json'
    target.write_text(json.dumps({'status': 'RUNNING'}))
    with pytest.raises(RuntimeError, match='retry'):
        L.claim_attempt(target)
    assert json.loads(target.read_text()) == {'status': 'RUNNING'}


def test_cpu_slots_cannot_share_cores_with_r4_or_each_other():
    L.check_slots([[53, 149], [61, 157]], set(range(192)))
    for slots in ([[5, 101]], [[53, 149], [53, 149]], [[53], [149]]):
        with pytest.raises(ValueError):
            L.check_slots(slots, set(range(192)))


def test_subset_requires_a_registered_replacement_not_an_old_retry():
    rows = [{'design': 'old'}, {'design': 'new'}]
    roster = {'qualification_amendment': {'replacement_design': 'new'}}
    assert L.qualification_rows(roster, rows, ['new']) == [rows[1]]
    for subset in (['old'], ['outside'], ['new', 'new'], ['old', 'new']):
        with pytest.raises(ValueError):
            L.qualification_rows(roster, rows, subset)
    assert L.qualification_rows({}, rows, None) == rows
