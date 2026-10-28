"""Confirmed, resumable deletion of task-owned generated artifacts."""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from . import housekeeping
from .lifecycle import resource_lock
from .paths import r2g_home
from .processes import execution_members, group_members, proc_start, stop_orphan

ORFS_ARTIFACT_KINDS = ('designs', 'results', 'logs', 'reports', 'objects')


def legacy_created_jobs(messages: list[dict]) -> set[str]:
    """Only successful submission results establish ownership, never mentions."""
    submissions = set()
    jobs = set()
    for message in messages:
        if message.get('role') == 'assistant':
            submissions.update(t.get('id') for t in message.get('tool_calls', [])
                               if t.get('name') in ('start_job', 'start_batch'))
        if message.get('role') != 'tool' or message.get('tool_call_id') not in submissions:
            continue
        try:
            result = json.loads(message.get('content', ''))
            if isinstance(result, dict) and not result.get('error') and isinstance(result.get('job_id'), str):
                jobs.add(result['job_id'])
        except (ValueError, TypeError):
            pass
    return jobs


def _safe_path(path: Path, root: Path) -> Path:
    """Allow unlinking a leaf symlink, never traversal through a symlink."""
    path, root = path.absolute(), root.absolute()
    rel = path.relative_to(root)
    if not rel.parts or '..' in rel.parts:
        raise ValueError('拒绝删除存储根目录或越界路径')
    parent = root
    if root.is_symlink():
        raise ValueError('存储目录是符号链接，未删除任何数据')
    for part in rel.parts[:-1]:
        parent /= part
        if parent.is_symlink():
            raise ValueError('产物路径经过符号链接，拒绝越界删除')
    return path


def artifact_paths(job: dict, home: Path | None = None, flow: Path | None = None) -> list[Path]:
    jid = job['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', jid):
        raise ValueError('invalid job identifier')
    home = (home or r2g_home()).resolve()
    workspace = _safe_path(home / 'workspace' / jid, home)
    paths = set()
    flow = flow if flow is not None else housekeeping.orfs_flow_dir()
    variant_pattern = re.compile(r'[A-Za-z0-9_-]+__' + re.escape(jid))
    if workspace.is_dir() and not workspace.is_symlink():
        for project in workspace.iterdir():
            if not project.is_dir() or project.is_symlink() or not variant_pattern.fullmatch(project.name):
                continue
            if flow:
                # metadata.json is written before ORFS starts; run-meta.json is
                # written only after it finishes. Both identify the exact variant.
                for meta in [project / 'metadata.json', *(project / 'backend').glob('RUN_*/run-meta.json')]:
                    _safe_path(meta, home)
                    if meta.is_symlink():
                        raise ValueError('产物元数据是符号链接，拒绝读取外部路径')
                    try:
                        m = json.loads(meta.read_text())
                    except (OSError, ValueError):
                        continue
                    if meta.name == 'run-meta.json' and m.get('flow_variant') != project.name:
                        continue
                    platform, design = m.get('platform', ''), m.get('design_name', '')
                    if not all(re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in (platform, design)):
                        raise ValueError('ORFS 产物元数据包含无效路径')
                    for kind in ORFS_ARTIFACT_KINDS:
                        paths.add(_safe_path(flow / kind / platform / design / project.name, flow))
    if flow:
        # Also find interrupted runs without readable metadata, including an
        # already removed workspace. Only the exact __<job id> suffix owns a
        # variant: substring matches could erase another task's files.
        # Traverse exactly platform/design/variant, never symlinked ancestors.
        for kind in ORFS_ARTIFACT_KINDS:
            directory = _safe_path(flow / kind, flow)
            if directory.is_symlink():
                raise ValueError('ORFS 产物目录是符号链接，拒绝越界删除')
            if not directory.is_dir():
                continue
            for platform in directory.iterdir():
                if platform.is_symlink() or not platform.is_dir():
                    continue
                for design in platform.iterdir():
                    if design.is_symlink() or not design.is_dir():
                        continue
                    for variant in design.iterdir():
                        if variant_pattern.fullmatch(variant.name):
                            paths.add(_safe_path(variant, flow))
    paths.add(workspace)
    for suffix in ('.tar.gz', '.tar.tmp', '_partial.tar.gz', '_partial.tar.tmp'):
        paths.add(_safe_path(home / 'datasets' / ('r2g_dataset_' + jid + suffix), home))
    paths.add(_safe_path(home / 'datasets' / 'per_design' / jid, home))
    return sorted(paths)


