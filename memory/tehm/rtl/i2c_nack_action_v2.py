"""Core shadow action over the frozen I2C NACK v2 source-only binder.

Only the frozen v2 ``bind``/``apply`` (commit 317c8a3) decide the edit; this module
adds a strict multi-file closure transport so the single-string core pipeline can
carry the freecores four-role closure. It grants no TRAIN or Memory authority.
"""
from collections.abc import Mapping
import copy
import hashlib
from pathlib import Path
import re

from tehm.evaluation import research_r5_i2c_nack_binding_dev as _v1
from tehm.evaluation import research_r5_i2c_nack_binding_dev_v2 as binder

DOMAIN = "rtl.I2C_NACK_STATUS_LATCH_V2"
PROFILE = binder.PROFILE
PAYLOAD_KEYS = frozenset({"domain", "compatibility_profile", "public_context",
                          "source_sha256", "action_digest", "binding_contract"})
FROZEN_SHA256 = {
    "research_r5_i2c_nack_binding_dev_v2.py": "d0b594a767bace01886b247636d875721a95643130f7678ea6a93c798d4b88e7",
    "research_r5_i2c_nack_binding_dev.py": "bb8f459b38c4797de17b62655a9e13b26e16fe32417d36ab5140a6d6624963ff",
}
for _module in (binder, _v1):
    _name = Path(_module.__file__).name
    if hashlib.sha256(Path(_module.__file__).read_bytes()).hexdigest() != FROZEN_SHA256[_name]:
        raise ImportError("frozen I2C v2 binder drift: " + _name)

ASSET = {"binding_template": binder.TEMPLATE}
HEADER = re.compile(r"// @tehm-closure-v1 role=([a-z_]+) bytes=([0-9]+)\n")


def encode_closure(sources: Mapping) -> str:
    """Canonical transport text for a role->text closure (fixed role order)."""
    if not isinstance(sources, Mapping) or set(sources) != set(binder.ROLES):
        raise ValueError("I2C v2 closure requires exactly the binder roles")
    parts = []
    for role in binder.ROLES:
        text = sources[role]
        if not isinstance(text, str):
            raise ValueError("I2C v2 closure role text must be str")
        parts.append("// @tehm-closure-v1 role=%s bytes=%d\n%s" % (role, len(text.encode("utf-8")), text))
    return "".join(parts)


def decode_closure(text: str) -> dict:
    """Exact inverse of encode_closure; any deviation is rejected."""
    if not isinstance(text, str):
        raise ValueError("I2C v2 closure must be str")
    data, offset, out = text.encode("utf-8"), 0, {}
    for role in binder.ROLES:
        match = HEADER.match(data[offset:].decode("utf-8", errors="strict")) if offset < len(data) else None
        if match is None or match[1] != role:
            raise ValueError("I2C v2 closure role order/header mismatch")
        offset += len(match[0].encode("utf-8"))
        size = int(match[2])
        if match[2] != str(size) or offset + size > len(data):
            raise ValueError("I2C v2 closure byte length mismatch")
        out[role] = data[offset:offset + size].decode("utf-8", errors="strict")
        offset += size
    if offset != len(data):
        raise ValueError("I2C v2 closure trailing data")
    if encode_closure(out) != text:
        raise ValueError("I2C v2 closure is not canonical")
    return out


def _binder_input(source: str, public_context: Mapping):
    if not isinstance(source, str) or not isinstance(public_context, Mapping):
        raise ValueError("I2C v2 requires str source and explicit public context")
    if public_context.get("interface") == binder.INTERFACE:
        return decode_closure(source)
    if source.startswith("// @tehm-closure-v1 "):
        raise ValueError("I2C v2 closure supplied for a single-file interface")
    return source


def source_binding(source, public_context):
    binding = binder.bind(ASSET, _binder_input(source, public_context), public_context)
    if binding.get("status") != "BOUND":
        raise ValueError("I2C v2 source binding rejected: %s/%s" % (binding.get("status"), binding.get("reason")))
    return binding


def payload_from_source_i2c_v2(source, public_context):
    binding = source_binding(source, public_context)
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "public_context": copy.deepcopy(dict(public_context)),
            "source_sha256": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "action_digest": binding["witness"]["action_digest"],
            "binding_contract": binder.CONTRACT}


