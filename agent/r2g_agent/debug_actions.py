"""One source-bound recovery: complete an existing input compilation list.

No RTL bytes, constraints, tool flags or acceptance rules are editable here.
The only candidate is a dependency already copied from the authorized source
directory but omitted by its explicit design.json compilation list. A failed
child never gains another recovery round.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from . import capabilities, design_checks, project
from .input_snapshots import capture, digest, manifest, read_tree, write_json
from .submissions import source_snapshot
from .tool_outputs import ToolContext
from .turns import TurnStore


class DebugDenied(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS dependency_debug (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_job_id TEXT NOT NULL,
 status TEXT NOT NULL, created REAL NOT NULL, deadline REAL NOT NULL,
 scope TEXT NOT NULL, candidate TEXT, child_job_id TEXT, outcome TEXT NOT NULL DEFAULT '{}',
 UNIQUE(session_id,parent_job_id)
);
"""
_SAFE = re.compile(r"[A-Za-z0-9_.\-/]+\Z")
_MISSING = re.compile(r"unknown module|module .*not part|module .*not found|unable to find.*module|not found in the design", re.I | re.S)
_INFRA = re.compile(r"permission denied|no space left|command not found|not installed|timed? ?out", re.I)


def _relative(name):
    return (isinstance(name, str) and _SAFE.fullmatch(name) and not name.startswith("/")
            and name == Path(name).as_posix() and ".." not in Path(name).parts)


def _write_tree(root, files):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _policy():
    return {Path(module).name: hashlib.sha256(Path(module).read_bytes()).hexdigest()
            for module in (__file__, project.__file__, design_checks.__file__)}


