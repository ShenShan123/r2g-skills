"""Cross-host first-batch accounting gate. This never changes raw usage."""
import json
import hashlib
from pathlib import Path

from bridge import ROOT, digest


def inspect(root=ROOT, authorization=None):
    root = Path(root)
    if authorization is None:
        candidates = [root / "inputs" / name for name in (
            "continuation-authorization-c3.json", "continuation-authorization-c2.json",
            "continuation-authorization-20261026.json")]
        authorization = next((p for p in candidates if p.exists()), candidates[-1])
    authorization = Path(authorization)
    acknowledgements = {}
    if authorization.exists():
        auth = json.loads(authorization.read_text())
        if auth.get("authorized") is not True or auth.get("no_episode_reruns") is not True:
            raise ValueError("invalid continuation authorization")
        preserved = {**auth.get("preserve_episode_sha256", {}), **auth.get("preserve_files_sha256", {})}
        parent = auth.get("prior_authorization")
        if parent:
            preserved[parent["path"]] = parent["sha256"]
        for name, sha in preserved.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("preserved episode changed")
        acknowledgements = {(e["ledger"], e["request_local_id"]): e["end_event_sha256"]
                            for e in auth.get("acknowledged_unknowns", [])}
    paths = sorted((Path(root) / "hosts").glob("*/*/episodes/*/provider/requests.jsonl"))
    known_tokens = requests = 0
    unknown, open_requests = [], []
    for path in paths:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        starts = {e["request_local_id"]: e for e in events if e["event"] == "request_started"}
        ends = {e["request_local_id"]: e for e in events if e["event"] == "request_ended"}
        requests += len(starts)
        for ident in sorted(starts):
            item = {"ledger": str(path.relative_to(root)), "request_local_id": ident}
            if ident not in ends:
                open_requests.append(item)
            elif ends[ident].get("usage") == "UNKNOWN":
                item["acknowledged_for_continuation"] = (
                    acknowledgements.get((item["ledger"], ident)) == digest(ends[ident]))
                unknown.append(item)
            elif isinstance(ends[ident].get("usage"), dict):
                known_tokens += ends[ident]["usage"]["total_tokens"]
            else:
                raise ValueError("invalid accounting record")
    return {"online_requests": requests, "known_tokens": known_tokens,
            "unknown_requests": unknown, "open_requests": open_requests,
            "new_paid_admissions_allowed": not (any(not e["acknowledged_for_continuation"] for e in unknown) or open_requests),
            "billing_cost": None, "usage_complete": not (unknown or open_requests)}


def require_clear(root=ROOT):
    status = inspect(root)
    if not status["new_paid_admissions_allowed"]:
        raise RuntimeError("first-batch UNKNOWN/open usage gate: no new paid episode admitted")
    return status


if __name__ == "__main__":
    print(json.dumps(inspect(), indent=2))
