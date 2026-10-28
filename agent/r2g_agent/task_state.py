"""Controller-owned task goals, completion conditions and unresolved evidence.

A finished conversation turn is not a finished engineering task.  The model
cannot set these verdicts: they are derived from fresh readers of the existing
EDA validation, diagnostic and delivery receipts.  This ledger adds no repair
authority and never changes the pipeline's acceptance policy.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tarfile
import time
import uuid

from .input_snapshots import digest, manifest, read_tree
from .tool_outputs import redact

PROFILES = ("dataset_delivery", "compile_only")
ACTIVE = frozenset(("pending", "queued", "running", "finalizing", "cancelling",
                    "waiting_input", "waiting_approval", "validating", "recovery_required"))
VERDICTS = frozenset(("PASS", "FAIL", "UNKNOWN", "STALE"))
LAYERS = ("final_result", "quantitative", "technical")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _object(value):
    return value if isinstance(value, dict) else {}


def _condition(identifier, description, verdict="UNKNOWN", *, design=None, source, refs=(), reasons=()):
    return {"id": identifier, "description": description, "design": design,
            "verdict": verdict if verdict in VERDICTS else "UNKNOWN", "source": source,
            "refs": list(refs), "reasons": list(reasons)}


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _regular(path):
    # Reuse the descriptor-based reader used by the delivery writer.  Neither
    # an artifact nor any ancestor may be a symlink.
    from .package import _directory, _open_relative
    path = Path(path).absolute()
    with _directory(path.parent) as directory:
        fd = _open_relative(directory, path.name)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("evidence_is_not_regular_file")
        yield stream, before
        if _identity(before) != _identity(os.fstat(stream.fileno())):
            raise ValueError("evidence_changed_during_read")


def _sha(path, limit=256 * 1024 * 1024):
    with _regular(path) as (stream, info):
        if info.st_size > limit:
            raise ValueError("evidence_byte_limit")
        h, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("evidence_byte_limit")
            h.update(chunk)
        if size != info.st_size:
            raise ValueError("evidence_size_changed")
        return h.hexdigest()


def _document(path):
    with _regular(path) as (stream, info):
        if info.st_size > 4 * 1024 * 1024:
            raise ValueError("evidence_document_limit")
        value = json.loads(stream.read(4 * 1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError("evidence_document_not_object")
        return value


def _compile_condition(evidence):
    """Accept only the fixed compiler's persisted, currently bound receipt."""
    from .paths import r2g_home
    from .task_skills import load_skill
    receipt = _object(_object(evidence).get("diagnostic"))
    ref = {"path": receipt.get("report"), "evidence_id": receipt.get("evidence_id")}
    result = _condition("compile", "固定编译检查通过，且源码、工具与检查契约仍与执行凭据一致",
                        source="host:design_checks.check_design", refs=[ref])
    if not receipt:
        result["reasons"] = ["compile_receipt_not_observed"]
        return result
    try:
        check_id = receipt.get("check_id")
        if not isinstance(check_id, str) or len(check_id) != 32 or any(c not in "0123456789abcdef" for c in check_id):
            raise ValueError("invalid_diagnostic_identity")
        expected = r2g_home() / "design-checks" / check_id / "result.json"
        if Path(receipt.get("report", "")).absolute() != expected.absolute():
            raise ValueError("diagnostic_report_outside_host_store")
        actual = _document(expected)
        if actual != receipt or actual.get("evidence_id") != digest({
                k: v for k, v in actual.items() if k not in ("log_tail", "report", "evidence_id")}):
            raise ValueError("diagnostic_receipt_identity_mismatch")
        if actual.get("check") != "compile":
            raise ValueError("compile_profile_requires_compile_receipt")
        if (manifest(read_tree(Path(actual["source"])))["sha256"] != actual.get("input_snapshot_id")
                or _sha(expected.parent / "stdout.log", 1024 * 1024) != actual.get("log_sha256")
                or _sha(Path(actual["checker"]["path"])) != actual["checker"].get("sha256")):
            result.update(verdict="STALE", reasons=["diagnostic_sources_tool_or_log_changed"])
            return result
        permission = _object(actual.get("task_permission"))
        if permission.get("contract_sha256") != digest(load_skill("compile_failure")):
            result.update(verdict="STALE", reasons=["diagnostic_contract_changed"])
            return result
        if (actual.get("status") == "passed" and type(actual.get("exit_code")) is int
                and actual["exit_code"] == 0 and permission.get("allowed") is True
                and all(actual.get(k) is True for k in
                        ("inputs_unchanged", "checker_unchanged", "task_contract_unchanged"))):
            result.update(verdict="PASS", reasons=[])
        elif type(actual.get("exit_code")) is int and actual["exit_code"] != 0:
            result.update(verdict="FAIL", reasons=[actual.get("failure_class") or "compile_failed"])
        else:
            result["reasons"] = ["diagnostic_execution_unconfirmed"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ValueError) and str(exc) in ("evidence_changed_during_read", "evidence_size_changed"):
            result.update(verdict="STALE", reasons=["diagnostic_evidence_changed_during_read"])
        else:
            result["reasons"] = ["diagnostic_receipt_unavailable_or_invalid:" + type(exc).__name__]
    return result


