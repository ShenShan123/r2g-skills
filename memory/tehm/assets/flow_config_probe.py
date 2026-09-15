"""Expand a trusted ORFS project with its pinned Make parser, without EDA.

Makefiles are executable input: this is for the user's in-scope toolchain and
project, not an untrusted-file sandbox. Output is a configuration observation,
not a toolchain authority receipt or evidence of design success.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from tehm.ids import stable_dumps
from tehm.orfs_runtime_resources import verify_runtime_resources
from .flow_config import _numeric, _RANGES


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_flow_config(project: Path, orfs_root: Path, *, keys: tuple[str, ...],
                      make_exe: Path, python_exe: Path, openroad_exe: Path,
                      yosys_exe: Path, klayout_exe: Path | None = None,
                      runtime_resources: dict | None = None) -> dict:
    """Double-expand with input hashes checked between and after both probes.

The environment is deliberately explicit; this does not claim to reproduce
ambient shell overrides. Execution must consume or recheck these observations.
An explicit KLayout pin selects v2 and binds Make's version-query command.
Legacy v1 remains reproducible but may discover host KLayout; neither version
is evidence of transitive native-library closure or design-flow execution.
Explicit resource pins select v3 and bind Make's child environment only; they
cannot retroactively attest the parent interpreter's startup environment.
"""
    resources = (verify_runtime_resources(runtime_resources)
                 if runtime_resources is not None else None)
    if resources is not None and klayout_exe is None:
        raise ValueError("resource-bound configuration probe requires an explicit KLayout pin")
    project, orfs_root = project.resolve(strict=True), orfs_root.resolve(strict=True)
    if not keys or len(set(keys)) != len(keys) or any(key not in _RANGES for key in keys):
        raise ValueError("configuration probe requires supported unique keys")
    binaries = {name: Path(path).resolve(strict=True) for name, path in {
        "make": make_exe, "python": python_exe,
        "openroad": openroad_exe, "yosys": yosys_exe}.items()}
    if klayout_exe is not None:
        binaries["klayout"] = Path(klayout_exe).resolve(strict=True)
        # KLAYOUT_CMD is expanded as a shell command by trusted ORFS Makefiles.
        # Do not accept shell/Make syntax or ambiguous whitespace in this pin.
        if not re.fullmatch(r"/[A-Za-z0-9_./:+-]+", str(binaries["klayout"])):
            raise ValueError("configuration probe KLayout pin must be a shell-safe absolute path")
    if any(not p.is_file() or not os.access(p, os.X_OK) for p in binaries.values()):
        raise ValueError("configuration probe tool pins must be executable files")
    config = project / "constraints" / "config.mk"
    flow = orfs_root / "flow"
    fields = (*keys, "PLATFORM", "DESIGN_NAME", "SCRIPTS_DIR")
    if klayout_exe is not None:
        fields = (*fields, "KLAYOUT_CMD")
    if resources is not None:
        fields = (*fields, *sorted(resources["environment"]))
    target = "tehm-effective-config-probe"
    recipe = target + ":\n\t" + "".join(
        "$(info TEHM_CONFIG:" + key + "=$(" + key + "))" for key in fields)
    recipe += "$(info TEHM_FILES:$(MAKEFILE_LIST))@:"
    args = [str(binaries["make"]), "--no-print-directory", "-f", str(flow / "Makefile"),
            "--eval", recipe, target, "DESIGN_CONFIG=" + str(config),
            "PYTHON_EXE=" + str(binaries["python"]),
            "OPENROAD_EXE=" + str(binaries["openroad"]),
            "YOSYS_EXE=" + str(binaries["yosys"]), "NUM_CORES=2"]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"}
    if klayout_exe is not None:
        env["KLAYOUT_CMD"] = str(binaries["klayout"])
        args.append("KLAYOUT_CMD=" + env["KLAYOUT_CMD"])
    if resources is not None:
        env.update(resources["environment"])
        args.extend(key + "=" + value for key, value in sorted(resources["environment"].items()))

    def expand(work):
        result = subprocess.run(args, cwd=work, env=env, capture_output=True,
                                text=True, timeout=30, check=True)
        values, file_lines = {}, []
        for line in result.stdout.splitlines():
            if line.startswith("TEHM_CONFIG:"):
                key, value = line[len("TEHM_CONFIG:"):].split("=", 1)
                if key in values:
                    raise ValueError("duplicate configuration probe field")
                values[key] = value
            elif line.startswith("TEHM_FILES:"):
                file_lines.append(line[len("TEHM_FILES:"):])
        if set(values) != set(fields) or len(file_lines) != 1:
            raise ValueError("configuration probe output is incomplete")
        if klayout_exe is not None and values.pop("KLAYOUT_CMD") != env["KLAYOUT_CMD"]:
            raise ValueError("configuration probe KLayout command override")
        if resources is not None:
            if any(values.pop(key) != value for key, value in resources["environment"].items()):
                raise ValueError("configuration probe runtime resource environment override")
            verify_runtime_resources(resources)
        if Path(values.pop("SCRIPTS_DIR")).resolve() != flow / "scripts":
            raise ValueError("configuration probe does not support script overlays")
        for key in keys:
            _numeric(key, values[key])
        files = {Path(p).resolve(strict=True) for p in file_lines[0].split()}
        files.update({flow / "scripts" / "defaults.py", flow / "scripts" / "variables.json"})
        if config not in files or flow / "Makefile" not in files:
            raise ValueError("configuration probe source files are missing")
        if any(not (p.is_relative_to(project) or p.is_relative_to(orfs_root)) for p in files):
            raise ValueError("configuration probe includes files outside frozen roots")
        return values, {str(p): _sha(p) for p in sorted(files)}

    binary_hashes = {str(p): _sha(p) for p in binaries.values()}
    with tempfile.TemporaryDirectory(prefix="tehm-config-probe-") as work:
        first, hashes = expand(work)
        second, final_hashes = expand(work)
    if first != second or hashes != final_hashes:
        raise ValueError("configuration changed during probe")
    if any(_sha(p) != digest for p, digest in {**hashes, **binary_hashes}.items()):
        raise ValueError("configuration probe inputs changed")
    if resources is not None:
        verify_runtime_resources(resources)
    payload = {"version": "orfs-effective-config-probe-v3" if resources is not None
               else "orfs-effective-config-probe-v2" if klayout_exe is not None
               else "orfs-effective-config-probe-v1", "project": str(project),
               "orfs_root": str(orfs_root), "values": first,
               "input_sha256": hashes, "tool_sha256": binary_hashes,
               "environment": env, "eda_executed": False,
               "scope": "explicit_environment_make_expansion_only"}
    if klayout_exe is not None:
        payload["tool_bindings"] = {"KLAYOUT_CMD": str(binaries["klayout"])}
    if resources is not None:
        payload["runtime_resources"] = resources
        payload["runtime_resource_sha256"] = {
            path: pin.removeprefix("sha256:") for path, pin in resources["file_sha256"].items()}
        payload["parent_launch_binding_verified"] = False
        payload["native_closure_proven"] = False
    return {**payload, "receipt_digest": "sha256:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()}
