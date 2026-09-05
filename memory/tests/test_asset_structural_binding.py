"""Answer firewall, alpha-transfer bounds, and authority replay negatives."""
import copy
from pathlib import Path

import pytest

from tehm.assets import (
    bind_rtl_asset_to_project, bind_rtl_asset_to_source,
    get_asset, record_asset_authority, promote_asset, register_asset_proposal,
    set_asset_status, verify_asset_authority,
)
from tehm.assets.structural_binding import verify_structural_binding
from test_asset_memory import _proposal, PROJECTS


def _source(name):
    return (PROJECTS / name / "rtl/req_ack_fsm.v").read_text()


def test_binding_is_file_free_and_discards_answer_comments(monkeypatch):
    asset, source = _proposal().to_dict(), _source("req_ack_bug3")
    def reject_read(*args, **kwargs):
        raise AssertionError("binding must not read files")
    monkeypatch.setattr(Path, "read_text", reject_read)
    bound = bind_rtl_asset_to_source(asset, source, design_id="heldout")
    changed = bind_rtl_asset_to_source(
        asset, source + "\n// FIX: use wrong_answer\n", design_id="heldout")
    assert bound == changed
    assert verify_structural_binding(bound, asset)
    assert bound["definition"]["action"]["payload"]["add_condition"] == "rd_ack"


@pytest.mark.parametrize("change", ["condition", "operator", "extra_module", "extra_input"])
def test_structure_change_does_not_silently_localize(change):
    source = _source("req_ack_bug3")
    if change == "condition":
        source = source.replace("if (rd_req)", "if (rd_req && rd_ack)")
    elif change == "operator":
        source = source.replace("state == RD_DONE", "state != RD_DONE")
    elif change == "extra_module":
        source += "\nmodule another; endmodule\n"
    else:
        source = source.replace("input  wire       rd_ack,",
                                "input wire another_ack, input wire rd_ack,")
    with pytest.raises(ValueError):
        bind_rtl_asset_to_source(_proposal().to_dict(), source, design_id="heldout")


@pytest.mark.parametrize("field", ["payload", "proof", "asset", "digest"])
def test_binding_replay_rejects_tampering(field):
    asset = _proposal().to_dict()
    bound = bind_rtl_asset_to_source(asset, _source("req_ack_bug3"), design_id="heldout")
    bad = copy.deepcopy(bound)
    if field == "payload":
        bad["definition"]["action"]["payload"]["add_condition"] = "rd_req"
    elif field == "proof":
        bad["provenance"]["binding_evidence"]["source"] += " module extra; endmodule"
    elif field == "asset":
        bad["definition"]["binding_template"]["slot_roles"]["add_condition"] = "identifier_0"
    else:
        bad["provenance"]["binding_digest"] = "sha256:tampered"
    assert not verify_structural_binding(bad, asset)


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_fixture_answer_binding_cannot_gain_strict_authority(
        tmp_tehm, monkeypatch, legacy_receipt):
    conn, _, _ = tmp_tehm
    registered = register_asset_proposal(conn, _proposal())
    for status in ("shadow", "candidate"):
        set_asset_status(conn, asset_id=registered.asset_id,
                         target_scope=registered.target_scope, status=status)
    asset = get_asset(conn, registered.asset_id)
    bindings = [bind_rtl_asset_to_project(asset, PROJECTS / name)
                for name in ("req_ack_bug", "req_ack_bug2")]
    validation = {"static_valid": True, "independent_verifier": True,
                  "oracle_verdict": "PASS", "regression_verdict": "PASS", "errors": []}
    with monkeypatch.context() as old_code:
        if legacy_receipt:
            # Model an already-persisted v1 receipt from the old accepting gate.
            old_code.setattr("tehm.assets.lifecycle._binding_is_compatible",
                             lambda *_: True)
        authority = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            validation_receipts=[validation, validation], bindings=bindings,
            rollback_receipt={"verified": True})
    assert authority.eligible is legacy_receipt
    if not legacy_receipt:
        assert "compatibility_verified" in authority.missing
    assert verify_asset_authority(conn, authority)["eligible"] is False
    with pytest.raises(ValueError, match="not eligible"):
        promote_asset(conn, authority)