def _archive_condition(job, reviews):
    """Cross-check actual archive members against fresh EDA evidence snapshots."""
    from .artifact_snapshots import from_entries
    from .package import MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_MEMBERS
    package = _object(job.get("result")).get("package")
    out = _condition("delivery", "实际交付包完整，字节与当前验证过的源码、数据集及原始证据一致",
                     source="host:delivery_archive_reader", refs=[{"path": package}])
    if not package:
        out["reasons"] = ["delivery_archive_not_recorded"]
        return out
    try:
        files, documents, total = {}, {}, 0
        with _regular(package) as (stream, info):
            with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                for member in archive:
                    parts = member.name.split("/")
                    if (not member.isfile() or any(part in ("", ".", "..") for part in parts)
                            or member.name in files or len(files) >= MAX_MEMBERS
                            or member.size > MAX_FILE_BYTES or member.size < 0):
                        raise ValueError("invalid_archive_member")
                    total += member.size
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError("archive_byte_limit")
                    h, size, body = hashlib.sha256(), 0, bytearray()
                    document = parts[-1] in ("delivery_manifest.json", "index.json")
                    if document and member.size > 4 * 1024 * 1024:
                        raise ValueError("archive_document_limit")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("archive_member_unreadable")
                    with source:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            if size > member.size:
                                raise ValueError("archive_member_size_changed")
                            h.update(chunk)
                            if document:
                                body.extend(chunk)
                    if size != member.size:
                        raise ValueError("archive_member_incomplete")
                    files[member.name] = {"size": size, "sha256": h.hexdigest()}
                    if document:
                        documents[member.name] = json.loads(body)
            out["refs"][0]["size"] = info.st_size
        root = "r2g_dataset_" + job["id"]
        index = documents.get(root + "/index.json")
        if not isinstance(index, dict) or index.get("job") != job["id"] or index.get("partial") is not False:
            raise ValueError("archive_index_not_final_job")
        designs = job.get("designs", [])
        if {d.get("name") for d in index.get("designs", [])} != {d["name"] for d in designs}:
            raise ValueError("archive_design_set_mismatch")
        for design in designs:
            prefix = root + "/" + design["name"] + "/"
            receipt = documents.get(prefix + "delivery_manifest.json")
            checked = reviews[design["name"]]
            if (not isinstance(receipt, dict) or receipt.get("job_id") != job["id"]
                    or receipt.get("design") != design["name"] or receipt.get("status") != "passed"
                    or receipt.get("missing_reports")
                    or receipt.get("source_snapshot_id") != _object(checked.get("input_snapshot")).get("sha256")
                    or receipt.get("output_snapshot_id") != _object(checked.get("output_snapshot")).get("sha256")
                    or receipt.get("acceptance_policy_sha256") != checked.get("policy_sha256")
                    or not checked.get("input_snapshot") or not checked.get("output_snapshot")):
                raise ValueError("delivery_manifest_does_not_bind_fresh_evidence")
            members = receipt.get("members", [])
            recorded = {entry["path"]: {"sha256": entry["sha256"], "size": entry["size"]} for entry in members}
            actual = {name[len(prefix):]: value for name, value in files.items()
                      if name.startswith(prefix) and name != prefix + "delivery_manifest.json"}
            if len(recorded) != len(members) or recorded != actual:
                raise ValueError("delivery_member_hash_or_membership_mismatch")
            for evidence in checked.get("evidence_files", []):
                if recorded.get(evidence.get("path"), {}).get("sha256") != evidence.get("sha256"):
                    raise ValueError("delivery_raw_evidence_changed")
            inputs = {}
            for folder in ("rtl", "constraints"):
                entries = sorted(({**entry, "path": entry["path"][len(folder) + 1:]}
                                  for entry in members if entry["path"].startswith(folder + "/")),
                                 key=lambda entry: entry["path"])
                inputs[folder] = {"files": entries, "sha256": digest(entries)}
            inputs["sha256"] = digest(inputs)
            if (inputs != checked["input_snapshot"]
                    or from_entries(members, _object(job.get("spec")).get("output", "graph")) != checked["output_snapshot"]):
                raise ValueError("delivery_input_or_dataset_snapshot_mismatch")
        expected_prefixes = [root + "/" + design["name"] + "/" for design in designs]
        if any(name != root + "/index.json" and not any(name.startswith(prefix) for prefix in expected_prefixes)
               for name in files):
            raise ValueError("unexpected_archive_scope")
        out.update(verdict="PASS", reasons=[])
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        out.update(verdict="STALE", reasons=["delivery_archive_not_verified:" + type(exc).__name__])
    return out


