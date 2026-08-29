"""Preflight semantic effect of ORFS routing configuration actions.

The ORFS flow accepts ``ROUTING_LAYER_ADJUSTMENT`` as a configuration knob,
but a platform's ``fastroute.tcl`` may replace it with a literal value.  In
that case editing ``config.mk`` creates a syntactically different arm without
changing the executed flow.  This module makes that boundary explicit before
an A/B trial starts.

The check is intentionally content-addressed and read-only.  It does not
execute Tcl or mutate a project.  If an ORFS root was supplied but the hook
cannot be inspected, the result is ``UNKNOWN`` and callers must fail closed.
When no root is supplied (for hermetic unit fixtures), the result is
``NOT_CHECKED`` so the compatibility executor can still exercise its fake
flow; production callers pass ``ORFS_ROOT`` and therefore receive the strict
decision.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from tehm.ids import stable_dumps

ROUTING_LAYER_ADJUSTMENT = "ROUTING_LAYER_ADJUSTMENT"
PREFLIGHT_VERSION = "orfs-routing-preflight-v1"

_ROUTING_COMMAND = re.compile(
    r"(?m)^\s*set_global_routing_layer_adjustment\b(?P<body>[^#\r\n]*)")
_ENV_KNOB = re.compile(
    r"(?:\$::env\(|\$env\()ROUTING_LAYER_ADJUSTMENT\)?")
_MAKE_VAR = re.compile(r"\$\(([^)]+)\)|\$\{([^}]+)\}")


def inspect_routing_layer_adjustment(
        platform: str, config_edits: Mapping,
        *, config: Mapping | None = None, project_dir: Path | None = None,
        orfs_root: str | Path | None = None) -> dict:
    """Return a deterministic semantic preflight for a routing knob edit.

    ``config`` is the parsed ``config.mk`` before the candidate edit.  A
    configured ``FASTROUTE_TCL`` takes precedence over the platform hook,
    matching ORFS's flow contract.  Relative custom hooks resolve against the
    project directory; otherwise the standard platform hook under
    ``<ORFS_ROOT>/flow/platforms/<platform>/fastroute.tcl`` is inspected.
    """
    edits = {str(key): str(value) for key, value in (config_edits or {}).items()}
    base = {
        "version": PREFLIGHT_VERSION,
        "knob": ROUTING_LAYER_ADJUSTMENT,
        "platform": str(platform or ""),
        "requested": ROUTING_LAYER_ADJUSTMENT in edits,
    }
    if ROUTING_LAYER_ADJUSTMENT not in edits:
        return {**base, "status": "NOT_APPLICABLE", "enforced": False,
                "reason": "routing_knob_not_present"}

    config = config or {}
    root_value = orfs_root
    if root_value is None:
        root_value = (config or {}).get("ORFS_ROOT")
    if root_value is None or not str(root_value).strip():
        return {**base, "status": "NOT_CHECKED", "enforced": False,
                "reason": "orfs_root_not_declared"}
    root = Path(str(root_value)).expanduser().resolve()

    hook_value = config.get("FASTROUTE_TCL")
    if hook_value:
        raw_hook = str(hook_value)
        hook = Path(_expand_make_path(
            raw_hook, root=root, platform=str(platform), config=config))
        if not hook.is_absolute():
            hook = (Path(project_dir).resolve() / hook
                    if project_dir is not None else root / hook)
        hook_source = "config.FASTROUTE_TCL"
    else:
        hook = root / "flow" / "platforms" / str(platform) / "fastroute.tcl"
        hook_source = "platform.fastroute.tcl"

    common = {**base, "enforced": True, "hook": str(hook),
              "hook_source": hook_source}
    if hook_value:
        common["configured_hook"] = str(hook_value)
    try:
        content = hook.read_text(errors="replace")
    except OSError as exc:
        return {**common, "status": "UNKNOWN",
                "reason": "routing_hook_unavailable",
                "error": f"{type(exc).__name__}: {exc}"}

    hook_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Tcl line continuations are semantically one command.  Normalize only
    # for inspection; the digest above remains over the original hook bytes.
    normalized = re.sub(r"\\\s*\n", " ", content)
    commands = []
    for match in _ROUTING_COMMAND.finditer(normalized):
        body = match.group("body").strip()
        commands.append({"text": match.group(0).strip(),
                         "uses_config_knob": bool(_ENV_KNOB.search(body)),
                         "body": body})
    common.update({"hook_sha256": hook_sha256,
                   "hook_bytes": len(content.encode("utf-8")),
                   "commands": commands})

    if not commands:
        # An explicit FASTROUTE_TCL hook suppresses floorplan.tcl's inline
        # fallback.  Without a direct command consuming the knob, the edit is
        # not demonstrated to affect routing; remain fail-closed.
        return {**common, "status": "NO_OP",
                "applicability": "INAPPLICABLE",
                "reason": "routing_hook_does_not_consume_config_knob"}
    if all(command["uses_config_knob"] for command in commands):
        return {**common, "status": "EFFECTIVE",
                "reason": "routing_hook_consumes_config_knob"}
    return {**common, "status": "NO_OP",
            "applicability": "INAPPLICABLE",
            "reason": "routing_hook_overrides_config_knob"}


def preflight_digest(result: Mapping) -> str:
    """Digest a preflight result for an external audit receipt."""
    return hashlib.sha256(stable_dumps(dict(result)).encode("utf-8")).hexdigest()


def _expand_make_path(value: str, *, root: Path, platform: str,
                      config: Mapping) -> str:
    """Resolve the small Make-variable vocabulary used by ORFS config files."""
    nickname = str(config.get("DESIGN_NICKNAME") or
                   config.get("DESIGN_NAME") or "")
    variables = {
        "ORFS_ROOT": str(root),
        "DESIGN_HOME": str(root / "flow" / "designs"),
        "PLATFORM_DIR": str(root / "flow" / "platforms" / platform),
        "DESIGN_DIR": str(root / "flow" / "designs" / platform / nickname),
        "PLATFORM": platform,
        "DESIGN_NICKNAME": nickname,
        "DESIGN_NAME": str(config.get("DESIGN_NAME") or nickname),
    }
    expanded = str(value)
    for _ in range(3):
        changed = False

        def replace(match):
            nonlocal changed
            key = match.group(1) or match.group(2)
            if key in variables:
                changed = True
                return variables[key]
            return match.group(0)

        expanded = _MAKE_VAR.sub(replace, expanded)
        if not changed:
            break
    return expanded
