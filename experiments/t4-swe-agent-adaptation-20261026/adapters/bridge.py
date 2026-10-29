"""Host-neutral connection to the unchanged, private TEHM runtime.

No model, host tool, verdict inference or authority promotion lives here.
The native host owns action execution and verification.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "code/memory"
sys.path.insert(0, str(CORE))

from contracts import RepairContext
from tehm.canonical.capture import ExecutionRecord
from tehm.verified_execution import require_verified_transition
from tehm_backend import TehmMemoryBackend

HOSTS = {"swe-agent"}


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(stable(value).encode()).hexdigest()


def routed_proposals(routing, proposals, candidates):
    bound = [p for p in proposals if p.applicability_status == "APPLICABLE"
             and (p.binding or {}).get("status") == "BOUND"]
    if routing.decision not in {"APPLY", "CONSIDER"} or routing.memory_budget < 1:
        return [], None
    if routing.decision == "APPLY" or routing.selected_rule_ids:
        selected = set(routing.selected_rule_ids)
        ids = {c.candidate_id: c.payload.get("rule_id") for c in candidates}
        bound = [p for p in bound if p.candidate_id in selected or ids.get(p.candidate_id) in selected]
    bound = bound[:1]  # the existing router authorizes at most one memory candidate
    direct = next((p for p in bound if routing.decision == "APPLY"
                   and (p.binding or {}).get("lifecycle_status") == "promoted"), None)
    return bound, direct


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind, **data):
        with self.path.open("a+") as out:
            fcntl.flock(out, fcntl.LOCK_EX)
            out.seek(0)
            count = sum(1 for _ in out)
            row = {"event": kind, "sequence": count + 1, "time": time.time(), **data}
            out.seek(0, 2)
            out.write(stable(row) + "\n")
            out.flush()
            os.fsync(out.fileno())
        return row


@dataclasses.dataclass(frozen=True)
class State:
    task_id: str
    lineage_id: str
    domain: str
    profile: str
    check: str
    reports: dict
    config: dict
    signature: dict

    def canonical(self):
        return {"config": self.config, "reports": self.reports,
                "failure_signature": self.signature,
                "repository_ref": self.task_id}

    def context(self):
        return RepairContext(design_id=self.task_id, platform=self.profile,
                             check=self.check, reports=self.reports, cfg=self.config,
                             symptom_signature=self.signature,
                             compatibility_profile=self.profile,
                             mechanism_signature={"mechanism_family": self.check,
                                                  "compatibility_profile": self.profile})


@dataclasses.dataclass(frozen=True)
class Verification:
    verdict: str
    oracle_type: str
    scope: str
    complete: bool
    evidence_path: str
    evidence_sha256: str
    before_measured: bool
    original_failure_removed: bool | None
    regressions: tuple[str, ...] = ()

    def validate(self, arm_root):
        if self.verdict not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError("invalid verifier verdict")
        path = Path(self.evidence_path).resolve()
        if not path.is_relative_to(arm_root.resolve()):
            raise ValueError("verification evidence outside private arm root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.evidence_sha256:
            raise ValueError("verification evidence changed")
        raw = json.loads(path.read_text())
        # A receipt is a native evaluator result, never an LLM's self-assessment.
        if raw.get("producer") != "native_evaluator" or raw.get("verdict") != self.verdict:
            raise ValueError("missing native evaluator evidence")
        if raw.get("oracle_type") != self.oracle_type or raw.get("scope") != self.scope:
            raise ValueError("verifier scope/oracle mismatch")
        if raw.get("complete") is not self.complete:
            raise ValueError("verifier completeness mismatch")
        for key, value in (("before_measured", self.before_measured),
                           ("original_failure_removed", self.original_failure_removed),
                           ("regressions", list(self.regressions))):
            if key in raw and raw[key] != value:
                raise ValueError("verifier effect metadata mismatch: " + key)

    def canonical(self):
        tiers = {"FORMAL": "F", "REGRESSION": "R", "DIFFERENTIAL_SIM": "R",
                 "TARGET_TEST": "T", "COMPILE": "H", "LINT": "H", "UNKNOWN": "H"}
        return {"verdict": self.verdict, "oracle_type": self.oracle_type,
                "scope": self.scope, "confidence_tier": tiers[self.oracle_type],
                "oracle_complete": self.complete,
                "evidence_refs": [self.evidence_path + "#sha256=" + self.evidence_sha256],
                "extractor_version": "table4-native-verifier-v1"}


class FullMemory:
    def __init__(self, host: str, *, arm="tehm", root: Path = ROOT):
        if host not in HOSTS or arm not in {"native", "tehm"}:
            raise ValueError("unknown host or arm")
        self.host, self.arm = host, arm
        requested = root.resolve() / "hosts" / host / arm
        self.arm_root = requested.resolve()
        if self.arm_root != requested or not self.arm_root.is_relative_to(root.resolve()):
            raise ValueError("arm storage escapes experiment")
        for name in ("memory", "artifacts", "episodes", "ledgers"):
            path = self.arm_root / name
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.resolve().is_relative_to(self.arm_root):
                raise ValueError("shared or escaping writable storage")
        database = self.arm_root / "memory/tehm.sqlite"
        if database.is_symlink() or (database.exists() and database.stat().st_nlink != 1):
            raise ValueError("shared database file")
        self.journal = Journal(self.arm_root / "ledgers/events.jsonl")
        self.backend = None if arm == "native" else TehmMemoryBackend(
            db_path=self.arm_root / "memory/tehm.sqlite",
            artifact_root=self.arm_root / "artifacts", read_only_eval=False)
        self._step = 0

    def consult(self, state: State):
        started, cpu = time.monotonic(), time.process_time()
        if self.backend is None:
            return {"mode": "native", "context": "", "action": None}
        query = self.backend.build_query(state.context())
        routing = self.backend.route_memory(query, no_memory_budget=1, memory_budget=1)
        candidates = self.backend.retrieve(query, limit=2)
        proposals = [p for c in candidates
                     if (p := self.backend.propose_activation(c, state.context())) is not None]
        bound, direct = routed_proposals(routing, proposals, candidates)
        advice = [{"candidate_id": p.candidate_id, "binding": p.binding,
                   "obligations": p.obligations, "causal_authority": False}
                  for p in bound]
        mode = "direct_reuse" if direct else "experience_assisted" if advice else "no_experience"
        result = {"mode": mode, "routing": routing.to_dict(),
                  "context": ("Historical evidence, not a current-task verdict or mandatory action:\n"
                              + stable(advice)) if advice else "",
                  "action": (direct.binding or {}).get("action") if direct else None,
                  "direct_proposal": {"candidate_id": direct.candidate_id,
                      "binding": direct.binding, "obligations": direct.obligations} if direct else None,
                  "selected_ids": [p.candidate_id for p in bound]}
        self.journal.append("memory_consult", task=state.task_id,
                            state_digest=digest(dataclasses.asdict(state)), **result,
                            wall_s=time.monotonic()-started, cpu_s=time.process_time()-cpu)
        return result

    def write(self, before: State, action: dict, after: State,
              verification: Verification, *, rolled_back: bool):
        if self.backend is None:
            raise ValueError("native arm cannot write TEHM")
        if before.task_id != after.task_id or before.domain != after.domain:
            raise ValueError("transition identity/domain mismatch")
        if type(rolled_back) is not bool:
            raise ValueError("explicit rollback marker required")
        verification.validate(self.arm_root)
        evidence = json.loads(Path(verification.evidence_path).read_text())
        if evidence.get("before_digest") != digest(before.canonical()) or evidence.get("after_digest") != digest(after.canonical()):
            raise ValueError("evaluator receipt is not bound to both states")
        if evidence.get("action_digest") != digest(action):
            raise ValueError("evaluator receipt is not bound to executed action")
        removed = ("UNKNOWN" if not verification.before_measured or verification.original_failure_removed is None
                   else "REMOVED" if verification.original_failure_removed else "PRESENT")
        record = ExecutionRecord.from_dict({
            "domain": before.domain, "before": before.canonical(), "action": action,
            "after": after.canonical(), "lineage_id": before.lineage_id,
            "project_id": before.task_id, "design_id": before.task_id,
            "observation_delta": {"original_failure": removed,
                "experiment_kind": "REPAIR" if verification.before_measured else "UNKNOWN",
                "rolled_back": "true" if rolled_back else "false",
                "created_regressions": list(verification.regressions)},
            "verification": verification.canonical(),
            "episode": {"mechanism_family": before.check, "lineage_id": before.lineage_id,
                        "step_index": self._step}})
        started, cpu = time.monotonic(), time.process_time()
        receipt = self.backend.ingest_execution(record)
        conn, _ = self.backend._open()
        try:
            require_verified_transition(conn, receipt.transition_id)
        except ValueError as exc:
            admission = {"eligible": False, "reason": str(exc)}
        else:
            admission = {"eligible": True}
        # Same admission/rebuild path as the existing memory; no local promotion.
        rebuild = self.backend.rebuild()
        self._step += 1
        row = {"transition_id": receipt.transition_id, "outcome": receipt.outcome,
               "admission": admission, "rebuild_ok": rebuild.ok, "rolled_back": rolled_back}
        self.journal.append("memory_write", task=before.task_id, **row,
                            wall_s=time.monotonic()-started, cpu_s=time.process_time()-cpu)
        return row

    def close(self):
        if self.backend:
            self.backend.close()
