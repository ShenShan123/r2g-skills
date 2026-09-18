"""Install byte-locked upstream archives without conda, sudo, or host mutation.

Payload installation/readiness is not native-read or TEHM promotion authority.
Failed downloads/stages remain recoverable. Existing untracked trees are never
overwritten. A preview makes no directories and performs no network operations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.parse


class InstallError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InstallError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def host_key():
    system = platform.system().lower()
    machine = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    return system + "-" + machine


def relative(name):
    path = PurePosixPath(name)
    require(bool(name) and not path.is_absolute() and ".." not in path.parts,
            "unsafe archive/descriptor path: " + name)
    return path


def select(lock_path, component, host):
    lock = json.loads(Path(lock_path).read_text())
    require(lock.get("schema") == "r2g-direct-artifacts-v1", "unsupported artifact-lock schema")
    row = lock.get("components", {}).get(component, {}).get(host)
    require(isinstance(row, dict), f"no pinned direct artifact for {component}/{host}; no fallback permitted")
    require(isinstance(row.get("sha256"), str) and len(row["sha256"]) == 64 and
            all(c in "0123456789abcdef" for c in row["sha256"]), "invalid artifact SHA256")
    url = urllib.parse.urlparse(row.get("url", ""))
    require(url.scheme == "https" and url.hostname and not url.username and not url.password,
            "artifact download requires a credential-free HTTPS URL")
    for key in ("size_bytes", "max_expanded_bytes", "max_members"):
        require(type(row.get(key)) is int and row[key] > 0, "invalid bound: " + key)
    root = relative(row.get("archive_root", ""))
    require(len(root.parts) == 1 and str(root) != ".", "archive_root must be one directory")
    require(isinstance(row.get("runtime_probes"), dict) and row["runtime_probes"], "runtime probes required")
    for name, args in row["runtime_probes"].items():
        relative(name)
        require(isinstance(args, list) and all(isinstance(arg, str) for arg in args), "invalid probe argv")
    return row


def inventory(root):
    files, links, directories = {}, {}, []
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            require(path.resolve(strict=True).is_relative_to(root.resolve()), "symlink escapes payload: " + name)
            links[name] = os.readlink(path)
        elif path.is_file():
            files[name] = {"sha256": sha(path), "mode": path.stat().st_mode & 0o777}
        elif path.is_dir():
            directories.append(name)
        else:
            raise InstallError("special payload file: " + name)
    return {"files": files, "symlinks": links, "directories": directories}


def validate_members(members, row):
    require(len(members) <= row["max_members"], "archive member bound exceeded")
    names, links = {}, set()
    total = 0
    root = row["archive_root"]
    for member in members:
        name = str(relative(member.name))
        require(name == root or name.startswith(root + "/"), "unexpected archive root: " + name)
        require(name not in names, "duplicate archive member: " + name)
        require(member.isdir() or member.isreg() or member.issym() or member.islnk(), "special archive member: " + name)
        require(not member.issparse(), "sparse archives unsupported")
        names[name] = member
        if member.isreg():
            require(member.size >= 0, "negative member size")
            total += member.size
        if member.issym() or member.islnk():
            links.add(name)
            require(not PurePosixPath(member.linkname).is_absolute(), "absolute archive link: " + name)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname)) if member.issym() else str(relative(member.linkname))
            require(target == root or target.startswith(root + "/"), "archive link escapes root: " + name)
    require(total <= row["max_expanded_bytes"], "expanded archive bound exceeded")
    for name, member in names.items():
        require(not any(str(parent) in links for parent in PurePosixPath(name).parents), "archive member below link: " + name)
        if member.islnk():
            target = str(relative(member.linkname))
            require(target in names and names[target].isreg(), "hardlink must target a regular member")
    return names


def extract(archive, stage, row):
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar:
            require(len(members) < row["max_members"], "archive member bound exceeded")
            members.append(member)
        names = validate_members(members, row)
        # Create byte files before links, so member order cannot redirect writes.
        for name, member in names.items():
            path = stage / name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isreg():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target, 1048576)
                require(path.stat().st_size == member.size, "truncated archive member: " + name)
                path.chmod(member.mode & 0o777)
        for name, member in names.items():
            path = stage / name
            if member.issym() or member.islnk():
                path.parent.mkdir(parents=True, exist_ok=True)
                if member.issym():
                    path.symlink_to(member.linkname)
                else:
                    os.link(stage / str(relative(member.linkname)), path)
    return inventory(stage / row["archive_root"])


def bounded_probe(command, root, timeout=30, output_limit=1048576):
    """Bound runtime/output and reap the probe's own process group, including children."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        deadline = time.monotonic() + timeout
        failure = None
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    failure = "runtime probe timeout"
                    break
                if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > output_limit:
                    failure = "runtime probe output bound exceeded"
                    break
                time.sleep(0.02)
        finally:
            # A successful wrapper may also have left children behind. Do not
            # permit either success or timeout to leak its private process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        require(failure is None, failure or "runtime probe failed")
        require(max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) <= output_limit,
                "runtime probe output bound exceeded")
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, process.returncode, stdout.read(), stderr.read())


