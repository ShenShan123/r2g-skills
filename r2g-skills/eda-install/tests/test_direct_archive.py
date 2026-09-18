"""Installer safety controls; fixture tools do not establish EDA capability."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from unittest.mock import patch

import pytest

EDA = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("direct_archive", EDA / "scripts/setup/direct_archive.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def fixture(tmp_path, members=None, **changes):
    prefix = tmp_path / "bundle"
    cache = prefix / ".r2g-downloads"
    cache.mkdir(parents=True)
    archive = cache / "fixture.tgz"
    if members is None:
        member = tarfile.TarInfo("payload/bin/tool")
        member.mode = 0o755
        body = b"#!/bin/sh\nprintf 'fixture tool\\n'\n"
        members = [(member, body)]
    with tarfile.open(archive, "w:gz") as stream:
        for member, body in members:
            if member.isreg():
                member.size = len(body)
            stream.addfile(member, io.BytesIO(body) if member.isreg() else None)
    row = dict(url="https://example.invalid/fixture.tgz", sha256=installer.sha(archive),
               size_bytes=archive.stat().st_size, archive_root="payload", max_expanded_bytes=4096,
               max_members=20, runtime_probes={"bin/tool": []})
    row.update(changes)
    archive.rename(cache / (row["sha256"] + ".tgz"))
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"schema": "r2g-direct-artifacts-v1",
                               "components": {"frontend": {installer.host_key(): row}}}))
    return prefix, lock, row


def test_install_and_replay_are_bound_and_offline(tmp_path):
    prefix, lock, row = fixture(tmp_path)
    with patch.object(installer.subprocess, "run", side_effect=AssertionError("network forbidden")):
        receipt = installer.install(prefix, lock, "frontend", row, offline=True)
        assert installer.install(prefix, lock, "frontend", row, offline=True) == receipt
    assert receipt["artifact_lock_sha256"] == installer.sha(lock)
    assert receipt["inventory"] == installer.inventory(prefix / "payload")
    assert receipt["production_authority"] is False
    assert receipt["native_read_closure_proved"] is False


@pytest.mark.parametrize("fault", ["bytes", "mode", "receipt", "lock", "authority"])
def test_existing_install_drift_fails_without_overwrite(tmp_path, fault):
    prefix, lock, row = fixture(tmp_path)
    installer.install(prefix, lock, "frontend", row, offline=True)
    tool = prefix / "payload/bin/tool"
    receipt = prefix / ".r2g-install/frontend.json"
    if fault == "bytes":
        tool.write_text("changed")
    elif fault == "mode":
        tool.chmod(0o700)
    elif fault == "lock":
        lock.write_text(lock.read_text() + "\n")
    else:
        value = json.loads(receipt.read_text())
        value["production_authority" if fault == "authority" else "component"] = True
        value["receipt_digest"] = installer.digest({k: v for k, v in value.items() if k != "receipt_digest"})
        receipt.write_text(json.dumps(value))
    before = (tool.read_bytes(), receipt.read_bytes())
    with pytest.raises(installer.InstallError):
        installer.install(prefix, lock, "frontend", row, offline=True)
    assert before == (tool.read_bytes(), receipt.read_bytes())


@pytest.mark.parametrize("fault", ["wrong_sha", "wrong_size", "symlink", "missing"])
def test_bad_cache_is_retained_not_installed(tmp_path, fault):
    prefix, lock, row = fixture(tmp_path)
    archive = prefix / ".r2g-downloads" / (row["sha256"] + ".tgz")
    if fault == "wrong_sha":
        archive.write_bytes(b"x" * row["size_bytes"])
    elif fault == "wrong_size":
        archive.write_bytes(b"x")
    else:
        moved = archive.with_suffix(".retained")
        archive.rename(moved)
        if fault == "symlink":
            archive.symlink_to(moved)
    with pytest.raises(installer.InstallError):
        installer.install(prefix, lock, "frontend", row, offline=True)
    assert not (prefix / "payload").exists()
    assert not (prefix / ".r2g-install/frontend.json").exists()


@pytest.mark.parametrize("fault", ["traversal", "absolute", "wrong_root", "duplicate", "symlink_escape",
                                   "absolute_link", "below_link", "hardlink_escape", "hardlink_missing",
                                   "fifo", "expanded_bound", "member_bound"])
def test_unsafe_archives_fail_before_extracting_bytes(tmp_path, fault):
    member = tarfile.TarInfo("payload/file")
    members = [(member, b"content")]
    changes = {}
    if fault in ("traversal", "absolute", "wrong_root"):
        member.name = {"traversal": "payload/../../escape", "absolute": "/payload/file",
                       "wrong_root": "other/file"}[fault]
    elif fault == "duplicate":
        members.append((tarfile.TarInfo(member.name), b"duplicate"))
    elif fault in ("symlink_escape", "absolute_link", "below_link", "hardlink_escape", "hardlink_missing"):
        member.type = tarfile.LNKTYPE if fault.startswith("hardlink") else tarfile.SYMTYPE
        member.linkname = {"symlink_escape": "../../escape", "absolute_link": "/tmp/escape",
                           "below_link": ".", "hardlink_escape": "other/file",
                           "hardlink_missing": "payload/absent"}[fault]
        if fault == "below_link":
            members.append((tarfile.TarInfo("payload/file/child"), b"child"))
    elif fault == "fifo":
        member.type = tarfile.FIFOTYPE
    elif fault == "expanded_bound":
        changes["max_expanded_bytes"] = 1
    else:
        changes["max_members"] = 1
        members.append((tarfile.TarInfo("payload/second"), b"second"))
    prefix, lock, row = fixture(tmp_path, members, **changes)
    with pytest.raises(installer.InstallError):
        installer.install(prefix, lock, "frontend", row, offline=True)
    assert not (prefix / "payload").exists()
    assert not list((prefix / ".r2g-staging").rglob("file"))


def test_lock_change_during_probe_prevents_publish(tmp_path):
    prefix, lock, row = fixture(tmp_path)
    def mutate(*args):
        lock.write_text(lock.read_text() + "\n")
    with patch.object(installer, "probes", side_effect=mutate), pytest.raises(installer.InstallError, match="lock changed"):
        installer.install(prefix, lock, "frontend", row, offline=True)
    assert not (prefix / "payload").exists()


def test_probe_output_is_bounded(tmp_path):
    with pytest.raises(installer.InstallError, match="output bound"):
        installer.bounded_probe(["/bin/sh", "-c", "yes fixture"], tmp_path, timeout=2, output_limit=1024)


def test_probe_timeout_reaps_child_group(tmp_path):
    pid_path = tmp_path / "child.pid"
    with pytest.raises(installer.InstallError, match="timeout"):
        installer.bounded_probe(["/bin/sh", "-c", 'sleep 60 & echo $! > "$1"; wait', "probe", str(pid_path)],
                                tmp_path, timeout=0.2)
    pid = int(pid_path.read_text())
    status = Path(f"/proc/{pid}/stat")
    assert not status.exists() or status.read_text().split()[2] == "Z"


def test_cli_preview_has_no_network_or_writes(tmp_path):
    prefix, lock, row = fixture(tmp_path)
    fresh = tmp_path / "fresh"
    result = subprocess.run(["python3", str(EDA / "scripts/setup/direct_archive.py"), "--component", "frontend",
                             "--prefix", str(fresh), "--lock", str(lock), "--dry-run"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "DRY_RUN_NO_NETWORK_OR_WRITES"
    assert not fresh.exists()


def test_unsupported_host_has_no_fallback(tmp_path):
    _, lock, _ = fixture(tmp_path)
    with pytest.raises(installer.InstallError, match="no fallback"):
        installer.select(lock, "frontend", "unsupported-host")


def test_direct_frontend_entry_uses_locked_archive(tmp_path):
    prefix, lock, _ = fixture(tmp_path)
    env = dict(os.environ, R2G_DIRECT="1", R2G_HERMETIC="1", R2G_TOOLCHAIN_ROOT=str(prefix),
               R2G_PREFIX=str(prefix), R2G_DIRECT_ARTIFACT_LOCK=str(lock), R2G_DIRECT_OFFLINE="1")
    result = subprocess.run(["bash", str(EDA / "scripts/setup/install_frontend.sh"), "--force"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (prefix / ".r2g-install/frontend.json").is_file()


def test_missing_direct_core_refuses_unpinned_clone(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "git.called"
    fake_git = fake_bin / "git"
    fake_git.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 99\n')
    fake_git.chmod(0o755)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    env = dict(os.environ, PATH=str(fake_bin) + ":/usr/bin:/bin", R2G_DIRECT="1", R2G_HERMETIC="1",
               R2G_PREFIX=str(bundle), R2G_TOOLCHAIN_ROOT=str(bundle), ORFS_ROOT=str(tmp_path / "missing-orfs"))
    result = subprocess.run(["bash", str(EDA / "scripts/setup/install_core.sh"), "--force"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "unpinned ORFS cloning" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "missing-orfs").exists()


def uncached(tmp_path):
    prefix, lock, row = fixture(tmp_path)
    archive = prefix / ".r2g-downloads" / (row["sha256"] + ".tgz")
    body = archive.read_bytes()
    archive.rename(tmp_path / "retained-fixture.tgz")
    return archive, body, row


def test_transport_timeout_resumes_instead_of_resetting(tmp_path):
    archive, body, row = uncached(tmp_path)
    offsets = []
    def transport(command, timeout):
        assert command[command.index("--retry") + 1] == "0"
        offset = int(command[command.index("--continue-at") + 1])
        offsets.append(offset)
        part = Path(command[command.index("--output") + 1])
        with part.open("ab") as stream:
            stream.write(body[offset:len(body)//2] if offset == 0 else body[offset:])
        return 28 if offset == 0 else 0
    with patch.object(installer, "run_curl", side_effect=transport):
        installer.download(archive, row)
    assert offsets == [0, len(body)//2]
    assert archive.read_bytes() == body


def test_partial_survives_failure_and_resumes_next_invocation(tmp_path):
    archive, body, row = uncached(tmp_path)
    def stalled(command, timeout):
        part = Path(command[command.index("--output") + 1])
        if part.stat().st_size == 0:
            with part.open("ab") as stream: stream.write(body[:17])
        return 28
    with patch.object(installer,"run_curl",side_effect=stalled), pytest.raises(installer.InstallError, match="stalled"):
        installer.download(archive,row)
    assert archive.with_suffix(".part").read_bytes() == body[:17]
    def complete(command, timeout):
        assert command[command.index("--continue-at") + 1] == "17"
        with archive.with_suffix(".part").open("ab") as stream: stream.write(body[17:])
        return 0
    with patch.object(installer,"run_curl",side_effect=complete): installer.download(archive,row)
    assert archive.read_bytes() == body


def test_explicit_partial_import_is_unverified_until_final_sha(tmp_path):
    archive, body, row = uncached(tmp_path)
    source = tmp_path / "old-unverified.part"
    source.write_bytes(body[:11])
    def complete(command, timeout):
        assert command[command.index("--continue-at") + 1] == "11"
        with archive.with_suffix(".part").open("ab") as stream: stream.write(body[11:])
        return 0
    with patch.object(installer,"run_curl",side_effect=complete):
        installer.download(archive,row,resume_from=source)
    assert source.read_bytes() == body[:11]
    assert archive.read_bytes() == body
    assert json.loads(archive.with_suffix(".download.json").read_text())["verified"] is False


def test_wrong_partial_cannot_become_verified_cache(tmp_path):
    archive, body, row = uncached(tmp_path)
    source = tmp_path / "wrong.part"
    source.write_bytes(b"bad")
    def complete(command, timeout):
        with archive.with_suffix(".part").open("ab") as stream: stream.write(body[3:])
        return 0
    with patch.object(installer,"run_curl",side_effect=complete), pytest.raises(installer.InstallError, match="SHA mismatch"):
        installer.download(archive,row,resume_from=source)
    assert not archive.exists()
    assert source.read_bytes() == b"bad"


@pytest.mark.parametrize("value", ["0", "14401", "invalid"])
def test_invalid_download_budget_does_not_start_transport(tmp_path, monkeypatch, value):
    archive, _, row = uncached(tmp_path)
    monkeypatch.setenv("R2G_DIRECT_DOWNLOAD_TIMEOUT",value)
    with patch.object(installer,"run_curl",side_effect=AssertionError("must not run")), pytest.raises(installer.InstallError):
        installer.download(archive,row)
    assert not archive.with_suffix(".part").exists()


def test_existing_untracked_partial_is_not_overwritten(tmp_path):
    archive, _, row = uncached(tmp_path)
    part = archive.with_suffix(".part")
    part.write_bytes(b"user data")
    with pytest.raises(installer.InstallError, match="untracked partial"):
        installer.download(archive,row)
    assert part.read_bytes() == b"user data"


def test_parallel_ranges_require_exact_content_range_before_append(tmp_path):
    part = tmp_path / "artifact.part"
    part.write_bytes(b"prefix")
    total = len(b"prefix") + 5 * 1024 * 1024
    row = {"url":"https://example.invalid/a", "size_bytes":total}
    calls = []
    def transport(command, timeout):
        begin,end = map(int,command[command.index("--range") + 1].split("-"))
        body = Path(command[command.index("--output") + 1])
        headers = Path(command[command.index("--dump-header") + 1])
        body.write_bytes(bytes([begin % 251]) * (end - begin + 1))
        headers.write_text(f"HTTP/2 206\r\ncontent-range: bytes {begin}-{end}/{total}\r\n\r\n")
        calls.append((begin,end))
        return 0
    with patch.object(installer,"run_curl",side_effect=transport):
        added = installer.parallel_range_round(part,row,"curl",jobs=2,timeout=30)
    assert sorted(calls) == [(6,4194309),(4194310,total - 1)]
    assert added == total - 6 and part.stat().st_size == total


@pytest.mark.parametrize("fault", ["http_200", "wrong_start", "truncated"])
def test_parallel_range_failure_retains_evidence_without_appending(tmp_path, fault):
    part = tmp_path / "artifact.part"
    part.write_bytes(b"prefix")
    total = 100
    row = {"url":"https://example.invalid/a", "size_bytes":total}
    def transport(command, timeout):
        begin,end = map(int,command[command.index("--range") + 1].split("-"))
        body = Path(command[command.index("--output") + 1])
        headers = Path(command[command.index("--dump-header") + 1])
        body.write_bytes(b"x" * ((end - begin) if fault == "truncated" else (end - begin + 1)))
        if fault != "http_200":
            reported = begin + 1 if fault == "wrong_start" else begin
            headers.write_text(f"HTTP/2 206\r\ncontent-range: bytes {reported}-{end}/{total}\r\n\r\n")
        else:
            headers.write_text("HTTP/2 200\r\n\r\n")
        return 0
    with patch.object(installer,"run_curl",side_effect=transport):
        assert installer.parallel_range_round(part,row,"curl",jobs=2,timeout=30) == 0
    assert part.read_bytes() == b"prefix"
    assert list(tmp_path.glob("range-*.part")) and list(tmp_path.glob("range-*.headers"))


def test_parallel_download_retries_failed_round_without_offset_drift(tmp_path, monkeypatch):
    archive, body, row = uncached(tmp_path)
    monkeypatch.setenv("R2G_DIRECT_DOWNLOAD_JOBS","4")
    rounds = []
    def ranges(part, descriptor, curl, *, jobs, timeout):
        rounds.append(part.stat().st_size)
        if len(rounds) == 1:
            return 0
        with part.open("ab") as stream: stream.write(body[part.stat().st_size:])
        return len(body)
    with patch.object(installer,"parallel_range_round",side_effect=ranges): installer.download(archive,row)
    assert rounds == [0,0]
    assert archive.read_bytes() == body


def test_parallel_download_fails_after_three_unchanged_rounds(tmp_path, monkeypatch):
    archive, _, row = uncached(tmp_path)
    monkeypatch.setenv("R2G_DIRECT_DOWNLOAD_JOBS","4")
    with patch.object(installer,"parallel_range_round",return_value=0) as ranges, pytest.raises(installer.InstallError, match="stalled three rounds"):
        installer.download(archive,row)
    assert ranges.call_count == 3
    assert archive.with_suffix(".part").stat().st_size == 0


@pytest.mark.parametrize("value", ["0", "9", "invalid"])
def test_invalid_parallel_job_count_does_not_start_transport(tmp_path, monkeypatch, value):
    archive, _, row = uncached(tmp_path)
    monkeypatch.setenv("R2G_DIRECT_DOWNLOAD_JOBS",value)
    with patch.object(installer,"run_curl",side_effect=AssertionError("must not run")), pytest.raises(installer.InstallError):
        installer.download(archive,row)
    assert not archive.with_suffix(".part").exists()
