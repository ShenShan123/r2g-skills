"""A deliberately narrow, source-bound repair action with host-owned authority.

The first action inserts one semicolon into one approved RTL file. A proposal
is not approval. Every attempt uses a new copy and an immutable functional
oracle; only a checked copy may enter the existing EDA pipeline.
"""
from __future__ import annotations

from contextlib import contextmanager
import difflib
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .input_snapshots import capture, digest, manifest, read_tree, write_json
from .repair_checks import ORACLE_ID, check, oracle_policy
from .tool_outputs import ToolContext


class RepairDenied(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS repair_proposals (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_job_id TEXT NOT NULL,
 design TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
 grant_id TEXT, action_id TEXT UNIQUE, child_job_id TEXT, payload TEXT NOT NULL,
 outcome TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS repair_grants (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_job_id TEXT NOT NULL,
 status TEXT NOT NULL, deadline REAL NOT NULL, max_rounds INTEGER NOT NULL,
 rounds_used INTEGER NOT NULL DEFAULT 0, max_model_calls INTEGER NOT NULL,
 model_calls_used INTEGER NOT NULL DEFAULT 0, scope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repair_usage (
 grant_id TEXT NOT NULL, model_call_id TEXT PRIMARY KEY, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repair_approval_outbox (
 grant_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
 created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', turn_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS repair_candidate_once ON repair_proposals(
 session_id,parent_job_id,json_extract(payload,'$.before.sha256'),json_extract(payload,'$.after_rtl.sha256')
);
"""


class RepairController:
    def __init__(self, database: Path, jobs, pipeline=None):
        self.database, self.jobs, self.pipeline = Path(database), jobs, pipeline
        self.root = self.database.parent / "repair-attempts"
        with self._c() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _c(self):
        c = sqlite3.connect(self.database, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def proposal(self, sid: str, pid: str) -> dict:
        with self._c() as c:
            row = c.execute("SELECT * FROM repair_proposals WHERE id=? AND session_id=?", (pid, sid)).fetchone()
        if row is None:
            raise RepairDenied("repair proposal not found in this session")
        return dict(row, payload=json.loads(row["payload"]), outcome=json.loads(row["outcome"]))

    def _source(self, jid: str, name: str, event_id: int) -> tuple[dict, dict, dict, dict[str, bytes]]:
        if type(event_id) is not int or event_id < 1:
            raise RepairDenied("a positive evidence event ID is required")
        job, design = self.jobs.job(jid), self.jobs.design(jid, name)
        if not job or not design or design["status"] != "failed" or job["status"] != "failed":
            raise RepairDenied("repair requires a terminal failed job and design")
        detail = design.get("detail") or {}
        events = self.jobs.events(jid, after=event_id - 1, limit=1)
        if not events or events[0]["id"] != event_id or events[0]["design"] != name:
            raise RepairDenied("evidence event does not belong to this design")
        event = events[0]
        evidence = event["message"] + "\n" + str((event.get("data") or {}).get("log_tail", ""))
        if (event["stage"] != "synth" or event["kind"] != "fail" or detail.get("failed_stage") != "synth"
                or not re.search(r"syntax error", evidence, re.I)
                or re.search(r"Permission denied|No space left|command not found|not installed", evidence, re.I)):
            raise RepairDenied("only a recorded synthesis syntax error is supported; infrastructure errors are excluded")
        project = Path(design["project"])
        snapshot = capture(project)
        binding = detail.get("input_bindings", {}).get("synth", {})
        if not binding.get("unchanged") or binding.get("before", {}).get("sha256") != snapshot["sha256"]:
            raise RepairDenied("failure evidence is not bound to the current inputs; refuse an unverified source version")
        files = read_tree(project / "rtl")
        if manifest(files) != snapshot["rtl"]:
            raise RepairDenied("source changed between evidence capture and proposal read")
        return job, design, snapshot, files

    def propose(self, context: ToolContext, *, job_id: str, design: str, file: str,
                line: int, expected_line: str, evidence_event_id: int, rationale: str) -> dict:
        from .turns import TurnStore
        if not context.turn_id or not context.action_id:
            raise RepairDenied("a durable host turn and action are required for proposals")
        if job_id not in TurnStore(self.database).linked_jobs(context.session_id):
            raise RepairDenied("job is not explicitly linked to this conversation")
        if context.job_id and context.job_id != job_id:
            raise RepairDenied("repair proposal is outside the current review task")
        job, record, snapshot, files = self._source(job_id, design, evidence_event_id)
        if job["spec"].get("output") != "graph":
            raise RepairDenied("the first repair acceptance policy supports graph output only")
        if file not in files or Path(file).suffix not in (".v", ".sv") or len(files[file]) > 65536:
            raise RepairDenied("select one existing RTL file of at most 64 KiB")
        if len([f for f in files if Path(f).suffix in (".v", ".sv")]) != 1:
            raise RepairDenied("the first functional policy supports one counter8 source only")
        text = files[file].decode("utf-8")
        # No new constructs can be introduced by this action. Refuse unsafe
        # constructs already present before invoking an installed simulator.
        # This first policy deliberately rejects even comments/strings with
        # these tokens; a regex pretending to lex Verilog could hide $system.
        if re.search(r"[`$#]|\b(initial|forever|while|force|release)\b", text):
            raise RepairDenied("source needs a separately registered functional check policy")
        rows = text.splitlines(keepends=True)
        if type(line) is not int or not 1 <= line <= len(rows) or rows[line - 1].rstrip("\r\n") != expected_line:
            raise RepairDenied("line/version mismatch; read the source again")
        if not expected_line.strip() or expected_line.rstrip().endswith(";") or "//" in expected_line:
            raise RepairDenied("select a nonempty code line that does not already end in a semicolon")
        rows[line - 1] = expected_line + ";" + ("\r\n" if rows[line - 1].endswith("\r\n") else "\n" if rows[line - 1].endswith("\n") else "")
        after = "".join(rows)
        proposed_files = {**files, file: after.encode()}
        policy = oracle_policy()
        if (record.get("detail") or {}).get("top") not in (None, "counter8"):
            raise RepairDenied("no functional policy registered for this top module")
        payload = {"action": "rtl_insert_semicolon", "job_id": job_id, "design": design,
                   "file": file, "line": line, "expected_line": expected_line,
                   "rationale": rationale[:1000], "evidence_event_id": evidence_event_id,
                   "before": snapshot, "after_rtl": manifest(proposed_files),
                   "policy": policy, "policy_sha256": digest(policy),
                   "spec": {k: job["spec"].get(k) for k in ("platform", "output", "params")},
                   "diff": "".join(difflib.unified_diff(text.splitlines(True), after.splitlines(True),
                                                       fromfile="a/" + file, tofile="b/" + file))}
        pid, status = uuid.uuid4().hex, "awaiting_approval"
        if context.grant_id:
            grant = self.grant(context.session_id, context.grant_id)
            self._validate_scope(grant, payload)
            status = "approved"
        with self._c() as c:
            c.execute("BEGIN IMMEDIATE")
            # A repeated durable action must return its original proposal.
            row = c.execute("SELECT id FROM repair_proposals WHERE action_id=?", (context.action_id,)).fetchone()
            if row:
                return self.public(self.proposal(context.session_id, row[0]))
            row = c.execute("SELECT id FROM repair_proposals WHERE session_id=? AND parent_job_id=? "
                            "AND json_extract(payload,'$.before.sha256')=? AND json_extract(payload,'$.after_rtl.sha256')=?",
                            (context.session_id, job_id, snapshot["sha256"], payload["after_rtl"]["sha256"])).fetchone()
            if row:
                return {**self.public(self.proposal(context.session_id, row[0])), "repeated_candidate": True}
            c.execute("INSERT INTO repair_proposals "
                      "(id,session_id,parent_job_id,design,created,status,grant_id,action_id,payload) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (pid, context.session_id, job_id, design, time.time(), status,
                                                   context.grant_id, context.action_id, json.dumps(payload)))
        return self.public(self.proposal(context.session_id, pid))

    @staticmethod
    def public(proposal: dict) -> dict:
        return {"repair_proposal": proposal["id"], "status": proposal["status"],
                "job_id": proposal["child_job_id"] or proposal["parent_job_id"],
                "parent_job_id": proposal["parent_job_id"], "design": proposal["design"],
                "grant_id": proposal["grant_id"], "diff": proposal["payload"]["diff"],
                "source_snapshot_id": proposal["payload"]["before"]["sha256"],
                "functional_policy": ORACLE_ID, "action": "rtl_insert_semicolon",
                "scope": "one RTL file in a new copy; no changes to original, constraints, ports or oracle",
                "outcome": proposal["outcome"], "child_job_id": proposal["child_job_id"]}

    def grant(self, sid: str, gid: str) -> dict:
        with self._c() as c:
            row = c.execute("SELECT * FROM repair_grants WHERE id=? AND session_id=?", (gid, sid)).fetchone()
        if not row:
            raise RepairDenied("repair permission not found in this session")
        return dict(row, scope=json.loads(row["scope"]))

    @staticmethod
    def _validate_scope(grant: dict, payload: dict) -> None:
        expected = {k: payload[k] for k in ("job_id", "design", "file", "before", "policy_sha256", "spec")}
        if grant["status"] != "approved" or time.time() >= grant["deadline"] or grant["scope"] != expected:
            raise RepairDenied("repair permission is expired, revoked, or bound to another source/policy")

    def approve(self, sid: str, pid: str, snapshot: str, *, max_rounds=2, max_model_calls=6, wall_seconds=900) -> dict:
        if (type(max_rounds) is not int or not 1 <= max_rounds <= 2
                or type(max_model_calls) is not int or not 1 <= max_model_calls <= 8
                or type(wall_seconds) is not int or not 30 <= wall_seconds <= 900):
            raise RepairDenied("repair budget exceeds the registered policy bounds")
        proposal = self.proposal(sid, pid)
        payload = proposal["payload"]
        if snapshot != payload["before"]["sha256"]:
            raise RepairDenied("approval must name the reviewed source snapshot")
        self._source(payload["job_id"], payload["design"], payload["evidence_event_id"])
        if oracle_policy() != payload["policy"]:
            raise RepairDenied("functional checker changed before approval")
        gid = uuid.uuid4().hex
        scope = {k: payload[k] for k in ("job_id", "design", "file", "before", "policy_sha256", "spec")}
        with self._c() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT grant_id,status FROM repair_proposals WHERE id=? AND session_id=?", (pid, sid)).fetchone()
            if row["grant_id"]:
                return self.grant(sid, row["grant_id"])
            if row["status"] != "awaiting_approval":
                raise RepairDenied("proposal is no longer awaiting approval")
            if c.execute("SELECT 1 FROM repair_proposals WHERE session_id=? AND parent_job_id=? "
                         "AND (status='recovery_required' OR json_extract(outcome,'$.execution_may_have_completed')=1)",
                         (sid, proposal["parent_job_id"])).fetchone():
                raise RepairDenied("an earlier repair attempt requires reconciliation before new approval")
            c.execute("INSERT INTO repair_grants "
                      "(id,session_id,parent_job_id,status,deadline,max_rounds,max_model_calls,scope) VALUES (?,?,?,?,?,?,?,?)",
                      (gid, sid, proposal["parent_job_id"], "approved", time.time() + wall_seconds,
                       max_rounds, max_model_calls, json.dumps(scope)))
            c.execute("UPDATE repair_proposals SET grant_id=?,status='approved' WHERE id=?", (gid, pid))
            c.execute("INSERT INTO repair_approval_outbox(grant_id,session_id,proposal_id,created) VALUES (?,?,?,?)",
                      (gid, sid, pid, time.time()))
        return self.grant(sid, gid)

    def execute(self, context: ToolContext, pid: str) -> dict:
        if context.read_only or not context.grant_id or not context.turn_id or not context.action_id:
            raise RepairDenied("a host-approved repair turn and action are required")
        proposal = self.proposal(context.session_id, pid)
        payload = proposal["payload"]
        if proposal["grant_id"] != context.grant_id:
            raise RepairDenied("proposal and turn do not share an approved grant")
        grant = self.grant(context.session_id, context.grant_id)
        self._validate_scope(grant, payload)
        job, _, snapshot, files = self._source(payload["job_id"], payload["design"], payload["evidence_event_id"])
        if snapshot != payload["before"] or oracle_policy() != payload["policy"]:
            raise RepairDenied("inputs or checker version changed; approval is stale")
        with self._c() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status FROM repair_proposals WHERE id=?", (pid,)).fetchone()
            if row[0] != "approved":
                return self.public(self.proposal(context.session_id, pid))
            used = c.execute("UPDATE repair_grants SET rounds_used=rounds_used+1 WHERE id=? AND status='approved' "
                             "AND rounds_used<max_rounds AND deadline>? AND NOT EXISTS "
                             "(SELECT 1 FROM repair_proposals WHERE grant_id=? AND "
                             "(status IN ('executing','running','recovery_required') OR "
                             "json_extract(outcome,'$.execution_may_have_completed')=1))",
                             (grant["id"], time.time(), grant["id"])).rowcount
            if not used:
                raise RepairDenied("repair round or wall-clock budget exhausted")
            c.execute("UPDATE repair_proposals SET status='executing' WHERE id=?", (pid,))
        try:
            # Refuse all existing ancestors that redirect a private attempt.
            current = self.root
            while current != current.parent:
                if current.is_symlink():
                    raise RepairDenied("repair workspace cannot traverse a symlink")
                current = current.parent
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            workspace = self.root / pid
            workspace.mkdir(mode=0o700, exist_ok=False)
            source = workspace / "source"
            source.mkdir(mode=0o700)
            rows = files[payload["file"]].decode().splitlines(keepends=True)
            value = rows[payload["line"] - 1]
            rows[payload["line"] - 1] = payload["expected_line"] + ";" + value[len(payload["expected_line"]):]
            files[payload["file"]] = "".join(rows).encode()
            for name, data in files.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if manifest(read_tree(source)) != payload["after_rtl"]:
                raise RepairDenied("candidate copy differs from the reviewed patch")
            result = check(workspace, source / payload["file"], payload["policy"], grant["deadline"])
            write_json(workspace / "cheap_checks.json", result)
            (workspace / "change.diff").write_text(payload["diff"])
            if not result["ok"]:
                self._finish(pid, "check_failed", {"cheap_checks": result, "next": "use the new checker evidence; do not repeat this candidate"})
            else:
                receipt = {"proposal_id": pid, "grant_id": grant["id"], "parent_job_id": payload["job_id"],
                           "before": payload["before"], "after_rtl": payload["after_rtl"],
                           "policy_sha256": payload["policy_sha256"], "cheap_checks": result,
                           "diff": payload["diff"], "deadline": grant["deadline"]}
                jid = self.pipeline.submit(
                    [{"name": payload["design"], "path": str(source), "top": "counter8", "repair_receipt": receipt}],
                    platform=payload["spec"]["platform"], output=payload["spec"]["output"],
                    params=payload["spec"]["params"], title="修复验证 · " + payload["design"],
                    submission={"request_key": "repair:" + pid, "session_id": context.session_id,
                                "turn_id": context.turn_id, "action_id": context.action_id})
                with self._c() as c:
                    c.execute("UPDATE repair_proposals SET status='running',child_job_id=?,outcome=? WHERE id=?",
                              (jid, json.dumps({"cheap_checks": result}), pid))
        except Exception as exc:
            uncertain = not isinstance(exc, RepairDenied)
            self._finish(pid, "recovery_required" if uncertain else "stopped",
                         {"reason": str(exc), "execution_may_have_completed": uncertain})
            raise
        return self.public(self.proposal(context.session_id, pid))

    def _finish(self, pid, status, outcome):
        with self._c() as c:
            c.execute("UPDATE repair_proposals SET status=?,outcome=? WHERE id=?", (status, json.dumps(outcome), pid))

    def recover(self):
        with self._c() as c:
            c.execute("UPDATE repair_proposals SET status='recovery_required' WHERE status='executing'")

    def dispatch_approvals(self, bridge):
        with self._c() as c:
            rows = c.execute("SELECT * FROM repair_approval_outbox WHERE status='pending' ORDER BY created").fetchall()
        dispatched = {}
        for row in rows:
            grant = self.grant(row["session_id"], row["grant_id"])
            if grant["status"] != "approved" or grant["deadline"] <= time.time():
                with self._c() as c:
                    c.execute("UPDATE repair_approval_outbox SET status='expired' WHERE grant_id=?", (row["grant_id"],))
                    c.execute("UPDATE repair_proposals SET status=? WHERE id=? AND status='approved'",
                              ("revoked" if grant["status"] == "revoked" else "budget_exhausted", row["proposal_id"]))
                continue
            turn = bridge.enqueue(
                row["session_id"], "用户已审核并授权有限修复提案 %s。先执行该提案；检查失败时读取新证据，在已批准作用域及剩余预算内"
                "提出不同候选或明确停止。不要重复旧候选，完整流程通过前不得宣称修复成功。" % row["proposal_id"],
                request_id="repair-grant:" + grant["id"], kind="repair_review",
                job_id=grant["parent_job_id"], grant_id=grant["id"], allow_queue=True)
            with self._c() as c:
                c.execute("UPDATE repair_approval_outbox SET status='dispatched',turn_id=? WHERE grant_id=?",
                          (turn["id"], grant["id"]))
            dispatched[grant["id"]] = turn
        return dispatched

    def tick(self):
        # A child may have committed before the proposal update. Rebind it from
        # the authoritative idempotent submission record; never execute again.
        with self._c() as c:
            uncertain = c.execute("SELECT id FROM repair_proposals WHERE child_job_id IS NULL AND "
                                  "status IN ('executing','recovery_required','stopped')").fetchall()
        for row in uncertain:
            submitted = self.jobs.job_for_submission("repair:" + row["id"])
            if submitted:
                with self._c() as c:
                    c.execute("UPDATE repair_proposals SET status='running',child_job_id=? WHERE id=? AND child_job_id IS NULL",
                              (submitted["job_id"], row["id"]))
        with self._c() as c:
            rows = c.execute("SELECT p.*,g.deadline FROM repair_proposals p JOIN repair_grants g ON p.grant_id=g.id "
                             "WHERE p.child_job_id IS NOT NULL AND p.status IN ('running','recovery_required','budget_exhausted')").fetchall()
        for row in rows:
            job = self.jobs.job(row["child_job_id"])
            if job and job["status"] in ("done", "failed", "cancelled") and job["updated"] <= row["deadline"]:
                self._finish(row["id"], "validated" if job["status"] == "done" else "stopped",
                             {"job_id": job["id"], "status": job["status"], "result": job.get("result")})
            elif time.time() >= row["deadline"]:
                if job and job["status"] not in ("done", "failed", "cancelled"):
                    self.pipeline.cancel(job["id"])
                self._finish(row["id"], "budget_exhausted", {
                    "reason": "repair wall-clock budget exhausted; cancellation requested",
                    "execution_may_have_completed": not job or job["status"] not in ("done", "failed", "cancelled")})
            elif job and job["status"] == "recovery_required":
                self._finish(row["id"], "recovery_required", {"job_id": job["id"],
                             "execution_may_have_completed": True, "reason": "child execution requires reconciliation"})


def reserve_model_for_turn(database, grant_id, sid):
    controller = RepairController(database, None)
    with controller._c() as c:
        changed = c.execute("UPDATE repair_grants SET model_calls_used=model_calls_used+1 WHERE id=? AND session_id=? "
                            "AND status='approved' AND deadline>? AND model_calls_used<max_model_calls",
                            (grant_id, sid, time.time())).rowcount
        if not changed:
            raise RepairDenied("repair model-call budget exhausted or grant expired/revoked")


def record_model_usage(database, grant_id, cid, metadata):
    with RepairController(database, None)._c() as c:
        c.execute("INSERT OR IGNORE INTO repair_usage VALUES (?,?,?)", (grant_id, cid, json.dumps(metadata)))


def context_for_turn(database, grant_id, sid):
    grant = RepairController(database, None).grant(sid, grant_id)
    return {k: grant[k] for k in ("id", "status", "deadline", "scope", "rounds_used", "max_rounds",
                                 "model_calls_used", "max_model_calls")}


def validate_chat_grant(database, grant_id, sid, job_id=None):
    grant = RepairController(database, None).grant(sid, grant_id)
    if grant["status"] != "approved" or grant["deadline"] <= time.time() or (job_id and job_id != grant["parent_job_id"]):
        raise RepairDenied("repair grant is not valid for this request")
    return grant_id


def _receipt(jobs, jid):
    sources = (jobs.job(jid) or {}).get("spec", {}).get("sources", [])
    return next((s["repair_receipt"] for s in sources if s.get("repair_receipt")), None)


def prepare_child_receipt(jobs, jid, name, project):
    receipt = _receipt(jobs, jid)
    if not receipt:
        return
    if (receipt["after_rtl"] != manifest(read_tree(project / "rtl"))
            or not receipt["cheap_checks"].get("ok") or time.time() >= receipt["deadline"]
            or digest(receipt["cheap_checks"]["oracle"]) != receipt["policy_sha256"]
            or digest(oracle_policy()) != receipt["policy_sha256"]):
        raise RepairDenied("candidate inputs, functional evidence or policy are stale")
    write_json(project / "reports" / "agent_repair.json", receipt)
    (project / "reports" / "agent_repair.diff").write_text(receipt["diff"])
    write_json(project / "reports" / "functional_check.json", receipt["cheap_checks"])
    jobs.set_design(jid, name, detail={"repair_proposal_id": receipt["proposal_id"],
                                     "source_snapshot_id": receipt["after_rtl"]["sha256"],
                                     "acceptance_policy_sha256": receipt["policy_sha256"]})


def validate_child(jobs, jid, name, project):
    receipt = _receipt(jobs, jid)
    if not receipt:
        return
    snapshot = capture(project)
    detail = jobs.design(jid, name)["detail"]
    bindings = detail.get("input_bindings", {})
    if (time.time() >= receipt["deadline"] or snapshot["rtl"] != receipt["after_rtl"]
            or digest(oracle_policy()) != receipt["policy_sha256"]):
        raise RepairDenied("repair inputs changed or deadline expired during validation")
    for stage in ("synth", "flow", "signoff", "graph", "verify"):
        binding = bindings.get(stage, {})
        if (not binding.get("unchanged") or binding.get("exit_code") != 0
                or binding.get("before", {}).get("rtl_sha256") != receipt["after_rtl"]["sha256"]):
            raise RepairDenied("required stage is missing, failed or source-stale: " + stage)
    for stage in ("flow", "signoff", "graph", "verify"):
        binding = bindings[stage]
        if any(binding.get(side, {}).get("constraints_sha256") != snapshot["constraints"]["sha256"]
               for side in ("before", "after")):
            raise RepairDenied("required stage used different constraints: " + stage)
    if bindings["verify"]["after"]["sha256"] != snapshot["sha256"]:
        raise RepairDenied("constraints changed after the required verification")
    jobs.set_design(jid, name, detail={"repair_validation": {"status": "verified", "snapshot": snapshot,
                     "policy_sha256": receipt["policy_sha256"], "proposal_id": receipt["proposal_id"]}})
