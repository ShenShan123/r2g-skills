"""Shared live ORFS toolchain preflight for runners and evidence consumers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from tehm.orfs_toolchain import load_toolchain_manifest, validate_toolchain_manifest

TOOLCHAIN_PREFLIGHT_VERSION = "orfs-toolchain-preflight-v1"


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def preflight_orfs_toolchain(manifest: dict, *, env: dict | None = None) -> dict:
    """Resolve the ORFS executor without allowing a silent host fallback.

    ``_env.sh`` intentionally has a broad discovery fallback for interactive
    use.  A campaign receipt cannot use that policy: a source freeze rooted at
    one ORFS tree must not silently execute another tree's host binaries.  The
    campaign therefore accepts a binary packaged below ``ORFS_ROOT`` or an
    explicit executable under the operator's ``R2G_PREFIX``.  Arbitrary
    overrides (including ``/usr`` and ``/opt``) remain auditable diagnostics and
    are marked external; their semantic compatibility is not inferred here.
    """
    supplied = dict(os.environ if env is None else env)
    # Once a campaign names a manifest, its executable pins become the sole
    # source of discovery.  In particular, an ambient OPENROAD_EXE/YOSYS_EXE
    # from another shell must not override a tree-packaged lock.  An explicit
    # ``env`` argument remains an operator diagnostic override; the manifest
    # comparison below will fail closed if it drifts.
    manifest_ref = (manifest.get("toolchain_manifest") or
                    supplied.get("R2G_TOOLCHAIN_MANIFEST"))
    if manifest_ref and env is None:
        try:
            locked = load_toolchain_manifest(manifest_ref)
        except ValueError:
            locked = {}
        if locked.get("binding_status") == "bound_external":
            tools = locked.get("tools") or {}
            supplied = {
                "OPENROAD_EXE": str((tools.get("openroad") or {}).get("path") or ""),
                "YOSYS_EXE": str((tools.get("yosys") or {}).get("path") or ""),
            }
        elif locked:
            supplied = {}
            tools = locked.get("tools") or {}
            if locked.get("toolchain_root"):
                supplied["R2G_PREFIX"] = str(locked["toolchain_root"])
            # Reuse explicit internal pins for a user-prefix lock (or for a
            # deliberately explicit ORFS path); tree-packaged locks stay on
            # package discovery so the source classification is replayed.
            for name, variable in (("openroad", "OPENROAD_EXE"),
                                   ("yosys", "YOSYS_EXE")):
                tool = tools.get(name) or {}
                if tool.get("source") in {"r2g_prefix", "orfs_explicit"}:
                    supplied[variable] = str(tool.get("path") or "")
    raw_root = manifest.get("orfs_root")
    root = Path(str(raw_root)).expanduser().resolve() if raw_root else Path()
    report = {
        "version": TOOLCHAIN_PREFLIGHT_VERSION,
        "orfs_root": str(root),
        "toolchain_root": (str(Path(str(supplied.get("R2G_PREFIX"))).expanduser().resolve())
                           if supplied.get("R2G_PREFIX") else None),
        "status": "blocked",
        "tools": {},
        "environment": {},
        "reasons": [],
    }
    if manifest_ref:
        report["toolchain_manifest"] = (
            str(Path(str(manifest_ref)).expanduser().resolve())
            if isinstance(manifest_ref, (str, os.PathLike)) else manifest_ref)
    if not raw_root or not (root / "flow" / "Makefile").is_file():
        report["reasons"].append(
            f"ORFS_ROOT is missing or has no flow/Makefile: {root}")
        report["error"] = "; ".join(report["reasons"])
        return report

    specs = {
        "openroad": {
            "variable": "OPENROAD_EXE",
            "switch": "-version",
            "packaged": (
                root / "tools" / "install" / "OpenROAD" / "bin" / "openroad",
                root / "tools" / "install" / "openroad" / "bin" / "openroad",
            ),
        },
        "yosys": {
            "variable": "YOSYS_EXE",
            "switch": "-V",
            "packaged": (
                root / "tools" / "install" / "yosys" / "bin" / "yosys",
                root / "tools" / "install" / "Yosys" / "bin" / "yosys",
            ),
        },
    }
    external = False
    for name, spec in specs.items():
        variable = spec["variable"]
        configured = str(supplied.get(variable) or "").strip()
        candidate = None
        source = None
        if configured:
            # Support both an absolute path and an explicitly named executable
            # on PATH, but never search PATH when the variable is absent.
            candidate = (Path(configured).expanduser()
                         if ("/" in configured or configured.startswith("."))
                         else Path(shutil.which(configured) or configured))
            candidate = candidate.resolve()
            source = "explicit_override"
            # An explicit path is production-safe when it is either packaged
            # below this ORFS root or lives below the operator's R2G_PREFIX.
            # Bare /usr, /opt, and arbitrary paths remain external diagnostics.
            try:
                candidate.relative_to(root)
                source = "orfs_explicit"
            except ValueError:
                prefix = supplied.get("R2G_PREFIX")
                if prefix:
                    try:
                        prefix_path = Path(str(prefix)).expanduser().resolve()
                        prefix_text = str(prefix_path)
                        if any(prefix_text == base or prefix_text.startswith(base + "/")
                               for base in ("/usr", "/opt", "/bin", "/sbin")):
                            raise ValueError("host prefix")
                        candidate.relative_to(prefix_path)
                        source = "r2g_prefix"
                    except ValueError:
                        external = True
                else:
                    external = True
            if not (candidate.is_file() and candidate.stat().st_mode & 0o111):
                report["reasons"].append(
                    f"{variable} does not name an executable: {configured}")
                candidate = None
        else:
            # A symlink placed below ``tools/install`` must not turn a host
            # executable into an apparently tree-packaged tool.  Resolve the
            # candidate first and require the actual file to remain below the
            # frozen ORFS root.  Symlinks to a binary built inside this tree
            # remain valid; links escaping to /usr/bin, /opt, or another
            # campaign are rejected before any EDA process starts.
            escaped = []
            candidate = None
            for path in spec["packaged"]:
                if not path.exists():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    escaped.append(f"{path} -> {resolved}")
                    continue
                if resolved.is_file() and resolved.stat().st_mode & 0o111:
                    candidate = resolved
                    break
            if candidate is not None:
                source = "orfs_packaged"
            else:
                if escaped:
                    report["reasons"].append(
                        f"{root} packaged {name} escapes ORFS_ROOT: "
                        + ", ".join(escaped))
                else:
                    report["reasons"].append(
                        f"{root} has no packaged {name}; set {variable} explicitly")

        tool = {"variable": variable, "path": str(candidate) if candidate else None,
                "source": source, "sha256": _sha(candidate) if candidate else None}
        if candidate:
            runtime_env = _tool_runtime_env(candidate, supplied)
            tool["version"] = _tool_version(
                candidate, spec["switch"], env=runtime_env)
            report["environment"][variable] = str(candidate)
            if name == "yosys":
                capability = _probe_yosys_capabilities(
                    candidate, orfs_root=root,
                    version=tool["version"], env=runtime_env)
                tool["capabilities"] = capability
                if capability["status"] == "FAIL":
                    report["reasons"].append(
                        f"{variable} is incompatible with this ORFS flow: "
                        + str(capability["reason"]))
        report["tools"][name] = tool

    if not report["reasons"]:
        report["status"] = "bound_external" if external else "bound_internal"
        if external:
            report["compatibility"] = "operator_bound_unverified"
        else:
            internal_sources = {
                str(tool.get("source"))
                for tool in report["tools"].values()
            }
            if internal_sources <= {"orfs_packaged", "orfs_explicit"}:
                report["compatibility"] = "tree_packaged"
            elif internal_sources <= {"r2g_prefix"}:
                report["compatibility"] = "r2g_prefix"
            else:
                # A mixed internal binding remains user-owned, but must be
                # visible in the receipt instead of being mislabeled as one
                # tree-packaged release.
                report["compatibility"] = "mixed_internal"
        report["fingerprint"] = hashlib.sha256(
            json.dumps({"orfs_root": str(root),
                        "toolchain_root": report["toolchain_root"],
                        "tools": report["tools"]},
                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # A campaign may bind a content-addressed manifest generated by the
        # TEHM/R2G lock command.  Discovery still computes the fresh report,
        # then this optional gate compares every path/hash/version/capability
        # and the ORFS source identity.  No manifest means legacy diagnostics
        # retain their existing behavior; a malformed or drifting manifest is
        # always fail-closed before an EDA process is started.
        if manifest_ref:
            try:
                locked = load_toolchain_manifest(manifest_ref)
            except ValueError:
                locked = {}
            expected_pdk = ((locked.get("pdk") or {}).get("root")
                            if isinstance(locked, dict) else None)
            check = validate_toolchain_manifest(
                manifest_ref, report,
                pdk_root=(expected_pdk or manifest.get("pdk_root")))
            report["manifest_validation"] = check
            if not check.get("valid"):
                report["reasons"].extend(
                    "toolchain manifest: " + str(reason)
                    for reason in check.get("reasons") or ("invalid",))
    else:
        report["error"] = "; ".join(report["reasons"])
    if report["reasons"]:
        report["status"] = "blocked"
        report["error"] = "; ".join(report["reasons"])
    return report


def _tool_runtime_env(path: Path, supplied: dict[str, str]) -> dict[str, str]:
    """Give user-bundled ELF tools their private shared-library search path.

    The direct OpenROAD and KLayout payloads are copied from distribution
    packages, while their convenience wrappers set ``LD_LIBRARY_PATH``.  The
    manifest deliberately records the real ELF path so its hash is meaningful;
    preflight therefore has to reproduce the wrapper's loader environment when
    invoking that path directly.  Keep the operator's ambient environment but
    put the bundle's private libraries first.  This prevents a replay from
    accidentally succeeding only because an unrelated ``/opt`` installation
    happens to be visible on the host.
    """
    runtime = dict(os.environ)
    if supplied.get("LD_LIBRARY_PATH") is not None:
        runtime["LD_LIBRARY_PATH"] = str(supplied.get("LD_LIBRARY_PATH") or "")
    direct_root = supplied.get("R2G_TOOLCHAIN_ROOT") or supplied.get("R2G_PREFIX")
    root = (Path(str(direct_root)).expanduser().resolve()
            if direct_root else None)
    if root is None and path.parent.name in {"bin", "libexec"}:
        # Infer a bundle root only for the known direct-tool layout.  This is
        # intentionally conservative so arbitrary external overrides do not
        # gain a misleading private-library classification.
        for parent in path.parents:
            if parent.name in {"openroad", "klayout"}:
                root = parent.parent
                break
    private_libs = []
    if root is not None:
        for candidate in (root / "openroad" / "lib",
                          root / "klayout" / "lib"):
            if candidate.is_dir():
                private_libs.append(str(candidate))
    if private_libs:
        existing = runtime.get("LD_LIBRARY_PATH", "")
        runtime["LD_LIBRARY_PATH"] = ":".join(
            private_libs + ([existing] if existing else []))
    return runtime


def _tool_version(path: Path, switch: str, *, env: dict[str, str] | None = None) -> str:
    try:
        proc = subprocess.run([str(path), switch], capture_output=True,
                              text=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE:{exc}"
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return f"RC={proc.returncode}:{output}"
    return output[:4096]


def _probe_yosys_capabilities(path: Path, *, orfs_root: Path,
                              version: str,
                              env: dict[str, str] | None = None) -> dict:
    """Fail closed when a real Yosys lacks options used by this ORFS tree.

    A binary existing and returning ``-V`` is not enough for evidence.  The
    current ORFS canonicalization script invokes ``read_liberty -unit_delay``;
    Yosys 0.9 accepts the executable but rejects that option only after a flow
    has started.  Probe the command's help surface before any EDA work.  Fake
    test shims with non-semver version text remain ``UNKNOWN`` so unit tests
    can exercise path binding without pretending to prove compatibility.
    """
    result = {"status": "UNKNOWN", "required": [], "supported": []}
    if not re.match(r"^Yosys\s+\d+\.\d+", str(version)):
        result["reason"] = "version format is not a real Yosys release"
        return result
    canonicalize = orfs_root / "flow" / "scripts" / "synth_canonicalize.tcl"
    if not canonicalize.is_file():
        result["reason"] = "synth_canonicalize.tcl is missing"
        return result
    required = ["read_liberty -unit_delay"]
    result["required"] = required
    try:
        proc = subprocess.run(
            [str(path), "-p", "help read_liberty"],
            capture_output=True, text=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["status"] = "FAIL"
        result["reason"] = f"capability probe failed: {exc}"
        return result
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        result["status"] = "FAIL"
        result["reason"] = f"capability probe rc={proc.returncode}"
        return result
    supported = [item for item in required if "-unit_delay" in output]
    result["supported"] = supported
    if len(supported) != len(required):
        result["status"] = "FAIL"
        result["reason"] = "missing read_liberty -unit_delay"
    else:
        result["status"] = "PASS"
    return result
