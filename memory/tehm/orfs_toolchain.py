"""Content-addressed ORFS toolchain manifests.

The R2G skills intentionally have a discovery fallback for interactive use.
That is useful for a shell, but it is not a sufficient identity for TEHM
evidence: two phases must be able to prove that they used the same ORFS tree,
executables, PDK markers and capability probes.  This module is deliberately
side-effect free and contains only manifest construction and replay checks.

The manifest is a local/release *lock*, not a binary distribution.  Paths are
kept as operator pins so a replay can find the exact installation; the content
digests are the authority when a checkout is moved or a binary is replaced.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from tehm.ids import stable_dumps


MANIFEST_SCHEMA = "tehm-orfs-toolchain-manifest-v1"
MANIFEST_VERSION = 1
_PDK_MARKERS = (
    "sky130A/libs.tech/openlane/config.tcl",
    "sky130A/libs.tech/magic/sky130A.tech",
    "sky130A/libs.ref/sky130_fd_sc_hd/lef/sky130_fd_sc_hd.lef",
    "gf180mcuC/libs.tech/openlane/config.tcl",
)


class ToolchainManifestError(ValueError):
    """Raised when a toolchain manifest cannot be built or replayed."""


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True,
            text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _orfs_identity(root: Path) -> dict:
    makefile = root / "flow" / "Makefile"
    status = _git(root, "status", "--porcelain=v1")
    return {
        "root": str(root),
        "flow_makefile_sha256": _sha256(makefile),
        "flow_makefile_bytes": (makefile.stat().st_size
                                 if makefile.is_file() else None),
        "git_head": _git(root, "rev-parse", "HEAD"),
        # A status digest is retained even for a dirty tree.  A diagnostic
        # manifest may be created with allow_dirty=True, but replay still sees
        # the exact same dirty surface instead of silently accepting drift.
        "git_status_sha256": (
            hashlib.sha256(status.encode()).hexdigest()
            if status is not None else None),
        "git_dirty": bool(status) if status is not None else None,
        "version_source": "git" if status is not None else "filesystem",
    }


def _pdk_identity(pdk_root: str | Path | None) -> dict:
    if not pdk_root:
        return {"root": None, "markers": []}
    root = Path(str(pdk_root)).expanduser().resolve()
    markers = []
    for relative in _PDK_MARKERS:
        path = root / relative
        if path.is_file():
            markers.append({
                "path": relative,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            })
    return {"root": str(root), "markers": markers}


def _unsigned(manifest: Mapping) -> dict:
    result = dict(manifest)
    result.pop("manifest_digest", None)
    return result


def manifest_digest(manifest: Mapping) -> str:
    """Return the content digest excluding the self-referential digest field."""
    return hashlib.sha256(stable_dumps(_unsigned(manifest)).encode()).hexdigest()


def build_toolchain_manifest(
        preflight: Mapping, *, pdk_root: str | Path | None = None,
        require_internal: bool = False, allow_dirty: bool = False) -> dict:
    """Build a deterministic manifest from ``preflight_orfs_toolchain``.

    ``preflight`` is intentionally passed in by the caller so this module does
    not discover a second executable set.  A blocked preflight, missing binary
    digest or dirty ORFS tree is rejected unless the caller explicitly marks
    the result as a diagnostic manifest.
    """
    if not isinstance(preflight, Mapping):
        raise ToolchainManifestError("toolchain preflight must be an object")
    status = str(preflight.get("status") or "blocked")
    if status not in {"bound_internal", "bound_external"}:
        raise ToolchainManifestError(
            f"cannot lock blocked toolchain preflight: {status}")
    if require_internal and status != "bound_internal":
        raise ToolchainManifestError(
            f"production manifest requires bound_internal, got {status}")
    root_value = preflight.get("orfs_root")
    if not root_value:
        raise ToolchainManifestError("preflight has no ORFS root")
    root = Path(str(root_value)).expanduser().resolve()
    identity = _orfs_identity(root)
    if not (root / "flow" / "Makefile").is_file():
        raise ToolchainManifestError(f"ORFS flow/Makefile is missing: {root}")
    if identity["git_dirty"] and not allow_dirty:
        raise ToolchainManifestError(
            "ORFS tree is dirty; use a clean checkout or explicitly mark a diagnostic lock")
    tools = {}
    for name in ("openroad", "yosys"):
        raw = preflight.get("tools", {}).get(name)
        if not isinstance(raw, Mapping) or not raw.get("path"):
            raise ToolchainManifestError(f"preflight has no {name} binding")
        if not raw.get("sha256"):
            raise ToolchainManifestError(f"preflight has no {name} SHA256")
        tools[name] = json.loads(json.dumps(dict(raw), sort_keys=True))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "manifest_version": MANIFEST_VERSION,
        "binding_status": status,
        "compatibility": preflight.get("compatibility"),
        "toolchain_root": preflight.get("toolchain_root"),
        "orfs": identity,
        "tools": tools,
        "pdk": _pdk_identity(pdk_root),
        "policy": {
            "requires_internal": bool(require_internal),
            "allow_dirty": bool(allow_dirty),
        },
    }
    manifest["manifest_digest"] = manifest_digest(manifest)
    return manifest


def load_toolchain_manifest(value: str | Path | Mapping) -> dict:
    """Load and authenticate a manifest from a path or an in-memory object."""
    if isinstance(value, Mapping):
        manifest = dict(value)
    else:
        path = Path(str(value)).expanduser().resolve()
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolchainManifestError(f"cannot read toolchain manifest: {path}") from exc
    if not isinstance(manifest, dict):
        raise ToolchainManifestError("toolchain manifest must be a JSON object")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ToolchainManifestError("toolchain manifest schema/version mismatch")
    digest = manifest.get("manifest_digest")
    if not digest or manifest_digest(manifest) != digest:
        raise ToolchainManifestError("toolchain manifest digest mismatch")
    return manifest


def validate_toolchain_manifest(
        value: str | Path | Mapping, preflight: Mapping, *,
        pdk_root: str | Path | None = None) -> dict:
    """Replay a manifest against a fresh preflight report.

    The result is structured for receipts.  It never mutates the supplied
    report and returns every mismatch so callers can fail closed with an
    actionable reason.
    """
    try:
        manifest = load_toolchain_manifest(value)
    except ToolchainManifestError as exc:
        return {"valid": False, "reasons": [str(exc)]}
    reasons = []
    expected_status = manifest.get("binding_status")
    if preflight.get("status") != expected_status:
        reasons.append("binding status changed")
    expected_orfs = manifest.get("orfs") or {}
    if manifest.get("toolchain_root") != preflight.get("toolchain_root"):
        reasons.append("toolchain root changed")
    current_root = Path(str(preflight.get("orfs_root") or "")).expanduser().resolve()
    if expected_orfs.get("root") != str(current_root):
        reasons.append("ORFS root changed")
    current_identity = _orfs_identity(current_root)
    for field in ("flow_makefile_sha256", "git_head", "git_status_sha256", "git_dirty"):
        if expected_orfs.get(field) != current_identity.get(field):
            reasons.append(f"ORFS {field} changed")
    for name in ("openroad", "yosys"):
        expected = manifest.get("tools", {}).get(name) or {}
        current = preflight.get("tools", {}).get(name) or {}
        for field in ("path", "source", "sha256", "version", "capabilities"):
            if expected.get(field) != current.get(field):
                reasons.append(f"{name} {field} changed")
    expected_pdk = manifest.get("pdk") or {"root": None, "markers": []}
    if expected_pdk != _pdk_identity(pdk_root):
        reasons.append("PDK root or marker digest changed")
    if (manifest.get("policy") or {}).get("requires_internal") and \
            preflight.get("status") != "bound_internal":
        reasons.append("manifest requires bound_internal toolchain")
    return {
        "valid": not reasons,
        "manifest_digest": manifest.get("manifest_digest"),
        "reasons": reasons,
    }
