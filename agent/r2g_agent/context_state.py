"""Controller-owned facts that must survive context selection.

This is a read model, not an approval grant or a second task store. Callers
construct it from their durable turn/action records; model-generated prose is
not a source for pending actions, approval decisions, or job ownership.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContextState:
    goal: str = ""
    constraints: tuple[str, ...] = ()
    pending_actions: tuple[dict, ...] = ()
    approvals: tuple[dict, ...] = ()
    active_job_id: str | None = None
    source: dict = field(default_factory=dict)
