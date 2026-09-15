"""Explicit runtime resource bytes, not native closure or toolchain authority.

Pins must be provided by frozen consumer inputs. No ambient environment or
host fallback is used to derive them. These resources do not attest parent
interpreter startup, OpenSSL provider libraries, or binary build provenance.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import re
import stat


VERSION = "tehm-orfs-runtime-resources-v1"
ENVIRONMENT_KEYS = frozenset({"TERM", "TERMINFO", "TERMINFO_DIRS", "OPENSSL_CONF"})
_MAX_BYTES = 4 * 1024 * 1024


def _path(value) -> Path:
    if (type(value) is not str or not re.fullmatch(r"/[A-Za-z0-9_./+-]+", value)
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise ValueError("runtime resource requires an unambiguous shell-safe absolute path")
    return Path(value)


def verify_runtime_resources(value: Mapping) -> dict:
    """Copy and check exact pins; re-call around each probe/execution boundary.

    The input is a binding, not a saved valid receipt. Final-component physical
    symlinks, devices, missing files, unpinned extra resources, and active
    OpenSSL .include directives are rejected. Trusted configuration may still
    load providers; only a separate native audit can establish their closure.
    """
    if (not isinstance(value, Mapping)
            or set(value) != {"version", "environment", "file_sha256"}
            or value["version"] != VERSION):
        raise ValueError("runtime resource binding shape/version is invalid")
    environment, pins = value["environment"], value["file_sha256"]
    if (not isinstance(environment, Mapping) or set(environment) != ENVIRONMENT_KEYS
            or not isinstance(pins, Mapping)):
        raise ValueError("runtime resource environment/pins are incomplete")
    environment, pins = dict(environment), dict(pins)
    if environment["TERM"] != "dumb":
        raise ValueError("runtime resources require the fixed dumb terminal")
    terminfo = _path(environment["TERMINFO"])
    openssl = _path(environment["OPENSSL_CONF"])
    if environment["TERMINFO_DIRS"] != str(terminfo):
        raise ValueError("runtime resources require one explicit TERMINFO directory")
    expected = {str(terminfo / "d/dumb"), str(openssl)}
    if len(expected) != 2 or set(pins) != expected:
        raise ValueError("runtime resource file inventory/pins mismatch")
    contents = {}
    for name in sorted(expected):
        pin = pins[name]
        if type(pin) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin):
            raise ValueError("runtime resource requires independent canonical SHA256 pins")
        try:
            fd = os.open(_path(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= _MAX_BYTES:
                    raise ValueError("runtime resource must be a bounded nonempty regular file")
                data = stream.read(_MAX_BYTES + 1)
                after = os.fstat(stream.fileno())
        except OSError as exc:
            raise ValueError("runtime resource file is missing or a physical symlink") from exc
        attributes = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(before, key) != getattr(after, key) for key in attributes)
                or len(data) != before.st_size
                or "sha256:" + hashlib.sha256(data).hexdigest() != pin):
            raise ValueError("runtime resource bytes changed or SHA256 mismatched")
        contents[name] = data
    try:
        config = contents[str(openssl)].decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("runtime OpenSSL configuration must be UTF8 text") from exc
    if "\x00" in config or re.search(r"^\s*\.include\b", config, flags=re.MULTILINE):
        raise ValueError("runtime OpenSSL configuration has an unbound include or NUL")
    return {"version": VERSION, "environment": environment, "file_sha256": pins}
