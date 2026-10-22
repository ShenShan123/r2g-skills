#!/usr/bin/env python3
"""T2 source lock (user decision D-a, 2026-10-17).

A lineage group that enters the T2 qualification shortlist is isolated from the
learning-curve candidate pool at once (TEMP_LOCK), and fixed as TEST at registration
(REGISTERED_TEST). The lock is append-only: there is no unlock. The next learning-curve
preflight must call `locked_groups()` and exclude every group it returns, and `check`
reports any conflict with a roster the learning curve already used.

  python3 t2_source_lock.py lock  --lock <file> --entries <json list> --reason ... --by ...
  python3 t2_source_lock.py check --lock <file> --against <roster.json|jsonl> [...]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from pathlib import Path

STATUSES = ("TEMP_LOCK", "REGISTERED_TEST")
_ID = re.compile(r"\bC\d{5}\b")
_GROUP = re.compile(r"\blineage_[0-9a-f]{20}\b")


def load(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"schema": "t2-source-lock-v1", "entries": []}
    return json.loads(p.read_text())


def _save(path, data: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def lock(path, entries: list, *, reason: str, by: str) -> dict:
    data = load(path)
    have = {e["handoff_id"] for e in data["entries"]} | {e["group_id"] for e in data["entries"]}
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for e in entries:
        if e["handoff_id"] in have or e["group_id"] in have:
            raise ValueError(f"already locked: {e['handoff_id']} / {e['group_id']} (the lock is append-only)")
        data["entries"].append({**e, "status": "TEMP_LOCK", "locked_at": now, "reason": reason, "by": by,
                                "registration": None})
        have |= {e["handoff_id"], e["group_id"]}
    _save(path, data)
    return data


def register(path, handoff_ids: list, *, registration: str) -> dict:
    data = load(path)
    by_id = {e["handoff_id"]: e for e in data["entries"]}
    missing = [h for h in handoff_ids if h not in by_id]
    if missing:
        raise ValueError(f"not locked, cannot register: {missing}")
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for h in handoff_ids:
        by_id[h].update(status="REGISTERED_TEST", registration=registration, registered_at=now)
    _save(path, data)
    return data


def locked_groups(path) -> set:
    return {e["group_id"] for e in load(path)["entries"]}


def _walk(obj, ids: set, groups: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in ("handoff_id", "id", "design_id"):
                ids.add(v)
            elif isinstance(v, str) and k in ("group_id", "lineage", "lineage_id"):
                groups.add(v)
            else:
                _walk(v, ids, groups)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, ids, groups)


def _roster_ids(roster) -> tuple[set, set]:
    """Every handoff id and lineage group a roster names: structurally (JSON / JSONL keys)
    and textually (C##### and lineage_<hex> tokens), so a roster of any shape is covered."""
    p = Path(roster)
    if not p.exists():
        raise FileNotFoundError(f"roster not found: {p} (a missing roster is never a clean result)")
    text = p.read_text(errors="ignore")
    ids, groups = set(_ID.findall(text)), set(_GROUP.findall(text))
    for chunk in ([text] if text.lstrip().startswith(("{", "[")) else text.splitlines()):
        try:
            _walk(json.loads(chunk), ids, groups)
        except ValueError:
            if chunk is text:
                for line in text.splitlines():
                    try:
                        _walk(json.loads(line), ids, groups)
                    except ValueError:
                        pass
    return ids, groups


def conflicts(lock_data: dict, roster) -> list:
    ids, groups = _roster_ids(roster)
    out = []
    for e in lock_data["entries"]:
        if e["handoff_id"] in ids:
            out.append({**e, "matched_by": "handoff_id", "roster": str(roster)})
        elif e["group_id"] in groups:
            out.append({**e, "matched_by": "group_id", "roster": str(roster)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("lock")
    a.add_argument("--lock", type=Path, required=True)
    a.add_argument("--entries", type=Path, required=True, help="JSON list of {handoff_id, group_id, ...}")
    a.add_argument("--reason", required=True)
    a.add_argument("--by", required=True)
    c = sub.add_parser("check")
    c.add_argument("--lock", type=Path, required=True)
    c.add_argument("--against", type=Path, nargs="+", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "lock":
        d = lock(args.lock, json.loads(args.entries.read_text()), reason=args.reason, by=args.by)
        print(json.dumps({"locked": len(d["entries"])}))
        return 0
    found = [x for r in args.against for x in conflicts(load(args.lock), r)]
    print(json.dumps({"conflicts": found}, indent=1))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
