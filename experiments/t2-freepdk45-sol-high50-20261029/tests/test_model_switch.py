"""The requested Sol/high replacement must not modify or relabel Terra."""
import json
from pathlib import Path

import experiment as E
import sol_provider as P


def test_exact_target_and_effort():
    assert P.MODEL=='gpt-6.1-sol' and P.EFFORT=='high'
    assert E.load(E.ROOT/'registration-staged.json')['model_switch']['start_all_fifty_afresh'] is True


def test_independent_namespace_and_history():
    reg=E.load(E.ROOT/'registration-staged.json')
    assert reg['namespace_arm']=='R2G_SOL_HIGH'
    assert reg['cross_task_memory'] is False
    assert len(reg['plan'])==50
    assert reg['phase_binding']['ledger_path']==str(E.ROOT/'phase/resource_ledger.jsonl')
    terra=E.ROOT.parent/'t2-freepdk45-terra-high50-20261029'
    assert E.sha(terra/'registration.json')==E.load(E.ROOT/'terra-operator-pause.json')['terra_registration_sha256']


def test_no_checker_or_toolchain_delta_for_model_comparison():
    terra=E.ROOT.parent/'t2-freepdk45-terra-high50-20261029'
    for rel in ('run_drc.sh','run_lvs.sh','run_orfs.sh','fix_signoff.sh','_bounded_run.sh'):
        path=Path('r2g-skills/signoff-loop/scripts/flow')/rel
        assert E.sha(E.ROOT/'code'/path)==E.sha(terra/'code'/path)
