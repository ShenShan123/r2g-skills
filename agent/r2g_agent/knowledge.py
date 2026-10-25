"""Per-user memory for the r2g agent.

The agent defaults to TEHM at R2G_HOME/memory; legacy and none remain explicit
alternatives. Legacy stores are seeded additively from the shipped baseline.
TEHM captures and rebuilds only its own execution evidence. No legacy rows are
imported as TEHM authority. Every skill subprocess receives the same selection.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from .paths import r2g_home, skills_dir, repo_root

_LOCK = threading.RLock()
_BACKEND_NAME: str | None = None


def backend_name() -> str:
    """The agent uses TEHM by default; one authority is locked for its process."""
    global _BACKEND_NAME
    name = os.environ.get("R2G_MEMORY_BACKEND", "tehm").strip().lower()
    if name not in {"none", "legacy", "tehm"}:
        raise ValueError(f"invalid R2G_MEMORY_BACKEND={name!r}; refusing silent fallback")
    with _LOCK:
        if _BACKEND_NAME is not None and name != _BACKEND_NAME:
            raise RuntimeError(f"memory backend already opened as {_BACKEND_NAME!r}; refusing {name!r}")
        _BACKEND_NAME = name
    return name


def tehm_paths() -> dict:
    root = r2g_home() / "memory"
    return {"db": Path(os.environ.get("TEHM_DB", root / "tehm.sqlite")).expanduser().resolve(),
            "artifacts": Path(os.environ.get("TEHM_ARTIFACTS_ROOT", root / "artifacts")).expanduser().resolve()}



def shipped_dir() -> Path:
    return skills_dir() / "signoff-loop" / "knowledge"


def shipped_store() -> tuple[Path, Path]:
    """(knowledge.sqlite, heuristics.json) as shipped in the checkout."""
    return shipped_dir() / "knowledge.sqlite", shipped_dir() / "heuristics.json"


def store_dir() -> Path:
    d = r2g_home() / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def paths() -> dict:
    d = store_dir()
    return {"db": d / "knowledge.sqlite", "heuristics": d / "heuristics.json",
            "journal": d / "journal.sqlite", "marker": d / "shipped.sha256"}


def env() -> dict:
    """Pass the selected backend and user-local stores to every skill process."""
    name = backend_name()
    if name == "none":
        return {"R2G_MEMORY_BACKEND": name}
    if name == "tehm":
        p = tehm_paths()
        return {"R2G_MEMORY_BACKEND": name, "TEHM_DB": str(p["db"]),
                "TEHM_ARTIFACTS_ROOT": str(p["artifacts"])}
    p = paths()
    return {"R2G_MEMORY_BACKEND": name,
            "R2G_KNOWLEDGE_DB": str(p["db"]), "R2G_HEURISTICS_PATH": str(p["heuristics"]),
            "R2G_JOURNAL_DB": str(p["journal"])}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env())
    return subprocess.run(["python3", str(shipped_dir() / script), *args], cwd=shipped_dir(),
                          env=e, capture_output=True, text=True, timeout=1800)


def learn() -> tuple[bool, str]:
    """Rebuild the user's heuristics.json from the user's store."""
    name = backend_name()
    if name != "legacy":
        # runtime_router.ingest_project already rebuilds TEHM from its own evidence.
        return True, "disabled" if name == "none" else "TEHM rebuilt during ingest"
    p = paths()
    with _LOCK:
        r = _run("learn_heuristics.py", "--db", str(p["db"]), "--out", str(p["heuristics"]))
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, lines[-1] if lines else ""


def ensure() -> dict:
    """Seed or update the user's store. Returns what happened, for the UI/log."""
    name = backend_name()
    if name == "none":
        return {"action": "disabled", "backend": name}
    if name == "tehm":
        p = tehm_paths()
        e = {**os.environ, **env()}
        e["PYTHONPATH"] = str(repo_root() / "memory") + os.pathsep + e.get("PYTHONPATH", "")
        read_only = os.environ.get("R2G_MEMORY_READ_ONLY_EVAL", "0") == "1"
        command = [sys.executable, "-m", "tehm.cli", "init-db"]
        if read_only:
            command = [sys.executable, "-c",
                       "from pathlib import Path; import sys; from tehm import db; "
                       "c=db.connect_read_only(Path(sys.argv[1])); c.close()", str(p["db"])]
        with _LOCK:
            r = subprocess.run(command, cwd=repo_root(), env=e,
                               capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError("TEHM initialization failed: " + (r.stdout + r.stderr)[-800:])
        return {"action": "ready", "backend": name, "db": str(p["db"]), "read_only": read_only}
    p = paths()
    shipped, shipped_heur = shipped_store()
    if not shipped.is_file():
        with _LOCK:
            if p["db"].is_file():
                return {"action": "up_to_date", "backend": "legacy", "db": str(p["db"])}
            command = [sys.executable, "-c",
                       "import sys, knowledge_db; "
                       "c=knowledge_db.connect(sys.argv[1]); knowledge_db.ensure_schema(c); c.close()",
                       str(p["db"])]
            r = subprocess.run(command, cwd=shipped_dir(), env={**os.environ, **env()},
                               capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError("Legacy initialization failed: " + (r.stdout + r.stderr)[-800:])
        return {"action": "initialized", "backend": "legacy", "db": str(p["db"])}
    digest = _sha(shipped)
    with _LOCK:
        if not p["db"].is_file():
            src = sqlite3.connect("file:%s?mode=ro" % shipped, uri=True)
            tmp = p["db"].with_suffix(".sqlite.tmp")
            tmp.unlink(missing_ok=True)
            dst = sqlite3.connect(tmp)
            src.backup(dst)          # a consistent copy even if the file is mid-write
            dst.close()
            src.close()
            os.replace(tmp, p["db"])
            if shipped_heur.is_file():
                shutil.copy2(shipped_heur, p["heuristics"])
            p["marker"].write_text(digest + "\n")
            return {"action": "seeded", "db": str(p["db"])}
        if p["marker"].is_file() and p["marker"].read_text().strip() == digest:
            return {"action": "up_to_date", "db": str(p["db"])}
        r = _run("knowledge_sync.py", "merge", "--from-db", str(shipped), "--db", str(p["db"]))
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            # marker untouched: retried next start, and the user's store is as it was
            return {"action": "merge_refused", "db": str(p["db"]), "detail": out[-800:]}
        p["marker"].write_text(digest + "\n")
    ok, msg = learn()
    return {"action": "merged", "db": str(p["db"]), "learn": msg if ok else "learn failed: " + msg}


def summary() -> dict:
    name = backend_name()
    if name == "none":
        return {"backend": name, "enabled": False, "db": None, "runs": 0, "heuristics": False}
    if name == "tehm":
        p = tehm_paths()
        return {"backend": name, "db": str(p["db"]), "initialized": p["db"].is_file(),
                "runs": None, "heuristics": False}
    p = paths()
    try:
        c = sqlite3.connect("file:%s?mode=ro" % p["db"], uri=True)
        n = c.execute("select count(*) from runs").fetchone()[0]
        c.close()
    except sqlite3.Error:
        n = None
    return {"backend": name, "db": str(p["db"]), "runs": n, "heuristics": p["heuristics"].is_file()}


def lookup(query: str, *, context: dict | None = None, limit: int = 5) -> dict:
    """Keep legacy documentation matches separate from typed action proposals."""
    doc = skills_dir() / "signoff-loop" / "references" / "failure-patterns.md"
    sections = re.split(r"(?m)^(?=#{2,4} )", doc.read_text(errors="replace"))
    words = [word.lower() for word in query.split()]
    hits = [(sum(section.lower().count(word) for word in words), section)
            for section in sections if words and all(word in section.lower() for word in words)]
    hits.sort(key=lambda item: -item[0])
    return {"matches": [{"heading": section.splitlines()[0].lstrip("# "), "excerpt": section[:1500]}
                        for _, section in hits[:3]], "total": len(hits),
            "reference_kind": "documentation_only", "memory": lookup_actions(context or {}, limit=limit)}


def lookup_actions(context: dict, *, limit: int = 5) -> dict:
    """Consult existing TEHM retrieval/binding without activating or writing.

    The controller supplies RepairContext fields from its current task evidence,
    plus target_scope and source_snapshot_id. A model's prose is not evidence.
    Returned actions are proposals: execution must re-check source snapshot,
    rule status_version, policy, budget and the independent verification plan.
    expected_rule_versions can pin a previously selected rule on re-consultation.
    """
    name = backend_name()
    result = {"schema_version": 1, "backend": name, "decision": "ABSTAIN",
              "execution_authorized": False, "candidates": [], "reasons": []}
    if name != "tehm":
        result.update(decision="NO_SKILL", reasons=["typed_actions_unavailable_for_backend"])
        return result
    if not isinstance(context, dict):
        raise ValueError("memory context must be an object")
    required = ("project_dir", "design_id", "platform", "check", "target_scope",
                "source_snapshot_id", "compatibility_profile")
    missing = [key for key in required if not isinstance(context.get(key), str) or not context[key].strip()]
    if missing:
        result.update(reasons=["missing_context:" + key for key in missing])
        return result
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("memory lookup limit must be between 1 and 20")
    versions = context.get("expected_rule_versions") or {}
    if not isinstance(versions, dict) or any(not isinstance(key, str) or type(value) is not int or value < 1
                                             for key, value in versions.items()):
        raise ValueError("expected_rule_versions must map rule IDs to positive integers")
    memory_path = str(repo_root() / "memory")
    if memory_path not in sys.path:
        sys.path.insert(0, memory_path)
    from contracts import RepairContext
    from tehm import db as tehm_db
    from tehm.ids import stable_dumps
    from tehm.lifecycle.rule_status import get_status
    from tehm.retrieval.index import build_index
    from tehm.retrieval.recall import high_recall
    from tehm.retrieval.symbolic_filter import apply_symbolic_filter
    from tehm_backend import TehmMemoryBackend, SCHEMA_VERSION

    fields = {key: context[key] for key in RepairContext.__dataclass_fields__ if key in context}
    fields["project_dir"] = Path(fields["project_dir"])
    for key in ("reports", "cfg", "situation", "structural_graph", "symptom_signature", "mechanism_signature"):
        if fields.get(key) is not None and not isinstance(fields[key], dict):
            raise ValueError("memory context " + key + " must be an object")
    if fields.get("situation"):
        # UNKNOWN is missing evidence, never a concrete predicate mismatch.
        fields["situation"] = {key: None if isinstance(value, str) and value.upper() == "UNKNOWN" else value
                               for key, value in fields["situation"].items()}
    current = RepairContext(**fields)
    paths_ = tehm_paths()
    backend = TehmMemoryBackend(db_path=paths_["db"], artifact_root=paths_["artifacts"],
                                read_only_eval=True, trial_candidates=False, immutable_snapshot=False)
    try:
        conn, _ = backend._open()
        conn.execute("BEGIN")  # lifecycle, evidence and proposal share one read view
        query = backend.build_query(current)
        result["binding"] = {"target_scope": context["target_scope"],
                             "source_snapshot_id": context["source_snapshot_id"],
                             "compatibility_profile": context["compatibility_profile"],
                             "context_digest": "sha256:" + hashlib.sha256(
                                 stable_dumps(current.to_dict()).encode()).hexdigest(),
                             "memory_schema_version": SCHEMA_VERSION,
                             "memory_read_watermark": tehm_db.read_watermark(conn)}
        index = build_index(conn, lifecycle_statuses=frozenset({"promoted"}))
        stale_versions = []
        for rule_id, expected in versions.items():
            status = get_status(conn, rule_id=rule_id, target_scope=context["target_scope"])
            if not status or status["status"] != "promoted" or status["status_version"] != expected:
                stale_versions.append(rule_id)
        result["stale_rule_versions"] = sorted(stale_versions)
        retrieved = {candidate.payload["rule_id"]: candidate
                     for candidate in backend.retrieve(query, limit=limit)}
        result["rejected_rules"] = [{"memory_id": key, "reason": reason}
                                     for key, reason in sorted(index.rejected.items())[:limit]]
        for recalled in high_recall(index, query, limit=limit * 3):
            rule, rule_id = recalled.rule, recalled.rule_id
            status = get_status(conn, rule_id=rule_id, target_scope=context["target_scope"])
            profile = rule["before_pattern"].get("compatibility_profile") or rule["context_predicates"].get("compatibility_profile")
            applicability = apply_symbolic_filter(rule, query)
            item = {"memory_id": rule_id, "source": "tehm_rule", "applicability": applicability,
                    "target_scope": context["target_scope"], "lifecycle": status,
                    "conditions": {"hard_preconditions": rule["hard_preconditions"],
                                   "compatibility_profile": profile,
                                   "target_check": rule["before_pattern"].get("target_check")},
                    "source_evidence": {"episodes": rule["source_episodes"]},
                    "action": None, "reasons": [], "execution_authorized": False}
            if not status or status["status"] != "promoted":
                item["reasons"].append("not_promoted_in_target_scope")
            if stale_versions:
                item["reasons"].append("prior_decision_version_stale")
            if rule_id in versions and (not status or status["status_version"] != versions[rule_id]):
                item["reasons"].append("rule_status_version_changed")
            if not profile or profile.startswith("$H"):
                item["applicability"] = "UNRESOLVED"
                item["reasons"].append("rule_compatibility_profile_unresolved")
            elif profile != context["compatibility_profile"]:
                item["reasons"].append("compatibility_profile_mismatch")
            if applicability != "APPLICABLE":
                item["reasons"].append("symbolic_" + applicability.lower())
            if not rule["source_episodes"]:
                item["reasons"].append("source_evidence_missing")
            candidate = retrieved.get(rule_id)
            if candidate is None:
                item["reasons"].append("not_selected_by_retrieval")
            if not item["reasons"]:
                proposal = backend.propose_activation(candidate, current, target_scope=context["target_scope"])
                if proposal is None:
                    item["reasons"].append("activation_proposal_refused")
                else:
                    item["proposal"] = {"activation_id": proposal.activation_id,
                                        "binding": proposal.binding, "obligations": proposal.obligations,
                                        "obligation_coverage": proposal.obligation_coverage}
                    action = (proposal.binding or {}).get("action") or {}
                    if (proposal.binding or {}).get("status") != "BOUND":
                        item["applicability"] = "UNRESOLVED"
                        item["reasons"].append("binding_unresolved")
                    elif (action.get("payload") or {}).get("unresolved_knobs"):
                        item["applicability"] = "UNRESOLVED"
                        item["reasons"].append("action_values_unresolved")
                    elif not action:
                        item["reasons"].append("no_instantiated_action")
                    else:
                        item["action"] = action
            result["candidates"].append(item)
        if stale_versions:
            result["reasons"] = ["prior_decision_version_stale"]
        elif any(item["action"] is not None for item in result["candidates"]):
            result["decision"] = "CONSIDER"
        elif any(item["applicability"] == "UNRESOLVED" for item in result["candidates"]) or index.rejected:
            result["reasons"] = ["applicability_or_authority_unresolved"]
        else:
            result.update(decision="NO_SKILL", reasons=["no_applicable_promoted_action_in_scope"])
        result["decision_digest"] = "sha256:" + hashlib.sha256(stable_dumps(result).encode()).hexdigest()
        return result
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        return {"schema_version": 1, "backend": name, "decision": "ABSTAIN",
                "execution_authorized": False, "candidates": [],
                "reasons": ["memory_unavailable:" + type(exc).__name__]}
    finally:
        backend.close()
