"""Bounded, read-only host tool and platform observations.

READY means the registered prerequisites were observed, never that an EDA run
passed. Host configuration selects paths; model input selects only a platform.
No tool installation, database initialization, make evaluation or EDA is done.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

from .paths import defgraph_scripts, repo_root, signoff_scripts

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_ASSETS = 64
MAX_OUTPUT_BYTES = 16384
TOTAL_SECONDS = 35
PROBE_SECONDS = 3

# Formats describe existing adapters, not a claim that every dialect is accepted.
TOOLS = {
    "openroad": ("OPENROAD_EXE", ("-version",), ("LEF", "DEF", "ODB", "Verilog", "Liberty", "SDC", "SPEF")),
    "yosys": ("YOSYS_EXE", ("-V",), ("Verilog", "SystemVerilog subset", "Liberty")),
    "klayout": ("KLAYOUT_CMD", ("-v",), ("GDSII", "OASIS", "LEF", "DEF")),
    "sta": ("STA_EXE", ("-version",), ("Verilog", "Liberty", "SDC", "SPEF")),
    "iverilog": ("IVERILOG_EXE", ("-V",), ("Verilog", "SystemVerilog subset")),
    "vvp": ("VVP_EXE", ("-V",), ("Icarus compiled simulation",)),
    "verilator": ("VERILATOR_EXE", ("--version",), ("Verilog", "SystemVerilog subset")),
    "magic": ("MAGIC_EXE", ("--version",), ("GDSII", "LEF", "DEF", "MAG")),
    # There is no registered side-effect-free version invocation for all Netgen
    # launchers supported here. Presence must not be upgraded by guessing flags.
    "netgen": ("NETGEN_EXE", None, ("SPICE", "Verilog")),
    "orfs_python": ("R2G_ORFS_PYTHON", ("-B", "-c", "import json,sys,yaml; "
        "print(json.dumps({'python':sys.version.split()[0],'yaml':yaml.__version__}))"), ("YAML",)),
    "graph_python": ("R2G_GRAPH_PYTHON", ("-B", "-c", "import json,sys,torch,torch_geometric,pandas; "
        "print(json.dumps({'python':sys.version.split()[0],'torch':torch.__version__,"
        "'torch_geometric':torch_geometric.__version__,'pandas':pandas.__version__}))"), ("PyG", "CSV")),
    "image_python": ("R2G_GRAPH_PYTHON", ("-B", "-c", "import json,sys,numpy; "
        "print(json.dumps({'python':sys.version.split()[0],'numpy':numpy.__version__}))"), ("NPZ", "CSV")),
}
ENV_KEYS = tuple(dict.fromkeys(["ORFS_ROOT", "FLOW_DIR", "PDK_ROOT", "SKY130A_DIR", "PATH",
    "LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "PYTHONPATH", "HOME",
    "R2G_ENV_FILE", "R2G_IGNORE_ENV_LOCAL", "R2G_IVERILOG_EXE", "R2G_VVP_EXE",
    "ADDITIONAL_LIBS", "ADDITIONAL_LEFS", "ADDITIONAL_GDS",
    *[v[0] for v in TOOLS.values()]]))


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _platform(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
        raise ValueError("platform must be a registered-style name, not a path or command")
    return name


class _Reader:
    def __init__(self, deadline):
        self.deadline, self.used, self.cache = deadline, 0, {}

    def read(self, path: Path, *, text=False, roots=()):
        path = Path(path)
        resolved = path.resolve()
        if roots and not any(resolved.is_relative_to(root.resolve()) for root in roots):
            raise ValueError("asset escapes selected host roots")
        if resolved in self.cache:
            data = self.cache[resolved]
        else:
            if time.monotonic() >= self.deadline:
                raise ValueError("inventory time budget exhausted")
            fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError("file type or size limit refused")
                if self.used + before.st_size > MAX_TOTAL_BYTES:
                    raise ValueError("inventory byte budget exhausted")
                chunks, count = [], 0
                while chunk := stream.read(min(1024 * 1024, MAX_FILE_BYTES + 1 - count)):
                    chunks.append(chunk)
                    count += len(chunk)
                    if time.monotonic() >= self.deadline or count > MAX_FILE_BYTES:
                        raise ValueError("inventory read budget exhausted")
                data = b"".join(chunks)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                    raise ValueError("file changed during inventory")
            self.used += len(data)
            self.cache[resolved] = data
        if text:
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError("text inspection byte limit exceeded")
            return data.decode("utf-8", errors="replace")
        return data

    def file(self, path, *, roots=()):
        if not path:
            return {"status": "missing", "path": None, "sha256": None}
        if len(str(path)) > 4096:
            return {"status": "unverified", "path": str(path)[:512], "sha256": None, "reason": "path length limit exceeded"}
        try:
            data = self.read(Path(path), roots=roots)
            return {"status": "present" if data else "unverified", "path": str(Path(path)), "resolved_path": str(Path(path).resolve()),
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        except FileNotFoundError:
            return {"status": "missing", "path": str(path), "sha256": None}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"status": "unverified", "path": str(path), "sha256": None, "reason": str(exc)[:300]}


def _probe(argv, env, timeout):
    """Fixed argv only; cap output, deadline and descendant lifetime."""
    if timeout <= 0:
        return {"status": "timeout", "text": "", "exit_code": None}
    process = None
    data = bytearray()
    status = "ok"
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env, cwd=repo_root(), start_new_session=True)
        end = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = end - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        data.extend(chunk)
                        if len(data) > MAX_OUTPUT_BYTES:
                            status = "output_limit"
                            break
                if status != "ok":
                    break
            if status == "ok":
                try:
                    process.wait(timeout=max(0.001, end - time.monotonic()))
                except subprocess.TimeoutExpired:
                    status = "timeout"
    except OSError as exc:
        return {"status": "failed", "text": type(exc).__name__, "exit_code": None}
    finally:
        if process is not None:
            # A wrapper may exit while descendants keep its pipe open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
    return {"status": status if status != "ok" or process.returncode == 0 else "failed",
            "exit_code": process.returncode, "text": bytes(data[:MAX_OUTPUT_BYTES]).decode(errors="replace")}


def _resolve_env(deadline):
    # Source the same trusted host scripts as the runners. Values are emitted
    # from an allowlist; no arbitrary command or user design path is accepted.
    # Each runner starts from the original caller environment. Sequentially
    # sourcing both scripts instead lets signoff pins leak into graph discovery
    # and repeats path prefixes, creating false conflicts for identical pins.
    original = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NUM_CORES": "1"}
    deadline = min(deadline, time.monotonic() + 8)
    script = 'source "$1" >/dev/null 2>&1 || exit 3\n"$2" -B -c "$3"'
    code = ("import json,os; print(json.dumps({k:os.environ[k] for k in " + repr(ENV_KEYS) +
            " if k in os.environ}))")
    resolved = []
    for root in (signoff_scripts(), defgraph_scripts()):
        probe = _probe(["bash", "-c", script, "r2g-capability-env",
            str(root / "flow/_env.sh"), sys.executable, code], original.copy(), deadline - time.monotonic())
        if probe["status"] != "ok":
            return {}, {"status": "unverified", "reason": "host environment resolution failed", "probe_status": probe["status"]}
        try:
            env = json.loads(probe["text"])
            if not isinstance(env, dict) or any(k not in ENV_KEYS or not isinstance(v, str) for k, v in env.items()):
                raise ValueError("invalid environment payload")
        except (ValueError, TypeError):
            return {}, {"status": "unverified", "reason": "host environment response exceeded bounds or was invalid"}
        resolved.append(env)
    env, graph = resolved
    compared = [key for key in ENV_KEYS if key.endswith("_EXE") or key in (
        "KLAYOUT_CMD", "FLOW_DIR", "PDK_ROOT", "LD_LIBRARY_PATH", "PYTHONPATH", "QT_PLUGIN_PATH")]
    conflicts = [key for key in compared if env.get(key, "") != graph.get(key, "")]
    # The dataset runner explicitly preserves its caller's interpreter override.
    graph_python = original.get("R2G_GRAPH_PYTHON") or graph.get("R2G_GRAPH_PYTHON")
    env.pop("R2G_GRAPH_PYTHON", None)
    if graph_python:
        env["R2G_GRAPH_PYTHON"] = graph_python
    return env, {"status": "resolved", "source": "signoff-loop pins; def-graph Python pin",
                 "conflicting_skill_pins": conflicts}


def _config(text):
    values = {}
    for line in re.sub(r"\\\n\s*", " ", text).splitlines():
        match = re.match(r"\s*(?:(?:export|override)\s+)*([A-Z][A-Z0-9_]*)\s*(\?=|::=|:=|\+=|=)\s*(.*?)\s*(?:#.*)?$", line)
        if match:
            key, operation, value = match.groups()
            if operation == "+=":
                values[key] = (values.get(key, "") + " " + value).strip()
            elif operation == "?=":
                values.setdefault(key, value)
            else:
                values[key] = value
    return values


def _asset_paths(raw, pdir, platform, env):
    unresolved, paths = [], []
    values = {"PLATFORM_DIR": str(pdir), "PLATFORM": platform,
              **{key: env.get(key, "") for key in ("ADDITIONAL_LIBS", "ADDITIONAL_LEFS", "ADDITIONAL_GDS")}}
    expanded = re.sub(r"\$\(([A-Z_]+)\)", lambda m: values.get(m[1], m[0]), raw or "")

    def wildcard(match):
        pattern = Path(match[1])
        if not pattern.parent.resolve().is_relative_to(pdir.parent.parent.resolve()):
            unresolved.append("asset wildcard escapes selected flow root")
            return ""
        found = []
        for path in pattern.parent.glob(pattern.name):
            if len(found) >= MAX_ASSETS:
                unresolved.append("asset wildcard limit exceeded")
                break
            found.append(str(path))
        return " ".join(sorted(found))

    # Recognize only the existing literal GDS-list idiom, never invoke make.
    expanded = re.sub(r"\$\(sort\s+\$\(wildcard\s+([^()]+)\)\)", wildcard, expanded)
    for token in expanded.split():
        if len(token) > 4096 or "$" in token or any(c in token for c in "*?()"):
            unresolved.append(token[:200])
        else:
            path = Path(token)
            paths.append(path if path.is_absolute() else pdir / path)
    if len(paths) + len(unresolved) > MAX_ASSETS:
        unresolved = ["asset list limit exceeded", *unresolved]
    return paths[:MAX_ASSETS], unresolved[:MAX_ASSETS]


def _skill_probe(reader, flow, platform, env):
    path = signoff_scripts() / "flow/platform_capability.py"
    namespace = {"__file__": str(path), "__name__": "_r2g_capability_probe"}
    exec(compile(reader.read(path, text=True), str(path), "exec"), namespace)

    roots = (Path(flow),) + ((Path(env["PDK_ROOT"]),) if env.get("PDK_ROOT") else ())

    def read(path):
        try:
            return reader.read(Path(path), text=True, roots=roots) if path else None
        except (OSError, ValueError):
            return None

    def glob(pattern):
        parent, name = Path(pattern).parent, Path(pattern).name
        found = []
        for path in parent.glob(name):
            if len(found) >= MAX_ASSETS:
                raise ValueError("platform discovery entry limit exceeded")
            found.append(str(path))
        return found

    namespace["_read"] = read
    namespace["glob"] = SimpleNamespace(glob=glob)
    return namespace["probe_platform"](str(flow), platform, env=env)


def _environment(resolved_env, deadline):
    if resolved_env is None:
        env, record = _resolve_env(deadline)
    else:
        env = {k: v for k, v in resolved_env.items() if k in ENV_KEYS and isinstance(v, str)}
        record = {"status": "resolved", "source": "host-supplied resolved environment"}
    record["sha256"] = _digest({k: env.get(k) for k in ENV_KEYS if k not in ("PATH", "HOME")})
    return env, record


def _collect_tools(reader, env, names, probe_versions):
    runtime_env = {**env, "PATH": env.get("PATH", os.defpath), "PYTHONDONTWRITEBYTECODE": "1",
                   "QT_QPA_PLATFORM": "offscreen", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    tools = {}
    for name in names:
        variable, argv, formats = TOOLS[name]
        selected = env.get(variable)
        if name == "orfs_python":
            selected = shutil.which("python3", path=runtime_env["PATH"])
        if not selected and name in ("iverilog", "vvp"):
            selected = env.get("R2G_" + variable)
        path = selected if selected and Path(selected).is_absolute() else (
            shutil.which(selected, path=runtime_env["PATH"]) if selected else None)
        item = reader.file(path)
        item.update(env_key=variable, read_formats=list(formats), format_validation="unverified",
                    identity_scope="selected executable file; dependencies and runtime behavior are not certified")
        executable = item["status"] == "present" and os.access(path, os.X_OK)
        version = {"status": "skipped", "text": "", "exit_code": None}
        if executable and probe_versions and argv:
            version = _probe([str(path), *argv], runtime_env, min(PROBE_SECONDS if name != "graph_python" else 8,
                                                               reader.deadline - time.monotonic()))
        elif argv is None:
            version["status"] = "unsupported"
        version["output_sha256"] = hashlib.sha256(version["text"].encode()).hexdigest()
        version["text"] = next((line for line in version["text"].splitlines() if line.strip()), "")[:600]
        if item["status"] == "present" and not executable:
            item.update(status="unverified", reason="configured file is not executable")
        item.update(version=version, usable=bool(executable and version["status"] == "ok"))
        tools[name] = item
    return tools


def tool_inventory(names=("iverilog",), *, probe_versions=True, resolved_env=None) -> dict:
    """Selected host tool identity/probes without inspecting any PDK assets."""
    if not isinstance(names, (tuple, list)) or not names or len(names) > len(TOOLS) or any(name not in TOOLS for name in names):
        raise ValueError("select a nonempty bounded list of registered tools")
    names = tuple(dict.fromkeys(names))
    deadline = time.monotonic() + min(TOTAL_SECONDS, 8 + 8 * len(names))
    reader = _Reader(deadline)
    env, environment = _environment(resolved_env, deadline)
    result = {"schema_version": 1, "scope": "selected_tools_only", "environment": environment,
              "tools": _collect_tools(reader, env, names, probe_versions), "observed_bytes": reader.used}
    result["inventory_id"] = _digest(result)
    return result


def inventory(platform="nangate45", *, probe_versions=True, resolved_env=None) -> dict:
    """Observe host-selected tools/assets; never read user projects or stores.

    resolved_env is an internal host/testing interface, not a model argument.
    Omitting it uses the existing trusted skill environment resolver.
    """
    platform = _platform(platform)
    deadline = time.monotonic() + TOTAL_SECONDS
    reader = _Reader(deadline)
    env, environment = _environment(resolved_env, deadline)
    tools = _collect_tools(reader, env, TOOLS, probe_versions)
    flow = Path(env["FLOW_DIR"]) if env.get("FLOW_DIR") else (
        Path(env["ORFS_ROOT"]) / "flow" if env.get("ORFS_ROOT") else None)
    pdir = flow / "platforms" / platform if flow else None
    roots = tuple(p for p in (flow, Path(env["PDK_ROOT"]) if env.get("PDK_ROOT") else None) if p)
    assets, observations, cfg = {}, [], {}
    assets["platform_config"] = reader.file(pdir / "config.mk" if pdir else None, roots=roots)
    if assets["platform_config"]["status"] == "present":
        try:
            cfg = _config(reader.read(pdir / "config.mk", text=True, roots=roots))
        except (OSError, ValueError) as exc:
            observations.append(str(exc))
    for name, variable in (("technology_lef", "TECH_LEF"), ("standard_cell_lef", "SC_LEF"),
            ("timing_libraries", "LIB_FILES"), ("drc", "KLAYOUT_DRC_FILE"), ("lvs", "KLAYOUT_LVS_FILE"),
            ("rcx", "RCX_RULES"), ("layout_technology", "KLAYOUT_TECH_FILE"), ("cell_netlist", "CDL_FILE"),
            ("standard_cell_gds", "GDS_FILES")):
        paths, unresolved = _asset_paths(cfg.get(variable), pdir, platform, env) if pdir else ([], [])
        files = [reader.file(path, roots=roots) for path in paths]
        status = "unverified" if unresolved or any(f["status"] == "unverified" for f in files) else (
            "present" if files and all(f["status"] == "present" for f in files) else "missing")
        assets[name] = {"status": status, "config_key": variable, "files": files, "unresolved": unresolved}
    caps = {}
    if flow and assets["platform_config"]["status"] == "present":
        try:
            caps = _skill_probe(reader, flow, platform, env)
        except (OSError, ValueError, RuntimeError) as exc:
            observations.append("existing platform probe unavailable: " + str(exc)[:200])
    assets["antenna"] = {"status": "present" if caps.get("antenna", {}).get("ok") else "unverified",
                         "details": {k: v[:MAX_ASSETS] if isinstance(v, list) else v
                                     for k, v in caps.get("antenna", {}).items()},
                         "source": "existing platform_capability.py"}
    # Report the existing resolver's explicit fallback paths without inventing
    # cross-platform decks. ASAP7 remains unsupported even when files exist.
    for name, key in (("drc", "drc_deck"), ("lvs", "lvs"), ("rcx", "rcx")):
        found = caps.get(key, {})
        if not assets[name]["files"] and found.get("path"):
            record = reader.file(found["path"], roots=roots)
            assets[name].update(status=record["status"], files=[record], source=found.get("source", "existing platform probe"))
    libraries = []
    for item in assets["timing_libraries"]["files"]:
        if item["status"] != "present":
            continue
        try:
            data = reader.read(Path(item["path"]))
            if item["path"].endswith(".gz"):
                import io
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                    data = stream.read(MAX_TEXT_BYTES + 1)
            if len(data) > MAX_TEXT_BYTES:
                raise ValueError("liberty text limit exceeded")
            text = data.decode(errors="replace")
            libraries.append({"path": item["path"], "libraries": re.findall(r"\blibrary\s*\(\s*([^)]{1,160})\)", text)[:16],
                "operating_conditions": re.findall(r"\boperating_conditions\s*\(\s*([^)]{1,160})\)", text)[:16],
                "nominal": {key: (re.search(r"\b" + key + r"\s*:\s*([^;]{1,80});", text).group(1)
                                  if re.search(r"\b" + key + r"\s*:\s*([^;]{1,80});", text) else None)
                            for key in ("nom_process", "nom_temperature", "nom_voltage")},
                "cell_count": len(re.findall(r"\bcell\s*\(", text))})
        except (OSError, ValueError) as exc:
            observations.append("liberty metadata unverified: " + str(exc)[:200])
    assets["timing_libraries"]["library_metadata"] = libraries
    assets["lvs"]["engine"] = caps.get("lvs", {}).get("engine", "unverified")
    if caps.get("lvs", {}).get("engine") == "netgen":
        assets["lvs"]["setup_files"] = [reader.file(caps["lvs"].get(k), roots=roots) for k in ("pdk_tech", "pdk_setup")]
        assets["lvs"]["status"] = "present" if caps["lvs"].get("ok") else "unverified"
    orfs = {"makefile": reader.file(flow / "Makefile" if flow else None),
            "settings": reader.file(flow / "settings.mk" if flow else None),
            "defaults_helper": reader.file(flow / "scripts/defaults.py" if flow else None)}
    orfs["defaults_helper"]["executable"] = bool(flow and os.access(flow / "scripts/defaults.py", os.X_OK))
    orfs["revision"] = {"status": "unverified", "commit": None, "worktree_clean": None}
    if flow:
        try:
            gitdir = flow.parent / ".git"
            head = reader.read(gitdir / "HEAD", text=True, roots=(flow.parent,)).strip()
            if head.startswith("ref: "):
                head = reader.read(gitdir / head[5:], text=True, roots=(gitdir,)).strip()
            if re.fullmatch(r"[a-f0-9]{40,64}", head):
                orfs["revision"] = {"status": "observed", "commit": head, "worktree_clean": None}
        except (OSError, ValueError):
            pass
    overrides = []
    if flow and orfs["settings"]["status"] == "present":
        try:
            settings = _config(reader.read(flow / "settings.mk", text=True))
        except (OSError, ValueError) as exc:
            settings = {}
            overrides.append({"status": "unverified", "reason": "settings inspection failed: " + str(exc)[:200]})
        for key, raw in settings.items():
            if key.endswith("_EXE") and raw != env.get(key):
                overrides.append({"variable": key, "value": raw[:200], "status": "unverified",
                                  "reason": "ORFS settings may override the resolved tool pin"})
    environment["pins"] = [reader.file(signoff_scripts().parent / "references/env.local.sh"),
                           reader.file(defgraph_scripts().parent / "references/env.local.sh")]
    role = "predictive_research" if platform in ("nangate45", "asap7") else "unverified"
    result = {"schema_version": 1, "platform": {"name": platform, "agent_supported": platform == "nangate45",
        "role": role, "manufacturing_qualified": False, "directory": str(pdir) if pdir else None,
        "scope_source": "r2g_agent/platforms.py", "unsupported_reason": caps.get("unsupported_reason") or (
            None if platform == "nangate45" else "not registered in the agent's supported platforms")},
        "environment": environment, "tools": tools, "assets": assets, "orfs": orfs,
        "tool_pin_overrides": overrides, "observations": observations[:32],
        "combinations": [{"id": "r2g-" + platform + "-strict-graph", "status": "unverified",
            "registered": platform == "nangate45", "evidence": [],
            "reason": "inventory does not execute or certify an EDA toolchain combination"}],
        "verified_combinations": [], "limits": {"max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES,
            "max_assets_per_role": MAX_ASSETS, "wall_seconds": TOTAL_SECONDS},
        "observed_bytes": reader.used}
    result["inventory_id"] = _digest(result)
    return result


def requirements(platform="nangate45", output="graph") -> dict:
    platform = _platform(platform)
    return {"platform": platform, "output": output, "agent_supported": platform == "nangate45" and output in ("graph", "image"),
        "tools": ["openroad", "yosys", "klayout", "iverilog", "vvp", "orfs_python",
                  "image_python" if output == "image" else "graph_python"],
        "assets": ["platform_config", "technology_lef", "standard_cell_lef", "timing_libraries",
                   "drc", "lvs", "rcx", "antenna", "layout_technology", "standard_cell_gds", "cell_netlist"],
        "checks": ["drc", "lvs", "route", "rcx", "timing", output + "_verification"],
        "scope": "registered strict graph prerequisites; not manufacturing signoff certification"}


def check_requirements(snapshot: dict, *, output="graph") -> dict:
    req = requirements(snapshot.get("platform", {}).get("name", "unknown"), output)
    missing, unverified, unsupported = [], [], []
    if not req["agent_supported"] or not snapshot.get("platform", {}).get("agent_supported"):
        unsupported.append("platform/output is not registered for this agent admission policy")
    for key in req["tools"]:
        tool = snapshot.get("tools", {}).get(key, {})
        if tool.get("status") == "missing" or not tool:
            missing.append("tool:" + key)
        elif not tool.get("usable"):
            unverified.append("tool:" + key)
    for key in req["assets"]:
        asset = snapshot.get("assets", {}).get(key, {})
        if asset.get("status") == "missing" or not asset:
            missing.append("asset:" + key)
        elif asset.get("status") != "present":
            unverified.append("asset:" + key)
    if snapshot.get("environment", {}).get("status") != "resolved":
        unverified.append("resolved_environment")
    if snapshot.get("environment", {}).get("conflicting_skill_pins"):
        unverified.append("conflicting_skill_pins")
    if snapshot.get("tool_pin_overrides"):
        unverified.append("orfs_tool_pin_overrides")
    for name in ("makefile", "defaults_helper"):
        if snapshot.get("orfs", {}).get(name, {}).get("status") != "present":
            missing.append("orfs:" + name)
    if not snapshot.get("orfs", {}).get("defaults_helper", {}).get("executable"):
        missing.append("orfs:defaults_helper_executable")
    return {"verdict": "BLOCKED" if missing or unsupported else "UNVERIFIED" if unverified else "READY",
            "missing": missing, "unsupported": unsupported, "unverified": unverified,
            "combination_verification": "UNVERIFIED", "inventory_id": snapshot.get("inventory_id"),
            "requirements": req, "meaning": "READY admits prerequisite-complete work; it does not assert EDA PASS"}
