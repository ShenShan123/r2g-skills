"""P1b: rebuild admission == runtime admission, idempotence, no implicit authority (T9, T12, T13).

Runs on SCRATCH copies of the frozen evaluation stores (SQLite backup API + artifacts copy); the frozen
stores themselves are opened read-only and never written. Skipped where the stores are not present.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tehm_backend as tb  # noqa: E402

E = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
STORES = {"SL-hs": "sandbox-f/tehm/SL-hs", "Mp-hd": "sandbox-f/tehm/Mp-hd", "G-hs": "sandbox-f/tehm/G-hs",
          "P-hs": "evaluation-h-ablation/no_llm_evidence-hs",
          "L-hd": "evaluation-h-ablation/no_component_trial_evidence-hd", "J-hs": "evaluation-i/stores/IJ-hs"}
pytestmark = pytest.mark.skipif(not E.exists(), reason="frozen evaluation stores not present")


def _scratch(src: Path, dst: Path) -> tb.TehmMemoryBackend:
    dst.mkdir(parents=True)
    with sqlite3.connect("file:%s?mode=ro" % (src / "tehm.sqlite"), uri=True) as s, \
            sqlite3.connect(dst / "tehm.sqlite") as d:
        s.backup(d)
    shutil.copytree(src / "artifacts", dst / "artifacts")
    return tb.TehmMemoryBackend(db_path=dst / "tehm.sqlite", artifact_root=dst / "artifacts", read_only_eval=False)


def _statuses(db: Path) -> dict:
    with sqlite3.connect(db) as c:
        return {(r, s): st for r, s, st in c.execute("SELECT rule_id, target_scope, status FROM tehm_rule_status")}


@pytest.mark.parametrize("name", sorted(STORES))
def test_t13_rebuild_candidacy_equals_runtime_admission(name, tmp_path):
    b = _scratch(E / STORES[name], tmp_path / "s")
    b.rebuild()
    c = sqlite3.connect(tmp_path / "s" / "tehm.sqlite")
    c.row_factory = sqlite3.Row
    checked = 0
    for (rule_id, _scope), status in _statuses(tmp_path / "s" / "tehm.sqlite").items():
        if status not in ("candidate", "shadow"):
            continue
        assert (status == "candidate") == tb._trial_admissible(c, rule_id, status), (name, rule_id, status)
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("name", ["SL-hs", "Mp-hd"])
def test_t9_rebuild_is_idempotent(name, tmp_path):
    b = _scratch(E / STORES[name], tmp_path / "s")
    b.rebuild()
    first = _statuses(tmp_path / "s" / "tehm.sqlite")
    b.rebuild()
    assert _statuses(tmp_path / "s" / "tehm.sqlite") == first


def test_t12_rebuild_never_grants_or_revokes_production_authority(tmp_path):
    b = _scratch(E / STORES["SL-hs"], tmp_path / "s")
    db = tmp_path / "s" / "tehm.sqlite"
    with sqlite3.connect(db) as c:   # make one rule promoted (as an operator would), then rebuild
        rule_id, scope = c.execute("SELECT rule_id, target_scope FROM tehm_rule_status WHERE status='shadow' "
                                   "ORDER BY rule_id LIMIT 1").fetchone()
        c.execute("UPDATE tehm_rule_status SET status='promoted' WHERE rule_id=? AND target_scope=?", (rule_id, scope))
    b.rebuild()
    after = _statuses(db)
    assert after[(rule_id, scope)] == "promoted"
    assert [k for k, v in after.items() if v == "promoted"] == [(rule_id, scope)]   # nothing else promoted


def test_one_rule_per_id_is_order_independent_and_conservative():
    from tehm.crystallization.build_rules import _one_rule_per_id
    a = {"rule_id": "r1", "validity_status": "VALIDATED", "merge_trace_digest": "b"}
    b = {"rule_id": "r1", "validity_status": "REJECT_DEGENERATE", "merge_trace_digest": "a"}
    c = {"rule_id": "r2", "validity_status": "PROVISIONAL_VALID", "merge_trace_digest": "c"}
    for order in ([a, b, c], [b, a, c], [c, a, b]):
        out = _one_rule_per_id(order)
        assert sorted(r["rule_id"] for r in out) == ["r1", "r2"]
        assert next(r for r in out if r["rule_id"] == "r1") is b        # the conservative one, whatever the order
