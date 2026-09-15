"""Explicit, pinned, read-only historical artifact aliases.

Original paths are lexical identities, never filesystem lookup candidates.
This is a byte reader, not a canonical replay, toolchain probe, or authority
receipt. Callers must separately replay contracts and all applicable gates.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

SCHEMA = "tehm-origin-bundle-v1"
READ_PLAN_SCHEMA = "tehm-origin-read-plan-v1"
_HEX = re.compile(r"[0-9a-f]{64}")
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class OriginBundleError(ValueError):
    """Missing, ambiguous, escaped, changed, or unauthenticated origin bytes."""


def _pin(value: str) -> str:
    if type(value) is not str:
        raise OriginBundleError("a SHA256 pin is required")
    hexed = value.removeprefix("sha256:")
    if not _HEX.fullmatch(hexed):
        raise OriginBundleError("a lowercase full SHA256 pin is required")
    return "sha256:" + hexed


def _original(value: str) -> str:
    if (type(value) is not str or not value.startswith("/") or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise OriginBundleError("original path must be an unambiguous lexical absolute file path")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OriginBundleError("duplicate JSON object key")
        result[key] = value
    return result


def _constant(value):
    raise OriginBundleError("non-finite JSON constant")


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OriginBundleError("invalid or ambiguous origin JSON") from exc


class OriginBundle:
    """Load an exact bundle using an independently supplied manifest-file pin.

    Schema: ``{schema, aliases, blobs}``, where aliases are a list of
    ``{original_path, sha256}`` and blobs map prefixed SHA256 to
    ``{relative_path: sha256/<hh>/<hex>, size}``. No physical symlinks,
    extra files, unreferenced blobs, or status/authority annotations are accepted.

    Construction/verify checks the full inventory. Each read rechecks the
    manifest and the requested blob; it does not imply other files remained
    unchanged since the previous full verify. No executable is ever launched.
    """

    def __init__(self, root: str | Path, *, expected_manifest_sha256: str):
        self.root = Path(root).absolute()
        self.manifest_sha256 = _pin(expected_manifest_sha256)
        data = self._read("manifest.json", maximum=_MAX_MANIFEST_BYTES)
        self._check_manifest(data)
        manifest = _json(data)
        if type(manifest) is not dict or set(manifest) != {"schema", "aliases", "blobs"}:
            raise OriginBundleError("origin bundle schema fields mismatch")
        if manifest["schema"] != SCHEMA:
            raise OriginBundleError("origin bundle schema version mismatch")
        aliases, blobs = manifest["aliases"], manifest["blobs"]
        if type(aliases) is not list or not aliases or type(blobs) is not dict or not blobs:
            raise OriginBundleError("origin bundle requires explicit aliases and blobs")
        validated_blobs = {}
        for value, row in blobs.items():
            if _pin(value) != value or type(row) is not dict or set(row) != {"relative_path", "size"}:
                raise OriginBundleError("invalid blob binding")
            hexed = value.removeprefix("sha256:")
            relative = f"sha256/{hexed[:2]}/{hexed}"
            if row["relative_path"] != relative or type(row["size"]) is not int or row["size"] < 0:
                raise OriginBundleError("invalid blob address or size")
            validated_blobs[value] = (relative, row["size"])
        validated_aliases = {}
        for row in aliases:
            if type(row) is not dict or set(row) != {"original_path", "sha256"}:
                raise OriginBundleError("invalid original alias")
            original = _original(row["original_path"])
            value = _pin(row["sha256"])
            if row["sha256"] != value or value not in validated_blobs or original in validated_aliases:
                raise OriginBundleError("duplicate or unbound original alias")
            validated_aliases[original] = value
        if set(validated_aliases.values()) != set(validated_blobs):
            raise OriginBundleError("unreferenced origin blobs")
        self._aliases = MappingProxyType(validated_aliases)
        self._blobs = MappingProxyType(validated_blobs)
        self.verify()

    def _read(self, relative: str, *, maximum: int) -> bytes:
        """Descriptor-relative, no-follow opens; no unchecked physical path escapes."""
        descriptors = []
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(directory)
            parts = relative.split("/")
            for part in parts[:-1]:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                descriptors.append(directory)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            descriptors.append(fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise OriginBundleError("origin file type or size mismatch")
            blocks, total = [], 0
            while True:
                block = os.read(fd, min(1048576, maximum - total + 1))
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise OriginBundleError("origin file grew during read")
                blocks.append(block)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise OriginBundleError("origin file changed during read")
            return b"".join(blocks)
        except OSError as exc:
            raise OriginBundleError("origin file missing, escaped, or unreadable") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _check_manifest(self, data: bytes) -> None:
        if "sha256:" + hashlib.sha256(data).hexdigest() != self.manifest_sha256:
            raise OriginBundleError("independent manifest pin mismatch")

    def _blob(self, value: str) -> bytes:
        relative, size = self._blobs[value]
        data = self._read(relative, maximum=size)
        if len(data) != size or "sha256:" + hashlib.sha256(data).hexdigest() != value:
            raise OriginBundleError("origin blob pin or size mismatch")
        return data

    def read_bytes(self, original_path: str, *, expected_sha256: str) -> bytes:
        """Resolve only the exact original alias and the caller's independent file pin."""
        original, expected = _original(original_path), _pin(expected_sha256)
        self._check_manifest(self._read("manifest.json", maximum=_MAX_MANIFEST_BYTES))
        if self._aliases.get(original) != expected:
            raise OriginBundleError("original alias missing or caller file pin mismatch")
        return self._blob(expected)

    def read_json(self, original_path: str, *, expected_sha256: str):
        return _json(self.read_bytes(original_path, expected_sha256=expected_sha256))

    def verify(self) -> dict:
        """Recheck exact regular-file inventory and all bytes, without granting authority."""
        self._check_manifest(self._read("manifest.json", maximum=_MAX_MANIFEST_BYTES))
        expected = {relative for relative, _ in self._blobs.values()} | {"manifest.json"}
        actual = set()
        def walk_error(error):
            raise OriginBundleError("origin inventory unreadable") from error
        for directory, subdirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
            for name in subdirs + files:
                path = Path(directory) / name
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise OriginBundleError("origin inventory contains aliases or non-regular files")
            actual.update(str((Path(directory) / name).relative_to(self.root)) for name in files)
        if actual != expected:
            raise OriginBundleError("origin exact inventory mismatch")
        for value in self._blobs:
            self._blob(value)
        self._check_manifest(self._read("manifest.json", maximum=_MAX_MANIFEST_BYTES))
        return {"version": "tehm-origin-bundle-byte-verification-v1",
                "scope": "archived_bytes_and_alias_pins", "manifest_sha256": self.manifest_sha256,
                "alias_count": len(self._aliases), "blob_count": len(self._blobs),
                "bytes": sum(size for _, size in self._blobs.values()),
                "toolchain_probed": False, "canonical_replayed": False, "production_authority": False}


