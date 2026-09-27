"""Shared R5 S2 proposal/action bridge; no provider, oracle or Memory writes.

The trusted runner supplies authenticated C4 receipts; hashes are identity
checks, not credentials. A source-only worker executes minimal action inputs.
Memory authority is distinct from the shared cold-start v7 capability.
"""
from __future__ import annotations

import copy
import json

from . import research_r5_s2_controller as c
from . import research_r5_s2_memory as m

SCHEMA = "tehm-r5-rtl-action-proposal-v2"
ACTION_NAMES = ("no_action", "replace_sources", "transform_v7", "apply_memory_asset")
SYSTEM_PROMPT = """Propose one RTL repair within the declared source closure.
Use only the supplied buggy source, public specification, feedback and Memory.
Do not obtain clean/reference sources, tests, expected values or mutation data.
Return one JSON object with exactly schema, base_digest, action, arguments.
schema is tehm-r5-rtl-action-proposal-v2; copy the current base_digest exactly.
Actions available to every policy:
- no_action: arguments is {}.
- replace_sources: arguments is {\"edits\": [{\"path\": declared path, \"content\": complete replacement text}]}.
- transform_v7: arguments is {}; invoke the common source-only skid payload primitive.
- apply_memory_asset: arguments is {\"alias\": \"memory_asset_1\"}; requires that alias in Memory.
The generic v7 primitive and source replacement do not require Memory.
An asset alias does not imply functional correctness. Do not submit payloads,
binding slots, commands or verdicts. The independent evaluator owns success.
Unsupported or ambiguous binding is a legitimate rejection, not a repair.
"""


def prompt(sources: dict, public: dict) -> dict:
    """No private receipt parameter; no policy-specific prompt or action catalog."""
    before = c.source_digest(sources)
    if (public.get("schema") != "tehm-r5-s2-memory-public-c4-v1"
            or public.get("context_projection_ready") is not True
            or public.get("agent_ready") is not False
            or public.get("action_execution_authorized") is not False):
        raise c.ControllerError("unreviewed public packet")
    if list(sources) != ["rtl/axis_skid.sv"]:
        raise c.ControllerError("C5 source closure outside declared DEV scope")
    if c.sha(sources["rtl/axis_skid.sv"].encode()) != public["task"]["source_sha256"]:
        raise c.ControllerError("stale public context")
    return {"system": SYSTEM_PROMPT, "user": {"base_digest": before,
        "sources": copy.deepcopy(sources), "task": copy.deepcopy(public["task"]),
        "memory": copy.deepcopy(public["memory"])}}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise c.ControllerError("duplicate proposal key")
        result[key] = value
    return result


def parse(sources: dict, response: str) -> dict:
    if not isinstance(response, str) or len(response.encode()) > c.MAX_RESPONSE_BYTES:
        raise c.ControllerError("proposal byte bound")
    try:
        value = json.loads(response, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(c.ControllerError("nonfinite JSON")))
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise c.ControllerError("malformed proposal") from exc
    if type(value) is not dict or set(value) != {"schema", "base_digest", "action", "arguments"}:
        raise c.ControllerError("unexpected proposal fields")
    if value["schema"] != SCHEMA or value["base_digest"] != c.source_digest(sources):
        raise c.ControllerError("stale source or schema")
    action, args = value["action"], value["arguments"]
    if type(action) is not str or action not in ACTION_NAMES or type(args) is not dict:
        raise c.ControllerError("unknown action or arguments")
    expected = {"edits"} if action == "replace_sources" else {"alias"} if action == "apply_memory_asset" else set()
    if set(args) != expected or (action == "apply_memory_asset" and args["alias"] != "memory_asset_1"):
        raise c.ControllerError("action argument schema")
    if action == "replace_sources":
        c.candidate_from_proposal(sources, json.dumps({"schema": c.PROPOSAL_SCHEMA,
            "base_digest": value["base_digest"], "action": action, "edits": args["edits"]}))
    return value