class DependencyDebugController:
    def __init__(self, database: Path, jobs, pipeline=None):
        self.database, self.jobs, self.pipeline = Path(database), jobs, pipeline
        self.root = self.database.parent / "dependency-debug"
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

    def authority(self, sid: str, jid: str) -> dict | None:
        with self._c() as c:
            row = c.execute("SELECT * FROM dependency_debug WHERE session_id=? AND parent_job_id=?", (sid, jid)).fetchone()
        if not row:
            return None
        value = dict(row)
        for name in ("scope", "candidate", "outcome"):
            value[name] = json.loads(value[name]) if value[name] else None
        return value

    def authorize(self, sid: str, jid: str, *, wall_seconds: int = 900) -> dict:
        """Host-only delegation of one unchanged-source input-list recovery.

        Call when failure is observed, rather than starting its clock while a
        potentially long physical job is still running. Repeated calls do not
        extend the deadline or replenish the single attempt.
        """
        previous = self.authority(sid, jid)
        if previous:
            return previous
        if type(wall_seconds) is not int or not 30 <= wall_seconds <= 900:
            raise DebugDenied("dependency debug wall-clock budget must be 30..900 seconds")
        job = self.jobs.job(jid)
        submitted = self.jobs.submission(jid)
        if (not job or job["status"] != "failed" or not submitted or submitted.get("session_id") != sid
                or jid not in TurnStore(self.database).linked_jobs(sid)):
            raise DebugDenied("dependency debug requires this session's terminal failed submission")
        sources = job.get("spec", {}).get("sources") or []
        if not sources or any(s.get("dependency_debug_receipt") or s.get("repair_receipt") for s in sources):
            raise DebugDenied("a recovery child cannot automatically start another recovery")
        expected = submitted.get("source_snapshot")
        if not expected or job["spec"].get("source_snapshot") != expected or source_snapshot(sources) != expected:
            raise DebugDenied("the authorized source version changed since submission")
        roots = []
        for source in sources:
            root = Path(source["path"]).absolute()
            if not root.is_dir():
                raise DebugDenied("a single-file submission does not authorize sibling dependencies")
            files = read_tree(root)
            metadata = json.loads(files.get("design.json", b"{}"))
            listed = metadata.get("files")
            if not isinstance(listed, list) or not listed or any(not _relative(name) or name not in files for name in listed):
                raise DebugDenied("dependency recovery requires an explicit existing design.json.files list")
            roots.append({"path": str(root), "name": source.get("name"), "tree": manifest(files)})
        scope = {"source_snapshot": expected, "spec_sha256": digest(job["spec"]),
                 "roots": roots, "action": "complete_existing_compile_list", "max_attempts": 1,
                 "rtl_mutation": False, "acceptance_unchanged": True, "policy": _policy()}
        now, aid = time.time(), uuid.uuid4().hex
        with self._c() as c:
            c.execute("INSERT OR IGNORE INTO dependency_debug "
                      "(id,session_id,parent_job_id,status,created,deadline,scope) VALUES (?,?,?,?,?,?,?)",
                      (aid, sid, jid, "approved", now, now + wall_seconds, json.dumps(scope)))
        return self.authority(sid, jid)

    def _candidate(self, authority, design, event_id):
        jid = authority["parent_job_id"]
        job, record = self.jobs.job(jid), self.jobs.design(jid, design)
        if not job or job["status"] != "failed" or not record or record["status"] != "failed":
            raise DebugDenied("only a terminal failed design can recover its compilation list")
        if digest(job["spec"]) != authority["scope"]["spec_sha256"]:
            raise DebugDenied("job parameters changed since delegation")
        if authority["scope"].get("policy") != _policy():
            raise DebugDenied("dependency-debug policy version changed since delegation")
        if type(event_id) is not int or event_id < 1:
            raise DebugDenied("a positive recorded failure event ID is required")
        events = self.jobs.events(jid, after=event_id - 1, limit=1)
        event = events[0] if events and events[0]["id"] == event_id else {}
        text = str(event.get("message", "")) + "\n" + str((event.get("data") or {}).get("log_tail", ""))
        detail = record.get("detail") or {}
        if (event.get("design") != design or event.get("kind") != "fail" or event.get("stage") != "synth"
                or detail.get("failed_stage") != "synth" or not _MISSING.search(text) or _INFRA.search(text)):
            raise DebugDenied("the recorded synthesis failure must identify a missing module, not an environment failure")
        before = capture(Path(record["project"]))
        binding = detail.get("input_bindings", {}).get("synth", {})
        if (binding.get("unchanged") is not True or binding.get("before", {}).get("sha256") != before["sha256"]
                or binding.get("after", {}).get("sha256") != before["sha256"]
                or type(binding.get("exit_code")) is not int or binding["exit_code"] == 0):
            raise DebugDenied("missing-module evidence is not bound to current RTL and constraints")
        sources = job["spec"]["sources"]
        if source_snapshot(sources) != authority["scope"]["source_snapshot"]:
            raise DebugDenied("authorized source bytes changed since submission")
        roots = authority["scope"]["roots"]
        matches = [i for i, source in enumerate(sources) if source.get("name") == design]
        if len(matches) != 1:
            raise DebugDenied("design must map to exactly one authorized source directory")
        entry = matches[0]
        root = Path(roots[entry]["path"])
        files = read_tree(root)
        if manifest(files) != roots[entry]["tree"]:
            raise DebugDenied("the original directory changed after delegation")
        rtl = read_tree(Path(record["project"]) / "rtl")
        original_hdl = {name: value for name, value in files.items() if Path(name).suffix.lower() in project.HDL_SUFFIXES}
        if rtl != original_hdl or manifest(rtl) != before["rtl"]:
            raise DebugDenied("recovery requires byte-identical originally copied HDL; frontend edits need their own policy")
        if len(rtl) > 128 or sum(map(len, rtl.values())) > design_checks.MAX_BYTES:
            raise DebugDenied("dependency closure exceeds the fixed diagnostic input budget")
        metadata = json.loads(files["design.json"])
        listed = metadata["files"]
        if any(not _relative(name) or name not in rtl or Path(name).suffix.lower() not in (".v", ".sv") for name in listed):
            raise DebugDenied("the original compilation units must be existing HDL source files")
        top = detail.get("top")
        if not top or sources[entry].get("top") not in (None, top) or metadata.get("top") not in (None, top):
            raise DebugDenied("top selection cannot change during input recovery")
        decoded = {name: value.decode("utf-8") for name, value in rtl.items()}
        observed = design_checks._inspect(root, rtl, decoded, top)
        if observed["findings"] or len(observed["modules"]) >= 64:
            raise DebugDenied("ambiguous or unsupported module definitions cannot be recovered automatically")
        modules = {m["name"]: m for m in observed["modules"]}
        selected, added = set(listed), set()
        while True:
            missing = {dependency for module in modules.values() if module["file"] in selected
                       for dependency in module["dependencies"] if dependency not in modules
                       or modules[dependency]["file"] not in selected}
            if not missing:
                break
            if any(name not in modules for name in missing):
                raise DebugDenied("a missing dependency is absent from the originally authorized source directory")
            extension = {modules[name]["file"] for name in missing}
            if any(Path(name).suffix.lower() not in (".v", ".sv") for name in extension):
                raise DebugDenied("a missing module is not a standalone HDL compilation unit")
            added.update(extension - selected)
            selected.update(extension)
        if not added:
            raise DebugDenied("no omitted dependency exists in the authorized compilation list")
        updated = {**metadata, "files": list(listed) + sorted(added)}
        candidate = {**rtl, "design.json": json.dumps(updated, indent=2, ensure_ascii=False).encode()}
        # Cheap checks must use the same compilation units as the child. Its
        # private diagnostic copy includes quoted header closure, rather than
        # accidentally passing by compiling every original HDL file.
        check_files = {name: rtl[name] for name in selected}
        pending = list(selected)
        while pending:
            name = pending.pop()
            for ref in re.findall(r'`include\s+"([^"\n]+)"', project._strip_comments(decoded[name])):
                dependency = design_checks._reference(rtl, name, ref)
                if not dependency:
                    raise DebugDenied("an include is outside the originally copied dependency closure")
                if dependency not in check_files:
                    check_files[dependency] = rtl[dependency]
                    pending.append(dependency)
        payload = {"design": design, "evidence_event_id": event_id, "before": before,
                   "top": top, "added_compile_units": sorted(added), "original_compile_units": listed,
                   "candidate_snapshot": manifest(candidate), "diagnostic_snapshot": manifest(check_files),
                   "spec": {key: job["spec"].get(key) for key in ("platform", "output", "params")}}
        return payload, candidate, check_files, record

    @staticmethod
    def public(value):
        return {"debug_attempt": value["id"], "job_id": value["parent_job_id"], "status": value["status"],
                "child_job_id": value["child_job_id"], "deadline": value["deadline"],
                "candidate": value["candidate"], "outcome": value["outcome"],
                "rtl_modified": False, "functional_verification": "not_run", "delivered": False}

    def execute(self, context: ToolContext, *, job_id: str, design: str, evidence_event_id: int) -> dict:
        if context.read_only or not context.turn_id or not context.action_id or context.job_id not in (None, job_id):
            raise DebugDenied("dependency recovery requires a scoped durable writable turn and action")
        turn = TurnStore(self.database).get(context.turn_id)
        if not turn or turn["session_id"] != context.session_id or turn["kind"] not in ("user", "debug_review"):
            raise DebugDenied("only an original user turn or host debug review may execute dependency recovery")
        authority = self.authority(context.session_id, job_id)
        if not authority:
            raise DebugDenied("host dependency-debug delegation is required")
        if authority["status"] != "approved":
            candidate = authority["candidate"] or {}
            if candidate and (candidate.get("design"), candidate.get("evidence_event_id")) != (design, evidence_event_id):
                raise DebugDenied("the one delegated attempt is already bound to another candidate")
            return self.public(authority)
        if time.time() >= authority["deadline"]:
            raise DebugDenied("dependency-debug wall-clock budget exhausted")
        payload, candidate, check_files, record = self._candidate(authority, design, evidence_event_id)
        with self._c() as c:
            changed = c.execute("UPDATE dependency_debug SET status='executing',candidate=? WHERE id=? "
                                "AND status='approved' AND deadline>?", (json.dumps(payload), authority["id"], time.time())).rowcount
        if not changed:
            return self.public(self.authority(context.session_id, job_id))
        try:
            current = self.root
            while current != current.parent:
                if current.is_symlink():
                    raise DebugDenied("dependency-debug workspace cannot traverse symlinks")
                current = current.parent
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            workspace = self.root / authority["id"]
            workspace.mkdir(mode=0o700, exist_ok=False)
            _write_tree(workspace / "source", candidate)
            _write_tree(workspace / "diagnostic", check_files)
            # A custom SDC or synthesis override must not disappear when a
            # fresh child project is created from the original flow profile.
            # Reconstruct the parent configuration, normalize only its private
            # absolute project path, and require all constraint bytes to agree.
            expected_project = workspace / "expected-parent"
            origin = next(root["path"] for root in authority["scope"]["roots"] if root["name"] == design)
            project.create(Path(origin), expected_project, payload["spec"]["platform"], top=payload["top"],
                           name=design, params=payload["spec"]["params"])
            expected_constraints = read_tree(expected_project / "constraints")
            actual_constraints = {name: value.replace(str(Path(record["project"])).encode(), str(expected_project).encode())
                                  for name, value in read_tree(Path(record["project"]) / "constraints").items()}
            if actual_constraints != expected_constraints:
                raise DebugDenied("custom parent constraints or overrides require a separately registered recovery policy")
            inventory = capabilities.tool_inventory(("iverilog", "yosys"))
            if time.time() + design_checks.CHECK_SECONDS >= authority["deadline"]:
                raise DebugDenied("insufficient dependency-debug time remaining for a bounded compiler check")
            checks = [design_checks.check_design(str(workspace / "diagnostic"), payload["top"], "compile", inventory=inventory)]
            if checks[-1]["status"] == "passed" and inventory.get("tools", {}).get("yosys", {}).get("usable"):
                if time.time() + design_checks.CHECK_SECONDS >= authority["deadline"]:
                    raise DebugDenied("insufficient dependency-debug time remaining for a bounded synthesis check")
                checks.append(design_checks.check_design(str(workspace / "diagnostic"), payload["top"], "synthesis_structure", inventory=inventory))
            write_json(workspace / "cheap_checks.json", checks)
            if any(check["status"] != "passed" for check in checks):
                self._finish(authority["id"], "check_failed", {"checks": checks, "reason": "new independent diagnostics did not pass; no child submitted"})
            else:
                # Re-read every bound source after the compilers. An apparent
                # success on stale copied inputs never authorizes the child.
                current_payload, current_candidate, _, _ = self._candidate(authority, design, evidence_event_id)
                if current_payload != payload or current_candidate != candidate or manifest(read_tree(workspace / "source")) != payload["candidate_snapshot"]:
                    raise DebugDenied("source or evidence changed during recovery checks")
                if time.time() >= authority["deadline"]:
                    raise DebugDenied("dependency-debug wall-clock budget exhausted before child submission")
                receipt = {"attempt_id": authority["id"], "parent_job_id": job_id, "before": payload["before"],
                           "candidate_snapshot": payload["candidate_snapshot"], "added_compile_units": payload["added_compile_units"],
                           "checks": checks, "deadline": authority["deadline"], "allow_recursive_debug": False,
                           "rtl_modified": False, "scope_sha256": digest(authority["scope"])}
                jid = self.pipeline.submit(
                    [{"name": design, "path": str(workspace / "source"), "top": payload["top"], "dependency_debug_receipt": receipt}],
                    platform=payload["spec"]["platform"], output=payload["spec"]["output"], params=payload["spec"]["params"],
                    title="输入依赖恢复验证 · " + design,
                    submission={"request_key": "dependency-debug:" + authority["id"], "session_id": context.session_id,
                                "turn_id": context.turn_id, "action_id": context.action_id})
                with self._c() as c:
                    c.execute("UPDATE dependency_debug SET status='running',child_job_id=?,outcome=? WHERE id=?",
                              (jid, json.dumps({"checks": checks, "full_flow_validation": "pending"}), authority["id"]))
        except Exception as exc:
            uncertain = not isinstance(exc, DebugDenied)
            self._finish(authority["id"], "recovery_required" if uncertain else "stopped",
                         {"reason": str(exc), "execution_may_have_completed": uncertain})
            raise
        return self.public(self.authority(context.session_id, job_id))

    def _finish(self, aid, status, outcome):
        with self._c() as c:
            c.execute("UPDATE dependency_debug SET status=?,outcome=? WHERE id=?", (status, json.dumps(outcome), aid))

    def recover(self):
        """Startup only: uncertain external effects require reconciliation."""
        with self._c() as c:
            c.execute("UPDATE dependency_debug SET status='recovery_required' WHERE status='executing'")

    def tick(self):
        with self._c() as c:
            rows = [dict(row) for row in c.execute("SELECT * FROM dependency_debug WHERE status IN ('running','executing','recovery_required')")]
        for row in rows:
            if not row["child_job_id"]:
                submitted = self.jobs.job_for_submission("dependency-debug:" + row["id"])
                if submitted:
                    row["child_job_id"] = submitted["job_id"]
                    with self._c() as c:
                        c.execute("UPDATE dependency_debug SET status='running',child_job_id=? WHERE id=?", (submitted["job_id"], row["id"]))
            job = self.jobs.job(row["child_job_id"]) if row["child_job_id"] else None
            if job and job["status"] in ("done", "failed", "cancelled") and job["updated"] <= row["deadline"]:
                self._finish(row["id"], "flow_finished" if job["status"] == "done" else "stopped",
                             {"job_id": job["id"], "status": job["status"], "result": job.get("result"),
                              "note": "the existing delivery and three-layer evidence checks remain authoritative"})
            elif time.time() >= row["deadline"]:
                if job and job["status"] not in ("done", "failed", "cancelled"):
                    self.pipeline.cancel(job["id"])
                self._finish(row["id"], "budget_exhausted", {"reason": "dependency-debug deadline reached; active child cancellation requested",
                             "execution_may_have_completed": not job or job["status"] not in ("done", "failed", "cancelled")})
