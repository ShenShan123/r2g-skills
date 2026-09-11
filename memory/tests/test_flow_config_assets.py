"""Flow binding contracts; these synthetic inputs do not establish EDA gains."""
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from tehm.assets import flow_config
from tehm.assets.flow_config import bind_flow_config, select_flow_binding, require_hardware_oracle


def _asset():
    return {"asset_id": "flow-unit", "definition": {"action": {
        "payload": {"config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.05"}}}}}


def _context():
    return {"flow_design_id": "unit", "flow_config": {"ROUTING_LAYER_ADJUSTMENT": "0.8"}}


def test_fixed_training_action_does_not_read_target_proposal():
    asset, context = _asset(), _context()
    before = copy.deepcopy(asset)
    context["proposed_action"] = {"config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.99"}}
    binding = bind_flow_config(asset, "mk@1", context)
    assert binding.eligible and binding.selected_binding == {}
    assert binding.failure_evidence == ()
    assert asset == before
    context.pop("proposed_action")
    assert bind_flow_config(asset, "mk@1", context) == binding


@pytest.mark.parametrize("value", [None, True, "$(shell command)", "nan", "inf", "0", "1.1", {}])
def test_binding_rejects_unresolved_or_invalid_current_config(value):
    context = _context()
    context["flow_config"]["ROUTING_LAYER_ADJUSTMENT"] = value
    with pytest.raises(ValueError):
        bind_flow_config(_asset(), "mk@1", context)


def test_binding_rejects_noop():
    context = _context()
    context["flow_config"]["ROUTING_LAYER_ADJUSTMENT"] = "0.050"
    with pytest.raises(ValueError, match="no-op"):
        bind_flow_config(_asset(), "mk@1", context)


def test_binding_refuses_arbitrary_configuration_keys():
    asset = _asset()
    asset["definition"]["action"]["payload"]["config_edits"] = {"SDC_FILE": "/some/path"}
    with pytest.raises(ValueError, match="unsupported"):
        bind_flow_config(asset, "mk@1", _context())


def test_binding_changes_digest_when_observed_target_changes():
    context = _context()
    before = bind_flow_config(_asset(), "mk@1", context)
    context["flow_config"]["ROUTING_LAYER_ADJUSTMENT"] = "0.7"
    after = bind_flow_config(_asset(), "mk@1", context)
    assert before.binding_digest != after.binding_digest


def test_selection_rejects_asset_without_knowledge_witness_before_db_access():
    with pytest.raises(ValueError, match="knowledge binding mismatch"):
        select_flow_binding(None, _asset(), {"mk@1"}, _context())


def test_selection_follows_only_explicit_same_claim_supersedes(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE tehm_memory_relations
                    (source_type TEXT, source_id TEXT, relation_type TEXT,
                     target_type TEXT, target_id TEXT, scope_json TEXT)""")
    conn.execute("INSERT INTO tehm_memory_relations VALUES (?, ?, ?, ?, ?, ?)",
                 ("knowledge", "mk@2", "SUPERSEDES", "knowledge", "mk@1",
                  '{"target_scope":"flow_feasibility"}'))
    asset = {
        **_asset(), "asset_type": "FLOW_CONFIG_TRANSFORM", "name": "flow.mk",
        "version": "flow_numeric_config_v1", "input_contract": {},
        "output_contract": {}, "verifier_contract": {}, "compatibility": {},
        "provenance": {"mechanism_knowledge_ids": ["mk@1"],
                       "campaign_id": "training", "witness": "fixed"},
    }
    expected = copy.deepcopy(asset)
    expected["provenance"]["mechanism_knowledge_ids"] = ["mk@2"]
    proposal = SimpleNamespace(
        to_dict=lambda: copy.deepcopy(expected),
        provenance=copy.deepcopy(expected["provenance"]))
    seen = {}

    def build(_conn, knowledge_id, **_kwargs):
        seen["knowledge_id"] = knowledge_id
        return proposal

    monkeypatch.setattr(flow_config, "build_flow_asset_proposal", build)
    context = {**_context(), "target_scope": "flow_feasibility"}
    binding = select_flow_binding(conn, asset, {"mk@2"}, context)
    assert binding.knowledge_id == "mk@2"
    assert seen["knowledge_id"] == "mk@2"
    with pytest.raises(ValueError, match="scope mismatch"):
        select_flow_binding(
            conn, asset, {"mk@2"}, {**context, "target_scope": "other"})
    conn.execute("DELETE FROM tehm_memory_relations")
    with pytest.raises(ValueError, match="revision relation is missing"):
        select_flow_binding(conn, asset, {"mk@2"}, context)
    conn.close()


def test_scoped_binding_requires_and_hashes_same_measurement_contract():
    asset, context = _asset(), _context()
    legacy = bind_flow_config(asset, "mk@1", context)
    measurement = {"scope": "flow_feasibility", "contract_digest": "test-measurement"}
    asset["definition"]["measurement_contract"] = measurement
    asset["verifier_contract"] = {"measurement_contract": copy.deepcopy(measurement)}
    asset["compatibility"] = {"target_scope": "flow_feasibility"}
    with pytest.raises(ValueError, match="measurement contract mismatch"):
        bind_flow_config(asset, "mk@1", context)
    context.update(target_scope="flow_feasibility", measurement_contract_digest="test-measurement")
    scoped = bind_flow_config(asset, "mk@1", context)
    assert scoped.eligible and scoped.binding_digest != legacy.binding_digest
    for change in ({"target_scope": "global"}, {"measurement_contract_digest": "different"}):
        with pytest.raises(ValueError, match="measurement contract mismatch"):
            bind_flow_config(asset, "mk@1", {**context, **change})
    asset["verifier_contract"]["measurement_contract"]["contract_digest"] = "different"
    with pytest.raises(ValueError, match="measurement contract mismatch"):
        bind_flow_config(asset, "mk@1", context)


@pytest.mark.parametrize("location", ["spec", "before", "after"])
def test_config_presence_cannot_bootstrap_hardware_repair_assets(location):
    spec = {"kind": "config_presence", "config_key": "ROUTING_LAYER_ADJUSTMENT"}
    semantic = {"spec": spec} if location == "spec" else {location: {"spec": spec}}
    with pytest.raises(ValueError, match="not hardware repair evidence"):
        require_hardware_oracle({"verdict": "PASS", "oracle_complete": True,
                                 "semantic_oracle": semantic})
