"""Flow binding contracts; these synthetic inputs do not establish EDA gains."""
import copy

import pytest

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


@pytest.mark.parametrize("location", ["spec", "before", "after"])
def test_config_presence_cannot_bootstrap_hardware_repair_assets(location):
    spec = {"kind": "config_presence", "config_key": "ROUTING_LAYER_ADJUSTMENT"}
    semantic = {"spec": spec} if location == "spec" else {location: {"spec": spec}}
    with pytest.raises(ValueError, match="not hardware repair evidence"):
        require_hardware_oracle({"verdict": "PASS", "oracle_complete": True,
                                 "semantic_oracle": semantic})