def handoff(sources: dict, response: str, public: dict, private: dict | None) -> dict:
    """Trusted runner boundary. Never accept private authority from a model."""
    prompt(sources, public)
    proposal = parse(sources, response)
    request = {"schema": "tehm-r5-source-only-action-c5-v1",
        "base_digest": proposal["base_digest"], "action": proposal["action"],
        "parameters": copy.deepcopy(public["task"]["parameters"]),
        "arguments": copy.deepcopy(proposal["arguments"])}
    if proposal["action"] != "apply_memory_asset":
        return request
    if (type(private) is not dict or private.get("schema") != "tehm-r5-s2-memory-private-c4-v1"
            or public.get("policy") != "tehm" or private.get("policy") != "tehm"
            or private.get("public_digest") != m.digest(public)
            or private.get("task_digest") != m.digest(public["task"])
            or private.get("bundle_digest") != m.BUNDLE):
        raise c.ControllerError("missing or mismatched trusted Memory receipt")
    try:
        from tehm.assets.skid_binding_v7 import _template
        from tehm.rtl.skid_payload_action_v7 import payload_from_source_v7
        selection = private["selection"]
        assets = selection["assets"]
        selected = selection["receipt"]["selected_asset_ids"]
        if (selection["receipt"]["decision"] != "SELECT" or len(assets) != 1
                or selected != [assets[0]["asset_id"]]
                or public["memory"]["selection"] != "SELECT"
                or public["memory"]["entries"] != [{"alias": "memory_asset_1", "binding_template": _template()}]
                or assets[0]["definition"]["binding_template"] != _template()):
            raise c.ControllerError("selected asset identity/template mismatch")
        payload = assets[0]["definition"]["action"]["payload"]
        if payload != payload_from_source_v7(sources["rtl/axis_skid.sv"], request["parameters"]):
            raise c.ControllerError("stale or tampered bound payload")
        request["arguments"] = {"payload": copy.deepcopy(payload)}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise c.ControllerError("invalid selected asset handoff") from exc
    return request


def execute(sources: dict, request: dict) -> tuple[dict, dict]:
    """Source-only worker: no files, oracle, feedback or Memory queries."""
    if (type(request) is not dict or set(request) != {
            "schema", "base_digest", "action", "parameters", "arguments"}
            or request["schema"] != "tehm-r5-source-only-action-c5-v1"
            or request["base_digest"] != c.source_digest(sources)
            or list(sources) != ["rtl/axis_skid.sv"]):
        raise c.ControllerError("stale or malformed worker request")
    action, args = request["action"], request["arguments"]
    if type(action) is not str or action not in ACTION_NAMES or type(args) is not dict:
        raise c.ControllerError("invalid worker action")
    text = sources["rtl/axis_skid.sv"]
    binding = None
    if action in {"transform_v7", "apply_memory_asset"}:
        from tehm.rtl.skid_payload_action_v7 import payload_from_source_v7, apply_skid_payload_action_v7
        if set(args) != (set() if action == "transform_v7" else {"payload"}):
            raise c.ControllerError("invalid source-only arguments")
        try:
            payload = payload_from_source_v7(text, request["parameters"])
            if action == "apply_memory_asset" and args["payload"] != payload:
                raise c.ControllerError("asset payload not derived from this source")
            edited, binding = apply_skid_payload_action_v7(text, payload)
        except ValueError as exc:
            if isinstance(exc, c.ControllerError):
                raise
            return dict(sources), {"status": "REJECTED", "before_digest": c.source_digest(sources),
                "after_digest": c.source_digest(sources), "action": action,
                "functional_verdict": "NOT_EVALUATED", "reason": "SOURCE_BINDING_REJECTED"}
        edits = [{"path": "rtl/axis_skid.sv", "content": edited}]
        primitive = "replace_sources"
    else:
        if set(args) != ({"edits"} if action == "replace_sources" else set()):
            raise c.ControllerError("invalid edit/no-action arguments")
        edits, primitive = args.get("edits", []), action
    candidate, receipt = c.candidate_from_proposal(sources, json.dumps({
        "schema": c.PROPOSAL_SCHEMA, "base_digest": request["base_digest"],
        "action": primitive, "edits": edits}))
    receipt.update(action=action, request_digest=c.sha(c.canonical(request)),
                   source_only_binding=binding, functional_verdict="NOT_EVALUATED")
    return candidate, receipt