class DeletionManager:
    def __init__(self, store, sessions, pipeline):
        self.store, self.sessions, self.pipeline = store, sessions, pipeline
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name='r2g-deletions')
            self._thread.start()

    def close(self):
        self._stop.set()

    def request(self, kind: str, target: str) -> dict:
        if kind not in ('job', 'session') or not re.fullmatch(r'[A-Za-z0-9_-]+', target):
            raise ValueError('invalid deletion target')
        previous = self.store.deletion(kind, target)
        if previous and previous['status'] == 'done':
            return previous
        exists = self.store.job(target) if kind == 'job' else self.sessions.exists(target)
        if not exists:
            raise KeyError(target)
        request = self.store.request_deletion(kind, target)
        if kind == 'session':
            self.sessions.mark_deleting(target)
            if previous and previous['status'] == 'error':
                for jid in set(self.store.owned_jobs(target)) | legacy_created_jobs(self.sessions.load(target)):
                    job = self.store.job(jid)
                    child = self.store.deletion('job', jid)
                    if job and job['spec'].get('session_id') in (None, target) and child and child['status'] == 'error':
                        self.request('job', jid)
        else:
            self.pipeline.cancel(target)
        return request

    def _run(self):
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:
                # A busy database does not abandon the durable deletion queue.
                pass
            self._stop.wait(2)

    def process_once(self):
        for request in self.store.pending_deletions():
            kind, target = request['kind'], request['target']
            try:
                with resource_lock(kind, target, exclusive=True, blocking=False):
                    done = self._job(target) if kind == 'job' else self._session(target)
                    if done:
                        self.store.set_deletion(kind, target, 'done')
            except BlockingIOError:
                continue  # A stage, archive, or conversation is still exiting.
            except Exception as exc:
                self.store.set_deletion(kind, target, 'error', str(exc))

    def _session(self, sid: str) -> bool:
        self.sessions.mark_deleting(sid)
        jobs = set(self.store.owned_jobs(sid)) | legacy_created_jobs(self.sessions.load(sid))
        waiting = False
        for jid in jobs:
            # Explicit ownership takes precedence over ambiguous legacy history.
            job = self.store.job(jid)
            if job and job['spec'].get('session_id') not in (None, sid):
                continue
            deletion = self.store.deletion('job', jid)
            if deletion and deletion['status'] == 'error':
                raise ValueError('关联任务删除失败：' + jid + '；' + deletion['error'])
            if job:
                if not deletion:
                    self.request('job', jid)
                waiting = True
        if waiting:
            return False
        self.sessions.erase(sid)
        return True

    def _job(self, jid: str) -> bool:
        job = self.store.job(jid)
        if not job:
            return True
        self.pipeline.cancel(jid)
        # A dead executor may have left a conservative "unconfirmed" record.
        for design in job['designs']:
            detail = design['detail']
            if design['status'] == 'running':
                owner = detail.get('owner', '').split(':')
                alive = len(owner) == 2 and owner[0].isdigit() and proc_start(int(owner[0])) == owner[1]
                if alive and '无法确认子进程已停止' not in detail.get('reason', ''):
                    return False
                if not stop_orphan(detail.get('pgid'), design['project'],
                                   expected_start=detail.get('process_start'), execution_id=detail.get('execution_id')):
                    return False
                self.store.set_design(jid, design['name'], status='cancelled')
        job = self.store.job(jid)
        if job['status'] == 'finalizing' or any(d['status'] in ('pending', 'running') for d in job['designs']):
            return False
        # Completed rows can still hold a stale pgid. Check inherited markers,
        # never signal a recycled PID based on its number alone.
        for design in job['designs']:
            dt = design['detail']
            marker, start = dt.get('execution_id'), dt.get('process_start')
            if marker and start and execution_members(marker, start):
                if not stop_orphan(dt.get('pgid'), design['project'], expected_start=start, execution_id=marker):
                    return False
            elif dt.get('pgid') and group_members(dt['pgid']):
                if start and proc_start(dt['pgid']) == start:
                    if not stop_orphan(dt['pgid'], design['project'], expected_start=start):
                        return False
        paths = artifact_paths(job)
        # Generated RTL may have been used as another job's input. Do not
        # silently break that job, even if the original source was uploaded.
        generated = [path.resolve() for path in paths if not path.is_symlink()]
        with self.store._conn() as c:
            others = c.execute('SELECT id,spec FROM jobs WHERE id!=?', (jid,)).fetchall()
        for other in others:
            for source in json.loads(other['spec']).get('sources', []):
                if source.get('path') and any(Path(source['path']).resolve().is_relative_to(path) for path in generated):
                    deletion = self.store.deletion('job', other['id'])
                    if deletion and deletion['status'] == 'pending':
                        return False  # Let the dependent job stop and disappear first.
                    raise ValueError('任务 ' + other['id'] + ' 仍引用这些产物，请先删除该关联任务')
        for path in paths:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        # Re-discover after cleanup rather than trusting a successful unlink
        # call or incomplete run metadata. Keep the database row retryable if
        # any writer or failed filesystem removal has left generated artifacts.
        remaining = [path for path in set(paths) | set(artifact_paths(job))
                     if path.exists() or path.is_symlink()]
        if remaining:
            raise ValueError('清理后仍存在任务产物，未标记删除完成：' + str(remaining[0]))
        self.store.erase_job(jid)
        return True