class OriginReadPlan:
    """Bind a consumer's exact logical paths and pins to archived bytes.

    The plan is supplied and pinned by the consumer.  Logical paths remain the
    original absolute strings used by canonical identities; they are never
    resolved against the live filesystem.  Physical reads are delegated only
    to :class:`OriginBundle`.  This establishes a dual-address byte boundary,
    not canonical replay, toolchain validity, chronology, or authority.
    """

    def __init__(self, bundle: OriginBundle, bindings: list[dict], *,
                 expected_plan_digest: str):
        if not isinstance(bundle, OriginBundle):
            raise OriginBundleError("origin read plan requires an OriginBundle")
        if type(bindings) is not list or not bindings:
            raise OriginBundleError("origin read plan requires explicit bindings")
        validated = {}
        rows = []
        for row in bindings:
            if type(row) is not dict or set(row) != {"original_path", "sha256"}:
                raise OriginBundleError("invalid origin read binding")
            original, value = _original(row["original_path"]), _pin(row["sha256"])
            if row["sha256"] != value or original in validated:
                raise OriginBundleError("duplicate or noncanonical origin read binding")
            validated[original] = value
            rows.append({"original_path": original, "sha256": value})
        rows.sort(key=lambda row: (row["original_path"], row["sha256"]))
        payload = {"schema": READ_PLAN_SCHEMA, "bindings": rows}
        actual = "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode()).hexdigest()
        if _pin(expected_plan_digest) != actual:
            raise OriginBundleError("independent origin read plan pin mismatch")
        self.bundle = bundle
        self.plan_digest = actual
        self._bindings = MappingProxyType(validated)
        # Fail during construction if the independently pinned plan names an
        # alias absent from the independently pinned bundle.
        for original, value in self._bindings.items():
            bundle.read_bytes(original, expected_sha256=value)

    def _binding(self, original_path: str) -> tuple[str, str]:
        original = _original(original_path)
        value = self._bindings.get(original)
        if value is None:
            raise OriginBundleError("logical path is outside the origin read plan")
        return original, value

    def read_bytes(self, original_path: str) -> bytes:
        original, value = self._binding(original_path)
        return self.bundle.read_bytes(original, expected_sha256=value)

    def read_text(self, original_path: str) -> str:
        try:
            return self.read_bytes(original_path).decode("utf-8")
        except UnicodeError as exc:
            raise OriginBundleError("origin text is not strict UTF-8") from exc

    def read_json(self, original_path: str):
        return _json(self.read_bytes(original_path))

    def verify(self) -> dict:
        total = sum(len(self.read_bytes(original)) for original in self._bindings)
        return {
            "version": "tehm-origin-read-plan-verification-v1",
            "scope": "logical_identity_to_archived_bytes",
            "plan_digest": self.plan_digest,
            "manifest_sha256": self.bundle.manifest_sha256,
            "binding_count": len(self._bindings),
            "bytes": total,
            "logical_paths_preserved": True,
            "live_filesystem_fallback": False,
            "toolchain_probed": False,
            "canonical_replayed": False,
            "production_authority": False,
        }