def _dataset_conditions(job):
    from .validation import review
    jid, conditions, reviews = job["id"], [], {}
    designs = job.get("designs") or []
    active = job.get("status") in ACTIVE
    all_passed = bool(designs) and all(d.get("status") == "passed" for d in designs)
    execution = "UNKNOWN" if active else "PASS" if job.get("status") == "done" and all_passed else "FAIL"
    conditions.append(_condition("job_execution", "当前作业的全部请求设计完成执行", execution,
                                 source="jobs.sqlite", refs=[{"job_id": jid}],
                                 reasons=[] if execution == "PASS" else ["job_not_finished" if active else "job_not_fully_successful"]))
    for design in designs:
        name = design["name"]
        project = design.get("project")
        checked = None
        if not active and project:
            try:
                checked = review(Path(project))
            except (OSError, ValueError, TypeError):
                checked = None
        reviews[name] = checked or {}
        refs = [{"job_id": jid, "design": name, "project": project,
                 "reader": "validation.review", "policy_sha256": _object(checked).get("policy_sha256")}]
        refs.extend({"project": project, "path": item.get("path"), "sha256": item.get("sha256")}
                    for item in _object(checked).get("evidence_files", []) if isinstance(item, dict))
        for layer in LAYERS:
            value = _object(_object(_object(checked).get("three_layer")).get(layer))
            conditions.append(_condition("design:" + name + ":" + layer, "三层验证：" + layer,
                                         value.get("verdict", "UNKNOWN"), design=name,
                                         source="host:validation.review", refs=refs,
                                         reasons=value.get("reasons") or ([] if value.get("verdict") == "PASS" else
                                                 ["evidence_pending" if active else "fresh_layer_evidence_missing"])))
        dataset = _object(_object(_object(checked).get("checks")).get("dataset"))
        conditions.append(_condition("design:" + name + ":dataset", "数据集检查通过，输入与产物快照已绑定",
                                     dataset.get("verdict", "UNKNOWN"), design=name,
                                     source="host:validation.review", refs=refs,
                                     reasons=dataset.get("reasons") or ([] if dataset.get("verdict") == "PASS" else
                                             ["dataset_evidence_pending" if active else "fresh_dataset_evidence_missing"])))
    if all(condition["verdict"] == "PASS" for condition in conditions):
        conditions.append(_archive_condition(job, reviews))
    else:
        package_error = _object(job.get("result")).get("package_error")
        conditions.append(_condition("delivery", "实际交付包与本次已验证证据一致",
                                     "FAIL" if package_error else "UNKNOWN", source="jobs.sqlite",
                                     refs=[{"job_id": jid, "path": _object(job.get("result")).get("package")}],
                                     reasons=[str(package_error)] if package_error else ["prior_completion_conditions_unresolved"]))
    return conditions


