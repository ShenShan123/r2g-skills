"""C9 is explicit UNKNOWN occupancy, not usage rewriting or a silent retry."""
import importlib.util
from pathlib import Path
import pytest

FILE = Path(__file__).resolve().parents[1] / 'evaluation/tools/t2a_mechanism_continue.py'
spec = importlib.util.spec_from_file_location('mechanism_continue_test', FILE)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def bank():
    return {'caps': {'llm_requests': 5000}, 'combined_tokens_cap': 17000000,
            'open_holds': 0, 'budget_spent': {'llm_requests': 4869, 'tokens_in': 13773980,
                                           'tokens_out': 2931856},
            'unknown_occupancies': [{'hold_id': 'db96c8422b27',
                                    'upper_bound': {'tokens_in': 5362, 'tokens_out': 800},
                                    'actual_remains': 'UNKNOWN'}]}


def test_exact_authorized_bank_allows_continuation():
    C.bank_ready(bank())


@pytest.mark.parametrize('change', ['hold', 'unknown', 'nan', 'negative', 'exhausted', 'cap', 'calls', 'missing_grant', 'actual_zero'])
def test_bank_gate_stays_fail_closed(change):
    b = bank()
    if change == 'hold': b['open_holds'] = 1
    elif change == 'unknown': b['budget_spent']['tokens_in'] = 'UNKNOWN'
    elif change == 'nan': b['budget_spent']['tokens_in'] = float('nan')
    elif change == 'negative': b['budget_spent']['tokens_in'] = -1
    elif change == 'exhausted': b['budget_spent']['tokens_in'] = 17000000
    elif change == 'cap': b['combined_tokens_cap'] = 18000000
    elif change == 'calls': b['budget_spent']['llm_requests'] = 4905
    elif change == 'missing_grant': b['unknown_occupancies'] = []
    elif change == 'actual_zero': b['unknown_occupancies'][0]['actual_remains'] = 0
    with pytest.raises(RuntimeError): C.bank_ready(b)


def test_no_existing_core_or_continuation_can_be_retried(tmp_path):
    (tmp_path / 'continuation-c9').mkdir()
    with pytest.raises(RuntimeError, match='never overwrite'): C.prepare(tmp_path)
    (tmp_path / 'continuation-c9/start.json').write_text('{}')
    with pytest.raises(RuntimeError, match='no implicit retry'): C.run(tmp_path)


def test_missing_native_receipts_are_not_success(tmp_path):
    (tmp_path / 'registration.json').write_text('{"runtime_hashes": {}}')
    with pytest.raises(FileNotFoundError): C.native_receipts(tmp_path, tmp_path)
