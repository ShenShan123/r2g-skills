"""Lossless JSON projection of host trajectory messages, not repr-as-evidence."""
import dataclasses
from pathlib import Path


def native_json(value):
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if isinstance(value, dict):
        return {str(k): native_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native_json(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return native_json(dataclasses.asdict(value))
    if value.__class__.__module__.startswith(("langchain", "pydantic")):
        if hasattr(value, "model_dump"):
            fields = value.model_dump()
        elif hasattr(value, "dict"):
            fields = value.dict()
        else:
            raise TypeError("unsupported host trajectory object: " + type(value).__name__)
        return {"host_object_type": value.__class__.__module__ + "." + value.__class__.__name__,
                "fields": native_json(fields)}
    raise TypeError("unsupported trajectory object: " + type(value).__name__)


def write_episode(work, record):
    import json
    # First persist independent scalar status/accounting. A rich trajectory
    # conversion failure must never erase the terminal or the ledger summary.
    core = {k: v for k, v in record.items() if k != "native_result"}
    (work / "terminal-accounting.json").write_text(json.dumps(native_json(core), sort_keys=True)+"\n")
    payload = native_json(record)
    (work / "episode.json").write_text(json.dumps(payload, sort_keys=True)+"\n")
