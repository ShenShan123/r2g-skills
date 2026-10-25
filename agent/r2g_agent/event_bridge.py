"""Join job terminal outbox records to bounded, durable review turns.

Polling discovers committed events; it never polls the model. Each job terminal
record creates one review turn. A started/uncertain model call is not retried on
restart, while a queued turn that never started can still be claimed.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from . import agent_loop

LOG = logging.getLogger(__name__)


class BridgeWorker:
    def __init__(self, sessions, jobs, adapter_factory):
        self.sessions, self.jobs, self.adapter_factory = sessions, jobs, adapter_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._hooks: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None

    def add_hook(self, callback: Callable[[], None]) -> None:
        self._hooks.append(callback)

    def enqueue(self, session_id: str, message: str, *, request_id: str | None = None,
                kind: str = "user", job_id: str | None = None, grant_id: str | None = None,
                allow_queue: bool = False) -> dict:
        if kind not in ("user", "job_review", "repair_review"):
            raise ValueError("unsupported turn kind")
        payload = {"message": message, "job_id": job_id}
        if grant_id:
            payload["grant_id"] = grant_id  # Only server-validated callers supply this.
        turn, _ = self.sessions.turns.create(
            session_id, request_id or uuid.uuid4().hex, payload, kind=kind,
            job_id=job_id, max_model_calls=4 if kind == "job_review" else agent_loop.MAX_STEPS,
            max_actions=12 if kind == "job_review" else 24, allow_queue=allow_queue)
        self.launch(turn)
        return self.sessions.turns.get(turn["id"])

    def launch(self, turn: dict) -> None:
        if turn["status"] != "queued" or self._stop.is_set():
            return
        with self._lock:
            if turn["id"] in self._threads:
                return
            thread = threading.Thread(target=self._run, args=(turn,), daemon=True, name="turn-" + turn["id"][:8])
            self._threads[turn["id"]] = thread
            thread.start()

    def _run(self, turn: dict) -> None:
        try:
            try:
                adapter = self.adapter_factory()
            except Exception as exc:
                if self.sessions.turns.claim(turn["id"]):
                    self.sessions.turns.finish(turn["id"], "error", {
                        "type": "error", "message": "模型配置不可用（%s），审查未执行。" % type(exc).__name__})
                return
            for _ in agent_loop.run_turn(adapter, self.sessions, turn["session_id"],
                                         turn["payload"]["message"], turn_id=turn["id"]):
                pass  # Events are committed by run_turn, independent of SSE.
        finally:
            with self._lock:
                self._threads.pop(turn["id"], None)
            if self.sessions.turns.get(turn["id"])["status"] != "queued":
                self._wake.set()

    def tick(self) -> None:
        self.sessions.turns.recover()
        for event in self.jobs.review_outbox():
            # The unique request key closes the cross-database crash window:
            # if enqueue committed but reviewed() did not, the same turn is reused.
            self.enqueue(
                event["session_id"],
                "【自动任务审查】任务 %s 已进入 %s。读取该任务的最新报告与验证状态，解释实际结果、"
                "失败原因和下一步；证据不足请明确说明。只允许读取或提出需用户授权的修复提案。"
                % (event["job_id"], event["status"]),
                request_id="job-review:" + event["job_id"], kind="job_review",
                job_id=event["job_id"], allow_queue=True)
            turn = self.sessions.turns.by_key("job-review:" + event["job_id"])
            self.jobs.reviewed(event["id"], turn["id"])
        for hook in self._hooks:
            hook()
        for turn in self.sessions.turns.queued():
            self.launch(turn)

    def start(self) -> None:
        if self._thread is not None:
            return
        self.sessions.turns.recover()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-review-bridge")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.exception("job review bridge reconciliation failed")
            self._wake.wait(2)
            self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
