import dataclasses
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from bridge import FullMemory, State, Verification, digest


def states(task="task-1", domain="software", profile="python-test-v1"):
    before = State(task, task, domain, profile, "target_test",
                   {"target_test": {"status": "fail"}}, {"source": "raise AssertionError"},
                   {"observable_symptom": {"check": "target_test", "class": "AssertionError"}})
    after = dataclasses.replace(before, reports={"target_test": {"status": "pass"}},
                                config={"source": "assert True"})
    return before, after


def verify(memory, before, action, after, *, verdict="PASS", oracle="TARGET_TEST", complete=True):
    result = subprocess.run([sys.executable, "-c", after.config["source"]],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (verdict == "PASS")
    path = memory.arm_root / "episodes" / (digest([before.canonical(), action, after.canonical()]) + ".json")
    path.write_text(json.dumps({"producer": "native_evaluator", "verdict": verdict,
        "oracle_type": oracle, "scope": "target_test", "complete": complete,
        "before_digest": digest(before.canonical()), "after_digest": digest(after.canonical()),
        "action_digest": digest(action), "returncode": result.returncode}))
    return Verification(verdict, oracle, "target_test", complete, str(path),
                        hashlib.sha256(path.read_bytes()).hexdigest(), True, verdict == "PASS")


ACTION = {"domain": "software.PATCH", "transformation_family": "native_patch",
          "payload": {"patch": "raise AssertionError -> assert True"}}


def test_real_core_capture_materializes_views_and_keeps_arms_separate(tmp_path):
    before, after = states()
    native = FullMemory("mini-swe-agent", arm="native", root=tmp_path)
    memory = FullMemory("mini-swe-agent", root=tmp_path)
    try:
        assert native.consult(before)["mode"] == "native"
        assert memory.consult(before)["mode"] == "no_experience"
        result = memory.write(before, ACTION, after, verify(memory, before, ACTION, after), rolled_back=False)
        assert result["rebuild_ok"] and result["admission"]["eligible"]
        conn, _ = memory.backend._open()
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(DISTINCT view_type) FROM tehm_views").fetchone()[0] >= 4
        assert not (native.arm_root / "memory/tehm.sqlite").exists()
        # Re-observation has the same canonical identity and no duplicate evidence.
        memory.write(before, ACTION, after, verify(memory, before, ACTION, after), rolled_back=False)
        assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
    finally:
        memory.close()


def test_compile_scope_is_written_without_upgrading_to_functional_support(tmp_path):
    memory = FullMemory("rtlfixer", root=tmp_path)
    before, after = states(domain="rtl", profile="iverilog")
    try:
        v = verify(memory, before, ACTION, after, oracle="COMPILE")
        result = memory.write(before, ACTION, after, v, rolled_back=False)
        assert not result["admission"]["eligible"]
        assert "oracle_type_not_executable" in result["admission"]["reason"]
    finally:
        memory.close()


@pytest.mark.parametrize("change", ["tamper", "unbound_action", "unbound_state"])
def test_bad_provenance_cannot_enter_memory(tmp_path, change):
    memory = FullMemory("mini-swe-agent", root=tmp_path)
    before, after = states()
    v = verify(memory, before, ACTION, after)
    if change == "tamper": Path(v.evidence_path).write_text("{}")
    elif change == "unbound_state": after = dataclasses.replace(after, config={"source": "different"})
    action = {**ACTION, "payload": {"patch": "different"}} if change == "unbound_action" else ACTION
    try:
        with pytest.raises(ValueError): memory.write(before, action, after, v, rolled_back=False)
    finally:
        memory.close()


def test_missing_verification_is_unknown_and_is_never_a_positive(tmp_path):
    memory = FullMemory("rtlscout", root=tmp_path)
    before, after = states(domain="rtl")
    v = verify(memory, before, ACTION, after)
    raw = json.loads(Path(v.evidence_path).read_text())
    raw.update(verdict="UNKNOWN", complete=False, oracle_type="UNKNOWN")
    Path(v.evidence_path).write_text(json.dumps(raw))
    v = dataclasses.replace(v, verdict="UNKNOWN", complete=False, oracle_type="UNKNOWN",
                            original_failure_removed=None,
                            evidence_sha256=hashlib.sha256(Path(v.evidence_path).read_bytes()).hexdigest())
    try:
        result = memory.write(before, ACTION, after, v, rolled_back=False)
        assert not result["admission"]["eligible"] and result["outcome"] != "PASS"
    finally:
        memory.close()


def test_storage_symlink_is_rejected(tmp_path):
    (tmp_path / "hosts/rtlfixer/tehm").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "hosts/rtlfixer/tehm/memory").symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(ValueError): FullMemory("rtlfixer", root=tmp_path)
