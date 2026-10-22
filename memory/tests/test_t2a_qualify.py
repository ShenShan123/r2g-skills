import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import t2a_qualify as Q


def test_qualification_uses_real_ledger_event_api_and_stops_at_two_candidates(tmp_path, monkeypatch):
    checker = tmp_path / 'code/r2g-skills/def-graph/scripts/flow/signoff_gate.py'
    checker.parent.mkdir(parents=True)
    checker.touch()
    tasks = [{'design': 'gcd', 'variant': v, 'project': str(tmp_path / v)} for v in ('default', 'u80', 'u90')]
    Q.save(tmp_path / 'registration.json', {'files': {}, 'tasks': tasks})
    calls = []
    def ledger(proj, command, *args):
        assert command == 'event' and args[:2] == ('--type', 'attempt_start')
        calls.append(proj)
        return 0, ''
    def load(path):
        return {'orfs_fail_stage': 'place'} if path.name == 'ppa.json' else {}
    h = SimpleNamespace(_EPISODE={}, base_env=lambda _: {}, _ledger=ledger,
                        control_flow=lambda p,e: {'orfs_rc': 1, 'final_layout': False}, jload=load)
    monkeypatch.setattr(Q, 'module', lambda *a: h)
    monkeypatch.setenv('R2G_BUDGET_FILE', 'unused')
    monkeypatch.setenv('R2G_REQUIRE_LLM_BUDGET', 'unused')
    Q.run(tmp_path, 'gcd')
    summary = json.loads((tmp_path / 'gcd-summary.json').read_text())
    assert len(calls) == len(summary['records']) == 2
    assert not (tmp_path / 'gcd__u90.json').exists()
    assert summary['formal_test_manifest_frozen'] is False
