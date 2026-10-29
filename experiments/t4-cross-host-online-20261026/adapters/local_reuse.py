"""Host source-action projection, not new TEHM evidence or routing authority."""
import hashlib

from bridge import digest
from tehm.ids import stable_dumps

SUPPORTED_OBLIGATIONS = {"TARGET_FAILURE_REMOVED", "PRESERVE_FROZEN_REGRESSION", "VERIFIER_REGRESSION"}


def source_edits(choice, state, *, allowed_names):
    if choice.get("mode") != "direct_reuse" or choice.get("routing", {}).get("decision") != "APPLY":
        raise ValueError("not an APPLY direct-reuse decision")
    proposal = choice.get("direct_proposal") or {}
    binding = proposal.get("binding") or {}
    if binding.get("status") != "BOUND" or binding.get("lifecycle_status") != "promoted":
        raise ValueError("unbound or unpromoted action")
    expected = hashlib.sha256(stable_dumps(state.context().to_dict()).encode()).hexdigest()
    if (binding.get("proof") or {}).get("target_context_digest") != expected:
        raise ValueError("binding belongs to another state")
    if proposal.get("candidate_id") not in choice.get("selected_ids", []):
        raise ValueError("action is not selected")
    action = choice.get("action") or {}
    if action != binding.get("action"):
        raise ValueError("action differs from its checked binding")
    obligations = proposal.get("obligations") or []
    if not obligations or any(o.get("obligation") not in SUPPORTED_OBLIGATIONS
                              or o.get("status") not in {"BOUND", "SYNTHESIZABLE"} for o in obligations):
        raise ValueError("native evaluator cannot discharge all obligations")
    payload = action.get("payload") or {}
    edits = payload.get("config_edits")
    if payload.get("unresolved_knobs") or not isinstance(edits, dict) or not edits:
        raise ValueError("no complete source edit")
    if any(name not in allowed_names or type(value) is not str or not value
           for name, value in edits.items()):
        raise ValueError("unsupported source edit, path or value")
    if all(state.config.get(name) == value for name, value in edits.items()):
        raise ValueError("already-applied no-op")
    return edits


class ScoutReuse:
    """Execute with the native tool dispatcher; validate and restore on failure."""
    def __init__(self, agent, observer, evidence):
        self.agent, self.observer, self.evidence = agent, observer, evidence
        self.attempted = set()

    def __call__(self, choice):
        before = self.evidence.state()
        try:
            allowed = {name for name in before.config
                       if name not in {"tb.sv", "tb.v"} and name.endswith((".v", ".sv"))}
            edits = source_edits(choice, before, allowed_names=allowed)
            identity = digest([choice.get("selected_ids"), choice["action"]])
            if identity in self.attempted:
                raise ValueError("local action already tried in this task")
            for name in edits:
                path = self.agent.workspace / name
                if path.is_symlink() or path.name != name:
                    raise ValueError("unsafe native source path")
        except ValueError as exc:
            self.observer.memory.journal.append("local_reuse_rejected", reason=str(exc),
                                                selected_ids=choice.get("selected_ids", []))
            return None
        self.attempted.add(identity)
        self.observer.deferred = []
        self.observer.defer_writes = True
        success = False
        technical_error = None
        try:
            for name, value in edits.items():
                self.agent._execute_tool("replace_file", {"filename": name, "content": value})
            count = len(self.agent.all_evals)
            self.agent._execute_tool("run_evaluation", {"filename": next(iter(edits))})
            raw = self.agent.all_evals[-1] if len(self.agent.all_evals) > count else {}
            success = raw.get("passed") is True and type(raw.get("pass_rate")) in {int, float}
            for args in self.observer.deferred:
                self.observer.memory.write(*args, rolled_back=not success)
            if not self.observer.deferred:
                from native_evidence import receipt
                after = self.evidence.state()
                verification = receipt(self.observer.memory, before, choice["action"], after,
                    verdict="UNKNOWN", oracle="UNKNOWN", scope="rtlscout:missing_native_evaluation",
                    complete=False, before_measured=False, removed=None,
                    raw={"reason": "native evaluation produced no usable outcome"})
                self.observer.memory.write(before, choice["action"], after, verification, rolled_back=True)
        except Exception as exc:
            technical_error = exc
            success = False
        finally:
            self.observer.defer_writes = False
        if not success:
            self.observer.suppress_verification = True
            try:
                for name in edits:
                    self.agent._execute_tool("replace_file", {"filename": name, "content": before.config[name]})
                self.agent._execute_tool("run_evaluation", {"filename": next(iter(edits))})
            finally:
                self.observer.suppress_verification = False
            restored = self.evidence.state()
            if restored.config != before.config:
                raise RuntimeError("local source restoration did not reproduce entry state")
            expected = (before.reports.get("evaluation") or {}).get("status")
            actual = (restored.reports.get("evaluation") or {}).get("status")
            if expected in {"pass", "fail"} and actual != expected:
                raise RuntimeError("local source restoration could not be verified")
            self.evidence.previous_evaluated_state = restored
            self.evidence.seen_eval = len(self.agent.all_evals)
            self.evidence.pending_tools = []
        self.observer.memory.journal.append("local_reuse_completed", accepted=success,
            rolled_back=not success, selected_ids=choice.get("selected_ids", []))
        if technical_error is not None:
            raise RuntimeError("local memory trial had a technical failure; source restored") from technical_error
        return {"accepted": success, "summary": "Native memory action passed verification." if success
                else "Native memory action failed; entry source restored and rechecked. Continue without forcing this memory."}