def probes(root, row):
    results = {}
    for name, argv in row["runtime_probes"].items():
        executable = root / name
        require(executable.is_file() and os.access(executable, os.X_OK), "required executable absent: " + name)
        try:
            result = bounded_probe([str(executable), *argv], root)
        except OSError as exc:
            raise InstallError("required runtime probe failed: " + name) from exc
        require(result.returncode == 0 and bool(result.stdout.strip() or result.stderr.strip()), "unhealthy runtime: " + name)
        results[name] = {"returncode": result.returncode, "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                         "stderr_sha256": hashlib.sha256(result.stderr).hexdigest()}
    return results


def private_directory(path):
    require(not path.is_symlink(), "installer metadata directory is a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir(), "installer metadata path is not a directory")


def run_curl(command, timeout):
    process = subprocess.Popen(command, start_new_session=True)
    try:
        try:
            return process.wait(timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            return 28
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def parallel_range_round(part, row, curl, *, jobs, timeout):
    """Fetch small adjacent ranges concurrently; append only an all-valid round."""
    start = part.stat().st_size
    chunk_size = 4 * 1024 * 1024
    ranges = []
    for index in range(jobs):
        begin = start + index * chunk_size
        if begin >= row["size_bytes"]:
            break
        end = min(row["size_bytes"] - 1, begin + chunk_size - 1)
        ranges.append((begin, end))

    def fetch(bounds):
        begin, end = bounds
        fd, body_name = tempfile.mkstemp(prefix=f"range-{begin}-{end}-", suffix=".part", dir=part.parent)
        os.close(fd)
        fd, header_name = tempfile.mkstemp(prefix=f"range-{begin}-{end}-", suffix=".headers", dir=part.parent)
        os.close(fd)
        body, headers = Path(body_name), Path(header_name)
        code = run_curl([curl, "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                         "--connect-timeout", "15", "--max-time", str(timeout), "--retry", "0",
                         "--range", f"{begin}-{end}", "--max-filesize", str(end - begin + 1),
                         "--dump-header", str(headers), "--output", str(body), row["url"]], timeout)
        matches = re.findall(r"(?im)^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)\s*$",
                             headers.read_text(errors="replace"))
        valid = (code == 0 and matches and tuple(map(int, matches[-1])) == (begin, end, row["size_bytes"])
                 and body.stat().st_size == end - begin + 1)
        return begin, end, body, headers, valid, code

    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        results = list(pool.map(fetch, ranges))
    if not all(result[4] for result in results):
        # Preserve every response for inspection, leave the contiguous partial
        # untouched, and let the outer bounded loop retry the whole round.
        return 0
    for begin, end, body, headers, _, _ in sorted(results):
        require(part.stat().st_size == begin, "parallel append offset drift")
        with body.open("rb") as source, part.open("ab") as target:
            shutil.copyfileobj(source, target, 1048576)
        require(part.stat().st_size == end + 1, "parallel range append incomplete")
        body.unlink()
        headers.unlink()
    return part.stat().st_size - start


def download(archive, row, *, resume_from=None):
    """Resume unverified contiguous bytes; only the final fixed SHA grants cache status."""
    try:
        budget = int(os.environ.get("R2G_DIRECT_DOWNLOAD_TIMEOUT", "3600"))
    except ValueError as exc:
        raise InstallError("invalid R2G_DIRECT_DOWNLOAD_TIMEOUT") from exc
    require(1 <= budget <= 14400, "download timeout must be 1..14400 seconds")
    try:
        jobs = int(os.environ.get("R2G_DIRECT_DOWNLOAD_JOBS", "1"))
    except ValueError as exc:
        raise InstallError("invalid R2G_DIRECT_DOWNLOAD_JOBS") from exc
    require(1 <= jobs <= 8, "download jobs must be 1..8")
    curl = shutil.which("curl")
    require(curl is not None, "curl required for locked HTTPS download")
    part = archive.with_suffix(".part")
    metadata = archive.with_suffix(".download.json")
    lock = archive.with_suffix(".download.lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "w") as held:
        require(stat.S_ISREG(os.fstat(held.fileno()).st_mode), "download lock is not a regular file")
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("another downloader owns this artifact; no concurrent writes") from exc
        # Another invocation may have completed while we were entering the lock.
        if archive.exists() or archive.is_symlink():
            require(archive.is_file() and not archive.is_symlink() and
                    archive.stat().st_size == row["size_bytes"] and sha(archive) == row["sha256"],
                    "archive cache size/SHA mismatch; no overwrite")
            return
        binding = {"schema": "r2g-unverified-partial-v1", "artifact_descriptor_digest": digest(row),
                   "url": row["url"], "sha256": row["sha256"], "size_bytes": row["size_bytes"]}
        if part.exists() or part.is_symlink() or metadata.exists() or metadata.is_symlink():
            require(resume_from is None, "resume-from only imports into a new partial cache")
            require(part.is_file() and not part.is_symlink() and metadata.is_file() and not metadata.is_symlink(),
                    "untracked partial cache retained; refusing overwrite")
            require(json.loads(metadata.read_text()).get("binding") == binding, "partial descriptor drift")
        else:
            imported = None
            if resume_from is not None:
                source = Path(resume_from)
                require(source.is_file() and not source.is_symlink() and source.stat().st_size <= row["size_bytes"],
                        "invalid explicit resume source")
                imported = {"path": str(source.absolute()), "sha256": sha(source), "size_bytes": source.stat().st_size}
            with part.open("xb") as output:
                if imported is not None:
                    with source.open("rb") as source_bytes:
                        shutil.copyfileobj(source_bytes, output, 1048576)
            if imported is not None:
                require(sha(part) == imported["sha256"] and part.stat().st_size == imported["size_bytes"],
                        "resume source changed during import; partial retained unverified")
            with metadata.open("x") as stream:
                json.dump({"binding": binding, "imported_unverified_partial": imported,
                           "verified": False}, stream, sort_keys=True)
        deadline = time.monotonic() + budget
        stalled = 0
        # The wall-clock budget is authoritative. The high round ceiling only
        # permits many small validated ranges on slow proxies.
        for attempt in range(200):
            require(part.is_file() and not part.is_symlink(), "partial path changed")
            offset = part.stat().st_size
            require(offset <= row["size_bytes"], "partial size exceeds pinned artifact")
            if offset == row["size_bytes"]:
                break
            remaining = int(deadline - time.monotonic())
            require(remaining > 0, "download budget exhausted; resumable partial retained: " + str(part))
            timeout = min(600, remaining)
            print(json.dumps({"event": "DOWNLOAD_ATTEMPT", "attempt": attempt + 1,
                              "url": row["url"], "sha256": row["sha256"], "resume_offset": offset,
                              "timeout_seconds": timeout, "parallel_jobs": jobs}), flush=True)
            if jobs > 1:
                added = parallel_range_round(part, row, curl, jobs=jobs, timeout=timeout)
                stalled = stalled + 1 if added == 0 else 0
                require(stalled < 3,
                        "parallel range stalled three rounds; evidence retained unverified")
                continue
            # Never let curl retry and truncate its output. Each new process
            # observes the current offset and requires server resume support.
            code = run_curl([curl, "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                             "--connect-timeout", "15", "--max-time", str(timeout), "--retry", "0",
                             "--continue-at", str(offset), "--max-filesize", str(row["size_bytes"]),
                             "--output", str(part), row["url"]], timeout)
            size = part.stat().st_size
            require(offset <= size <= row["size_bytes"], "resume truncated/oversized partial; retained unverified")
            stalled = stalled + 1 if size == offset else 0
            require(code not in (22, 33, 63) and stalled < 3,
                    "download rejected/stalled; resumable partial retained: " + str(part))
        require(part.stat().st_size == row["size_bytes"] and sha(part) == row["sha256"],
                "download incomplete or SHA mismatch; resumable partial retained: " + str(part))
        require(not archive.exists() and not archive.is_symlink(), "archive cache appeared during download")
        os.rename(part, archive)


def install(prefix, lock_path, component, row, *, offline=False, resume_from=None):
    lock_sha = sha(lock_path)
    require(select(lock_path, component, host_key()) == row, "artifact lock changed before installation")
    def verify_lock():
        require(sha(lock_path) == lock_sha, "artifact lock changed during installation")

    descriptor = digest(row)
    target = prefix / row["archive_root"]
    receipt_path = prefix / ".r2g-install" / (component + ".json")
    if target.exists() or target.is_symlink():
        require(target.is_dir() and not target.is_symlink() and receipt_path.is_file() and not receipt_path.is_symlink(),
                "existing untracked payload retained; refusing overwrite: " + str(target))
        receipt = json.loads(receipt_path.read_text())
        require(receipt.get("schema") == "r2g-direct-archive-receipt-v1" and
                receipt.get("status") == "VERIFIED_PAYLOAD_AND_RUNTIME" and
                receipt.get("component") == component and receipt.get("prefix") == str(prefix) and
                receipt.get("artifact") == row and receipt.get("artifact_lock_sha256") == lock_sha and
                receipt.get("native_read_closure_proved") is False and
                receipt.get("production_authority") is False, "receipt binding mismatch")
        require(receipt.get("receipt_digest") == digest({k: v for k, v in receipt.items() if k != "receipt_digest"}), "receipt digest mismatch")
        require(receipt.get("artifact_descriptor_digest") == descriptor and receipt.get("inventory") == inventory(target), "installed payload/descriptor drift")
        probes(target, row)
        require(receipt.get("inventory") == inventory(target), "runtime probe modified installed payload")
        verify_lock()
        print(json.dumps({"status": "ALREADY_VERIFIED_PAYLOAD", "receipt": str(receipt_path)}), flush=True)
        return receipt
    require(not receipt_path.exists() and not receipt_path.is_symlink(), "orphan receipt retained; refusing overwrite")
    prefix.mkdir(parents=True, exist_ok=True)
    cache_dir = prefix / ".r2g-downloads"
    private_directory(cache_dir)
    archive = cache_dir / (row["sha256"] + ".tgz")
    if not archive.exists():
        require(not archive.is_symlink(), "archive cache symlink refused")
        require(not offline, "verified archive missing from offline cache")
        download(archive, row, resume_from=resume_from)
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size == row["size_bytes"] and sha(archive) == row["sha256"], "archive cache size/SHA mismatch; no overwrite")
    stage_dir = prefix / ".r2g-staging"
    private_directory(stage_dir)
    stage = Path(tempfile.mkdtemp(prefix=component + "-", dir=stage_dir))
    print(json.dumps({"event": "EXTRACT", "stage": str(stage)}), flush=True)
    expected_inventory = extract(archive, stage, row)
    probes(stage / row["archive_root"], row)
    require(expected_inventory == inventory(stage / row["archive_root"]), "runtime probe modified staged payload")
    require(sha(archive) == row["sha256"], "archive changed during extraction")
    verify_lock()
    require(not target.exists() and not target.is_symlink(), "target appeared during installation; stage retained")
    os.rename(stage / row["archive_root"], target)
    runtime = probes(target, row)
    require(expected_inventory == inventory(target), "runtime probe modified installed payload")
    verify_lock()
    private_directory(receipt_path.parent)
    receipt = {"schema": "r2g-direct-archive-receipt-v1", "status": "VERIFIED_PAYLOAD_AND_RUNTIME",
               "component": component, "prefix": str(prefix), "artifact": row,
               "artifact_descriptor_digest": descriptor, "artifact_lock_sha256": lock_sha,
               "inventory": expected_inventory, "runtime_probes": runtime,
               "native_read_closure_proved": False, "production_authority": False}
    receipt["receipt_digest"] = digest(receipt)
    with receipt_path.open("x") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"status": receipt["status"], "receipt": str(receipt_path), "receipt_digest": receipt["receipt_digest"]}), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--lock", default=os.environ.get("R2G_DIRECT_ARTIFACT_LOCK") or str(Path(__file__).resolve().parents[2] / "references/direct-artifacts.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--resume-from", default=os.environ.get("R2G_DIRECT_RESUME_FROM"),
                        help="import explicitly selected unverified partial bytes into a NEW cache")
    args = parser.parse_args()
    require(args.component.isidentifier(), "invalid component name")
    row = select(args.lock, args.component, host_key())
    prefix = Path(args.prefix).absolute()
    require(prefix == prefix.resolve() and prefix not in (Path("/"), Path.home(), Path.cwd()), "unsafe/aliased installation prefix")
    require(not any(prefix.is_relative_to(Path(root)) for root in ("/usr", "/opt", "/bin", "/sbin")), "host-wide prefix refused")
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN_NO_NETWORK_OR_WRITES", "component": args.component,
                          "prefix": str(prefix), "artifact": row}, sort_keys=True))
        return
    require(not (args.offline and args.resume_from), "offline cannot import a partial download")
    install(prefix, args.lock, args.component, row, offline=args.offline, resume_from=args.resume_from)


if __name__ == "__main__":
    try:
        main()
    except (InstallError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        raise SystemExit("[eda-install] ERROR: " + str(exc))