class TaskStateStore:
    """A separate ledger in the existing session SQLite database.

    New user turns get new tasks through ``ensure``. Continuations bind a turn
    explicitly, or look up a job within its session. Never attach an old task
    to a new request merely because it is the latest task in that session.
    """
    def __init__(self, database: Path):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS engineering_tasks (
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, root_turn_id TEXT NOT NULL UNIQUE,
                  goal TEXT NOT NULL, constraints TEXT NOT NULL, profile TEXT NOT NULL,
                  job_id TEXT, created REAL NOT NULL, updated REAL NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS engineering_task_turns (
                  turn_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS engineering_task_jobs (
                  session_id TEXT NOT NULL, job_id TEXT NOT NULL, task_id TEXT NOT NULL,
                  PRIMARY KEY(session_id,job_id));
                CREATE TABLE IF NOT EXISTS engineering_task_observations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  created REAL NOT NULL, observation TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS engineering_tasks_session ON engineering_tasks(session_id,created);
            """)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        value = dict(row)
        state = json.loads(value.pop("state"))
        value["constraints"] = json.loads(value["constraints"])
        return {"schema_version": 1, **value, **state}

    def get(self, task_id):
        with self.connection() as c:
            return self._row(c.execute("SELECT * FROM engineering_tasks WHERE id=?", (task_id,)).fetchone())

    def ensure(self, session_id, root_turn_id, goal, job_id=None, *, constraints=(), profile="dataset_delivery"):
        if profile not in PROFILES or not isinstance(goal, str) or not isinstance(constraints, (list, tuple)) or any(
                not isinstance(item, str) for item in constraints):
            raise ValueError("invalid host task contract")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM engineering_tasks WHERE root_turn_id=?", (root_turn_id,)).fetchone()
            if row:
                existing = self._row(row)
                if (existing["session_id"], existing["goal"], existing["profile"], existing["constraints"]) != (
                        session_id, goal, profile, list(constraints)):
                    raise ValueError("root turn already has another task contract")
                return existing
            task_id, now = uuid.uuid4().hex, time.time()
            state = {"phase": "input", "status": "ready", "conditions": [], "pending_evidence": [], "observation": None}
            initial = _compile_condition(None) if profile == "compile_only" else _condition(
                "job_execution", "提交并完成请求的 EDA 作业", source="host:task_contract",
                reasons=["job_not_observed" if job_id else "job_not_bound"])
            state["conditions"], state["pending_evidence"] = [initial], [initial]
            c.execute("INSERT INTO engineering_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (task_id, session_id, root_turn_id, goal, _json(list(constraints)), profile,
                       None, now, now, _json(state)))
            c.execute("INSERT INTO engineering_task_turns VALUES (?,?)", (root_turn_id, task_id))
            if job_id:
                self._bind_job(c, task_id, session_id, job_id)
        return self.get(task_id)

    def bind_turn(self, task_id, turn_id):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            task = c.execute("SELECT session_id FROM engineering_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("unknown task")
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'").fetchone():
                turn = c.execute("SELECT session_id FROM turns WHERE id=?", (turn_id,)).fetchone()
                if not turn or turn[0] != task[0]:
                    raise ValueError("turn is outside the task session")
            old = c.execute("SELECT task_id FROM engineering_task_turns WHERE turn_id=?", (turn_id,)).fetchone()
            if old and old[0] != task_id:
                raise ValueError("turn already belongs to another task")
            c.execute("INSERT OR IGNORE INTO engineering_task_turns VALUES (?,?)", (turn_id, task_id))

    @staticmethod
    def _bind_job(c, task_id, session_id, job_id):
        old = c.execute("SELECT task_id FROM engineering_task_jobs WHERE session_id=? AND job_id=?",
                        (session_id, job_id)).fetchone()
        if old and old[0] != task_id:
            raise ValueError("job already belongs to another task in this session")
        c.execute("INSERT OR IGNORE INTO engineering_task_jobs VALUES (?,?,?)", (session_id, job_id, task_id))
        c.execute("UPDATE engineering_tasks SET job_id=?,updated=? WHERE id=?", (job_id, time.time(), task_id))

    def bind_job(self, task_id, job_id):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT session_id,job_id FROM engineering_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("unknown task")
            if row[1] == job_id:
                return self._row(c.execute("SELECT * FROM engineering_tasks WHERE id=?", (task_id,)).fetchone())
            self._bind_job(c, task_id, row[0], job_id)
            # A child job inherits the goal, not its parent's completion verdict.
            c.execute("UPDATE engineering_tasks SET state=? WHERE id=?", (_json({
                "phase": "input", "status": "waiting_job", "conditions": [],
                "pending_evidence": [_condition("job_execution", "读取新绑定作业的最新证据", source="jobs.sqlite",
                                                refs=[{"job_id": job_id}], reasons=["job_not_observed"])],
                "observation": None}), task_id))
        return self.get(task_id)

    def fork_for_submission(self, turn_id, job_id, action_id):
        """Derive new work from a completed user submission, never model prose.

        A follow-up may initially read the previous job. Once that user turn
        actually submits a new job, retain the old task and give the new work
        the current user's goal. Repair children continue their original task.
        The single transaction also reconciles a crash after action commit.
        """
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            turn = c.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            action = c.execute("SELECT a.* FROM actions a JOIN job_links l ON a.id=l.action_id "
                "WHERE a.id=? AND a.turn_id=? AND l.turn_id=a.turn_id AND l.job_id=? "
                "AND l.session_id=a.session_id", (action_id, turn_id, job_id)).fetchone()
            if (not turn or turn["kind"] != "user" or json.loads(turn["payload"]).get("grant_id")
                    or not action or action["session_id"] != turn["session_id"]
                    or action["status"] != "complete" or action["tool_name"] not in {"start_job", "start_batch"}):
                raise ValueError("new task requires a completed ordinary user submission")
            result = json.loads(action["result"] or "{}")
            if not isinstance(result, dict) or result.get("error") or result.get("job_id") != job_id:
                raise ValueError("submission result does not establish this job")
            old = c.execute("SELECT t.* FROM engineering_tasks t JOIN engineering_task_turns x "
                            "ON t.id=x.task_id WHERE x.turn_id=?", (turn_id,)).fetchone()
            if not old:
                raise ValueError("submission turn has no source task")
            known = c.execute("SELECT task_id FROM engineering_task_jobs WHERE session_id=? AND job_id=?",
                              (turn["session_id"], job_id)).fetchone()
            if known:
                if known[0] != old["id"]:
                    raise ValueError("job already belongs to another task")
                return self._row(old)  # An old ancestor must not replace its child.
            now = time.time()
            state = {"phase": "input", "status": "waiting_job", "conditions": [],
                     "pending_evidence": [_condition("job_execution", "读取新提交作业的当前证据",
                        source="jobs.sqlite", refs=[{"job_id": job_id}], reasons=["job_not_observed"])],
                     "observation": None}
            if old["root_turn_id"] == turn_id:
                task_id = old["id"]
                c.execute("UPDATE engineering_tasks SET state=? WHERE id=?", (_json(state), task_id))
            else:
                task_id = uuid.uuid4().hex
                goal = json.loads(turn["payload"])["message"]
                c.execute("INSERT INTO engineering_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (task_id, turn["session_id"], turn_id, goal, old["constraints"], old["profile"],
                           None, now, now, _json(state)))
                c.execute("UPDATE engineering_task_turns SET task_id=? WHERE turn_id=? AND task_id=?",
                          (task_id, turn_id, old["id"]))
            self._bind_job(c, task_id, turn["session_id"], job_id)
            return self._row(c.execute("SELECT * FROM engineering_tasks WHERE id=?", (task_id,)).fetchone())

    def get_for_turn(self, turn_id):
        with self.connection() as c:
            return self._row(c.execute("SELECT t.* FROM engineering_tasks t JOIN engineering_task_turns x "
                                      "ON t.id=x.task_id WHERE x.turn_id=?", (turn_id,)).fetchone())

    def get_for_job(self, session_id, job_id):
        with self.connection() as c:
            return self._row(c.execute("SELECT t.* FROM engineering_tasks t JOIN engineering_task_jobs x "
                                      "ON t.id=x.task_id WHERE x.session_id=? AND x.job_id=?",
                                      (session_id, job_id)).fetchone())

    def get_for_session(self, session_id):
        """Administrative latest-task lookup; never an implicit turn binding."""
        with self.connection() as c:
            return self._row(c.execute("SELECT * FROM engineering_tasks WHERE session_id=? ORDER BY created DESC,id DESC LIMIT 1",
                                      (session_id,)).fetchone())

    def observe(self, task_id, job=None, evidence=None):
        task = self.get(task_id)
        if not task:
            raise ValueError("unknown task")
        now = time.time()
        if task["profile"] == "compile_only":
            conditions = [_compile_condition(evidence)]
            phase, status = "input", "complete" if conditions[0]["verdict"] == "PASS" else "blocked"
            observation = {"source": "host:fixed_compile_receipt", "observed_at": now,
                           "scope": "compilation_only; no simulation, signoff or dataset claim"}
        else:
            if not isinstance(job, dict) or job.get("id") != task["job_id"]:
                raise ValueError("observation does not match the current bound job")
            conditions = _dataset_conditions(job)
            focused = next((d for d in job.get("designs", []) if d.get("status") == "failed"),
                           next(iter(job.get("designs", [])), {}))
            phase = _object(focused.get("detail")).get("failed_stage") or focused.get("stage") or "input"
            if all(condition["verdict"] == "PASS" for condition in conditions):
                status = "complete"
            elif job.get("status") == "cancelled":
                status = "cancelled"
            else:
                status = "waiting_job" if job.get("status") in ACTIVE else "blocked"
            observation = {"source": "jobs.sqlite+fresh_evidence_readers", "job_id": job["id"],
                           "job_status": job.get("status"), "job_updated": job.get("updated"), "observed_at": now}
        state = {"phase": phase, "status": status, "conditions": conditions,
                 "pending_evidence": [condition for condition in conditions if condition["verdict"] != "PASS"],
                 "observation": observation}
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT job_id FROM engineering_tasks WHERE id=?", (task_id,)).fetchone()
            if not current or current[0] != task["job_id"]:
                raise ValueError("task job changed during evidence review")
            c.execute("UPDATE engineering_tasks SET state=?,updated=? WHERE id=?", (_json(state), now, task_id))
            c.execute("INSERT INTO engineering_task_observations(task_id,created,observation) VALUES (?,?,?)",
                      (task_id, now, _json(state)))
        return self.get(task_id)

    def view(self, task_id):
        """Bounded model view; goals/constraints remain explicit and unsummarized."""
        task = self.get(task_id)
        if task is None:
            return None
        task = redact(task)
        for key in ("conditions", "pending_evidence"):
            values = task[key]
            task[key] = [{**value, "refs": value["refs"][:4], "reasons": value["reasons"][:8]} for value in values[:24]]
            task["omitted_" + key] = max(0, len(values) - 24)
        task["completion_authority"] = "host evidence readers; model prose and a completed turn cannot award PASS"
        task["read_with"] = {"tool": "task_status", "arguments": {}}
        return task
