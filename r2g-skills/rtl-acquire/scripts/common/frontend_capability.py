#!/usr/bin/env python3
"""Installed-synthesis-frontend capability probe (RMD-HO-P1-03, held-out V3).

The expander used to write `export SYNTH_HDL_FRONTEND = slang` for ANY candidate
containing a `.sv` file, with no check that the installed Yosys can actually load
the Slang plugin. On a host without `slang.so` that guarantees a synthesis
failure; the sv2v fallback then changed how a valid SystemVerilog constant
function was interpreted, Yosys rejected the rewrite, and the terminal
classification was `low_value_failure` — a permanent source-quality verdict
issued for a missing TOOL. APB4 GPIO and WB DMA were both burned this way while
compiling cleanly under an independent standards-aware frontend.

The contract here is deliberately small and fail-closed in the SAFE direction:

  * `frontend_available(name)` runs a real canary against the installed Yosys and
    caches the verdict for the process. An unavailable frontend is never
    selected — selection falls back to the default Yosys frontend.
  * The probe NEVER claims a capability it did not observe. A probe that cannot
    run at all (no yosys, timeout) reports unavailable, because selecting an
    unverified frontend is exactly the failure mode being fixed.
  * `capability_record()` is the persistable preflight record: probe results,
    the yosys path and version, and the canary used. It rides design_meta so a
    later classification can prove WHY a frontend was or was not selected.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

PROBE_TIMEOUT_S = 60

# Canary per selectable frontend: the minimal command that proves the frontend is
# loadable by THIS yosys. `plugin -i slang` is the exact operation ORFS performs
# for SYNTH_HDL_FRONTEND=slang, so a passing canary means the real flow can load
# it too (the "declare capability only after a canary passes" rule of CAP-FE-01).
_CANARY = {
    "slang": ["-p", "plugin -i slang"],
}

_cache: dict[str, bool] = {}
_record: dict[str, dict] = {}


def _yosys_exe() -> str:
    explicit = os.environ.get("YOSYS_EXE", "").strip()
    if explicit:
        return explicit
    try:
        from skill_env import default_yosys  # noqa: PLC0415
        return default_yosys()
    except Exception:
        return shutil.which("yosys") or "yosys"


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=PROBE_TIMEOUT_S, check=False)
        return res.returncode, ((res.stdout or "") + (res.stderr or ""))[-2000:]
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def probe(name: str, *, refresh: bool = False) -> dict:
    """Probe one frontend. Returns the record dict (also cached)."""
    key = (name or "").strip().lower()
    if not refresh and key in _record:
        return _record[key]
    yosys = _yosys_exe()
    rec: dict = {"frontend": key, "yosys": yosys, "available": False,
                 "canary": None, "detail": ""}
    if key not in _CANARY:
        # An unknown frontend name is not a capability we can prove.
        rec["detail"] = "no canary registered for this frontend"
        _record[key] = rec
        _cache[key] = False
        return rec
    if shutil.which(yosys) is None and not Path(yosys).is_file():
        rec["detail"] = f"yosys not executable: {yosys}"
        _record[key] = rec
        _cache[key] = False
        return rec
    canary = [yosys, *_CANARY[key]]
    rec["canary"] = " ".join(canary)
    rc, out = _run(canary)
    rec["available"] = rc == 0
    rec["detail"] = "ok" if rc == 0 else out.strip()[-500:]
    _record[key] = rec
    _cache[key] = rec["available"]
    return rec


def frontend_available(name: str) -> bool:
    """True only when a real canary proved this frontend loadable."""
    key = (name or "").strip().lower()
    if not key:
        return False
    if key in _cache:
        return _cache[key]
    return probe(key)["available"]


def select_frontend(requested: str | None, *, needs_sv: bool) -> tuple[str | None, dict]:
    """(frontend_to_use, record) — capability-aware frontend routing.

    `requested` is the candidate's explicit `synth_frontend` column; `needs_sv`
    says the bundle contains SystemVerilog and would previously have been routed
    to slang unconditionally. An unavailable frontend yields (None, record) — the
    caller falls back to the default Yosys frontend and MUST record
    `frontend_tool_unavailable` in its notes so the terminal classification is a
    capability defer, never a source-quality exclusion.
    """
    want = (requested or "").strip().lower() or ("slang" if needs_sv else "")
    if not want:
        return None, {"frontend": None, "available": True, "detail": "default yosys frontend"}
    rec = probe(want)
    return (want if rec["available"] else None), rec


def capability_record() -> dict:
    """Everything probed so far — persist beside the candidate for provenance."""
    return {"probes": dict(_record), "yosys": _yosys_exe()}


def main() -> int:
    import argparse  # noqa: PLC0415
    import json      # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frontends", nargs="*", default=None,
                    help="frontends to probe (default: every registered one)")
    args = ap.parse_args()
    names = args.frontends or sorted(_CANARY)
    for n in names:
        probe(n)
    print(json.dumps(capability_record(), indent=2))
    return 0 if all(_cache.get(n) for n in names) else 1


if __name__ == "__main__":
    raise SystemExit(main())
