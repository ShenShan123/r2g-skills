"""Shared RTL proposal boundary for the future R5 S2 controller.

No provider or simulator is invoked here. Budget reservations are not measured
usage or authorization. Feedback must come from the trusted evaluator, never
from model JSON. Filesystem/process isolation and backend wiring remain separate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

POLICIES = ("no_persistent_memory", "legacy_memory", "tehm")
MAX_SOURCE_BYTES = 262144
MAX_RESPONSE_BYTES = 1048576
PROPOSAL_SCHEMA = "tehm-r5-rtl-proposal-v1"
OBLIGATIONS = ("target", "preservation", "native")


class ControllerError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def positive(value: int) -> bool:
    return type(value) is int and value > 0


def source_name(name: object) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        raise ControllerError("invalid source path")
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name or any(
            part.startswith(".") for part in path.parts)
            or path.suffix not in {".v", ".sv", ".vh", ".svh"}):
        raise ControllerError("source path outside declared RTL namespace")
    return name


def source_digest(sources: dict[str, str]) -> str:
    if not isinstance(sources, dict) or not sources or len(sources) > 64:
        raise ControllerError("empty or excessive source closure")
    total = 0
    for name, text in sources.items():
        source_name(name)
        if any(str(parent) in sources for parent in PurePosixPath(name).parents):
            raise ControllerError("source file conflicts with a source directory")
        if not isinstance(text, str) or not text or "\0" in text:
            raise ControllerError("source must be nonempty UTF-8 text without NUL")
        try:
            total += len(text.encode("utf-8"))
        except UnicodeError as exc:
            raise ControllerError("source is not encodable UTF-8") from exc
    if total > MAX_SOURCE_BYTES:
        raise ControllerError("source closure byte limit")
    return sha(canonical(sources))


def _unique(pairs: list) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ControllerError("duplicate JSON key")
        obj[key] = value
    return obj


def candidate_from_proposal(sources: dict[str, str], response: str) -> tuple[dict, dict]:
    """Validate model text and return candidate bytes, never a verdict or edit slot.

    Every policy has the same replace-declared-source and no-action capability.
    This generic proposal path does not bypass a TEHM asset's separate v7 binder.
    """
    before = source_digest(sources)
    if not isinstance(response, str) or len(response.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ControllerError("response byte limit or type")
    try:
        proposal = json.loads(response, object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(
                                  ControllerError("nonfinite JSON")))
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise ControllerError("malformed proposal") from exc
    if not isinstance(proposal, dict) or set(proposal) != {"schema", "base_digest", "action", "edits"}:
        raise ControllerError("unexpected proposal fields, including claimed verdicts")
    if proposal["schema"] != PROPOSAL_SCHEMA or proposal["base_digest"] != before:
        raise ControllerError("proposal schema or stale source identity")
    edits = proposal["edits"]
    if not isinstance(edits, list) or len(edits) > len(sources):
        raise ControllerError("invalid edits")
    if proposal["action"] == "no_action":
        if edits:
            raise ControllerError("no-action contains edits")
    elif proposal["action"] != "replace_sources" or not edits:
        raise ControllerError("unsupported or empty action")
    candidate, changed = dict(sources), []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "content"}:
            raise ControllerError("invalid edit fields")
        name = source_name(edit["path"])
        if name not in sources or name in changed:
            raise ControllerError("foreign or duplicate edit path")
        changed.append(name)
        candidate[name] = edit["content"]
    after = source_digest(candidate)
    return candidate, {"schema": "tehm-r5-rtl-candidate-v1", "before_digest": before,
        "after_digest": after, "proposal_digest": sha(response.encode("utf-8")),
        "status": "CHANGED" if after != before else "NO_CHANGE",
        "changed_files": sorted(name for name in sources if sources[name] != candidate[name]),
        "functional_verdict": "NOT_EVALUATED"}


def write_candidate(parent: Path, name: str, sources: dict[str, str]) -> Path:
    """Write a fresh candidate below a trusted runner-owned parent; never overwrite.

    Does not sandbox malicious HDL or defend against a hostile concurrent writer
    in the trusted parent. The evaluator must mount the result read-only.
    """
    source_digest(sources)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ControllerError("invalid candidate directory name")
    parent = Path(parent).absolute()
    if parent.resolve(strict=True) != parent:
        raise ControllerError("linked candidate parent")
    destination = parent / name
    destination.mkdir(mode=0o700, exist_ok=False)
    for relative, content in sorted(sources.items()):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
    return destination


def public_feedback(private_result: object) -> dict:
    """Project only fixed verdict enums from a trusted evaluator result.

    Never forward paths, expected values, assertions, traces, gold edits or text.
    Missing/malformed obligation records make the whole observation UNKNOWN.
    """
    raw = private_result.get("obligations") if isinstance(private_result, dict) else None
    valid = (isinstance(raw, dict) and set(raw) == set(OBLIGATIONS)
             and all(type(raw[key]) is str and raw[key] in {"PASS", "FAIL", "UNKNOWN"}
                     for key in OBLIGATIONS))
    verdicts = {key: raw[key] if valid else "UNKNOWN" for key in OBLIGATIONS}
    overall = ("FAIL" if "FAIL" in verdicts.values() else
               "UNKNOWN" if "UNKNOWN" in verdicts.values() else "PASS")
    return {"schema": "tehm-r5-public-verdict-v1", "obligations": verdicts,
            "task_verdict": overall, "stop": overall != "FAIL"}


@dataclass(frozen=True)
class Limits:
    proposals: int
    total_reserved_tokens: int
    max_input_tokens: int
    max_output_tokens: int
    evaluations: int

    def __post_init__(self):
        if not all(positive(value) for value in asdict(self).values()):
            raise ControllerError("limits must be positive integers")


class BudgetLedger:
    """Single-writer, append-only reservations before dispatch, with no refunds.

    Input token count is supplied by a future trusted tokenizer/broker; this
    module cannot verify it. Timeout, malformed output and pending calls retain
    their reservations. Existing ledgers cannot be reopened or reset by this API.
    No call/token reservation authorizes provider use or proves actual usage.
    """
    def __init__(self, path: Path, policy: str, limits: Limits):
        if policy not in POLICIES or not isinstance(limits, Limits):
            raise ControllerError("invalid policy or limits")
        self.limits, self.proposals, self.tokens, self.evaluations = limits, 0, 0, 0
        self.tail, self.sequence, self.pending = None, 0, False
        self.stopped, self.evaluation_pending = False, False
        self.candidate = None
        self.handle = Path(path).open("x", encoding="utf-8")
        self._append({"event": "init", "policy": policy, "limits": asdict(limits),
                      "execution_authorized": False, "mode": "OFFLINE_RESERVATION_COMPONENT"})

    def _append(self, value: dict) -> None:
        record = {"sequence": self.sequence, "previous_digest": self.tail, **value}
        record["digest"] = sha(canonical(record))
        self.handle.write(canonical(record).decode("utf-8") + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.tail, self.sequence = record["digest"], self.sequence + 1

    def reserve_proposal(self, input_tokens: int) -> int:
        if not positive(input_tokens) or input_tokens > self.limits.max_input_tokens:
            raise ControllerError("input token bound")
        amount = input_tokens + self.limits.max_output_tokens
        if (self.pending or self.stopped or self.evaluation_pending or self.candidate is not None
                or self.proposals >= self.limits.proposals
                or self.tokens + amount > self.limits.total_reserved_tokens):
            raise ControllerError("pending proposal or exhausted budget")
        self._append({"event": "reserve_proposal", "proposal": self.proposals,
                      "input_tokens": input_tokens, "output_token_reservation": self.limits.max_output_tokens})
        self.tokens += amount
        self.proposals += 1
        self.pending = True
        return self.proposals - 1

    def finish_proposal(self, terminal: str, candidate_digest: str | None = None) -> None:
        if not self.pending or terminal not in {"RECEIVED", "INVALID", "TIMEOUT", "ERROR"}:
            raise ControllerError("invalid proposal terminal")
        if terminal == "RECEIVED":
            if not isinstance(candidate_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_digest):
                raise ControllerError("received proposal requires validated candidate digest")
        elif candidate_digest is not None:
            raise ControllerError("failed proposal cannot supply a candidate")
        self._append({"event": "proposal_terminal", "proposal": self.proposals - 1,
                      "terminal": terminal, "candidate_digest": candidate_digest, "refund_tokens": 0})
        self.pending = False
        self.candidate = candidate_digest
        self.stopped = terminal in {"TIMEOUT", "ERROR"}

    def reserve_evaluation(self, candidate_digest: str) -> None:
        if (self.pending or self.stopped or self.evaluation_pending or self.candidate is None
                or candidate_digest != self.candidate or self.evaluations >= self.limits.evaluations):
            raise ControllerError("evaluation reservation invalid or exhausted")
        self._append({"event": "reserve_evaluation", "candidate_digest": candidate_digest})
        self.evaluations += 1
        self.evaluation_pending = True
        self.candidate = None

    def finish_evaluation(self, private_result: object) -> dict:
        if not self.evaluation_pending:
            raise ControllerError("no pending evaluator")
        feedback = public_feedback(private_result)
        self._append({"event": "evaluation_terminal", "public_feedback": feedback})
        self.evaluation_pending = False
        self.stopped = feedback["stop"]
        return feedback

    def close(self) -> None:
        self.handle.close()
