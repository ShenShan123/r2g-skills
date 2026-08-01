#!/usr/bin/env python3
"""Emit the OpenSTA setup/hold reports + V3 source manifest the label stage needs.

``03_extract_labels.py`` will only write timing labels when it can prove the
reports came from this sample's own post-route inputs: schema
``r2g2-opensta-timing-v3``, contract ``raw_route_def_raw_spef_audited_sdc``, and
a SHA256 match on ``route_def``, ``spef``, ``sdc`` plus both reports.

So the STA here is run from exactly those three files -- not from ``6_final.odb``,
which would be easier but would make the manifest attest to inputs the reports
did not actually come from. That distinction is the whole point of the gate.

Report format must match the label parser's regexes, which expect stock
``report_checks`` full output (``Startpoint:`` / ``Endpoint:`` / ``Path Type:``,
``<Delay> <Time> ^ pin (cell)`` rows, and a trailing ``<n> slack`` line). Do not
add ``-fields`` beyond the defaults without re-checking ``POINT_RE``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TIMING_MANIFEST_SCHEMA = "r2g2-opensta-timing-v3"
TIMING_SOURCE_CONTRACT = "raw_route_def_raw_spef_audited_sdc"
_START_RE = re.compile(r"^Startpoint:\s+\S+", re.MULTILINE)
_END_RE = re.compile(r"^Endpoint:\s+(\S+)", re.MULTILINE)
MAX_BEGIN = "===R2G2_PATHS_MAX_BEGIN==="
MAX_END = "===R2G2_PATHS_MAX_END==="
MIN_BEGIN = "===R2G2_PATHS_MIN_BEGIN==="
MIN_END = "===R2G2_PATHS_MIN_END==="


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_openroad(orfs_root: str) -> str:
    exe = os.environ.get("OPENROAD_EXE", "")
    if exe and Path(exe).is_file():
        return exe
    if orfs_root:
        candidate = Path(orfs_root) / "tools" / "install" / "OpenROAD" / "bin" / "openroad"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("openroad")
    if found:
        return found
    raise SystemExit("openroad not found: set OPENROAD_EXE or source scripts/flow/_env.sh")


def tcl_quote(value: Path | str) -> str:
    return "{" + str(value) + "}"


def report_checks_flags(openroad: str) -> tuple[str, str]:
    """Return this build's ``(group-count, endpoint-count)`` flag spellings.

    OpenSTA renamed ``-group_count``/``-max_paths`` to ``-group_path_count``/
    ``-endpoint_path_count``. Hardcoding either spelling makes this adapter fail
    on half the OpenROAD builds in the wild with an opaque ``STA-0563``, so ask
    the binary what it accepts.
    """

    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
        handle.write("help report_checks\nexit\n")
        probe = Path(handle.name)
    try:
        result = subprocess.run(
            [openroad, "-no_init", "-exit", str(probe)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            check=False, timeout=300,
        )
    finally:
        probe.unlink(missing_ok=True)
    text = result.stdout or ""
    group = "-group_path_count" if "-group_path_count" in text else "-group_count"
    endpoint = (
        "-endpoint_path_count" if "-endpoint_path_count" in text else "-max_paths"
    )
    return group, endpoint


def build_tcl(
    *,
    libs: list[Path],
    lefs: list[Path],
    route_def: Path,
    spef: Path,
    sdc: Path,
    max_rpt: Path,
    min_rpt: Path,
    max_paths: int,
    endpoint_paths: int,
    group_flag: str,
    endpoint_flag: str,
) -> str:
    lines = [f"read_liberty {tcl_quote(lib)}" for lib in libs]
    lines += [f"read_lef {tcl_quote(lef)}" for lef in lefs]
    lines += [
        f"read_def {tcl_quote(route_def)}",
        f"read_sdc {tcl_quote(sdc)}",
        f"read_spef {tcl_quote(spef)}",
        # -format full + default fields keeps the column layout the label
        # parser's POINT_RE assumes (…, Delay, Time).
        # report_checks writes to the interpreter's output stream and returns an
        # empty string, so `puts $fh [report_checks ...]` silently produces an
        # empty report. Fence each report with markers on stdout and split the
        # capture instead -- that works on every OpenROAD build regardless of
        # which redirection helpers it ships.
        # endpoint_flag caps paths PER ENDPOINT; group_flag caps paths per path
        # group. Setting both large spends the whole budget re-walking a few
        # endpoints -- measured on cordic/nangate45: 10005 paths covering only
        # 19 distinct endpoints. The node label is per-endpoint slack, so what
        # we want is one worst path for as MANY endpoints as possible.
        f"puts {{{MAX_BEGIN}}}",
        "report_checks -path_delay max -format full "
        f"{endpoint_flag} {endpoint_paths} {group_flag} {max_paths} -slack_max 1e30",
        f"puts {{{MAX_END}}}",
        f"puts {{{MIN_BEGIN}}}",
        "report_checks -path_delay min -format full "
        f"{endpoint_flag} {endpoint_paths} {group_flag} {max_paths} -slack_max 1e30",
        f"puts {{{MIN_END}}}",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def slice_between(text: str, begin: str, end: str) -> str:
    start = text.find(begin)
    stop = text.find(end)
    if start < 0 or stop < 0 or stop < start:
        return ""
    return text[start + len(begin) : stop].lstrip("\n")


def count_paths(path: Path) -> int:
    return len(_START_RE.findall(path.read_text(encoding="utf-8", errors="replace")))


def count_distinct_endpoints(path: Path) -> int:
    """How many distinct endpoints the report covers.

    This -- not the raw path count -- bounds how many pins can receive a slack
    label, so it is recorded in the manifest as the real coverage number.
    """

    text = path.read_text(encoding="utf-8", errors="replace")
    return len(set(_END_RE.findall(text)))


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSTA setup/hold reports + V3 manifest")
    parser.add_argument("--config", required=True, help="four-stage sample config JSON")
    parser.add_argument("--out-dir", default="", help="default <config dir>/time_rpt")
    parser.add_argument(
        "--max-paths", type=int, default=10000,
        help="paths per path group (breadth of endpoint coverage)",
    )
    parser.add_argument(
        "--endpoint-paths", type=int, default=1,
        help=(
            "paths reported PER ENDPOINT (default 1). The node label is the "
            "endpoint's worst slack, so 1 maximizes how many distinct pins get "
            "a label; raising it re-walks the same endpoints instead."
        ),
    )
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument(
        "--update-config",
        action="store_true",
        help="write the timing_* fields back into the sample config",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))

    def need(field: str) -> Path:
        raw = str(cfg.get(field, ""))
        if not raw:
            raise SystemExit(f"config missing {field}: {config_path}")
        path = Path(raw)
        if not path.is_absolute():
            path = (config_path.parent / path).resolve()
        if not path.is_file():
            raise SystemExit(f"{field} not found: {path}")
        return path

    route_def = need("route_def")
    spef = need("spef")
    sdc = need("sdc")
    libs = [Path(p) for p in cfg.get("lib", []) if Path(p).is_file()]
    lefs = [Path(p) for p in cfg.get("lef", []) if Path(p).is_file()]
    if not libs or not lefs:
        raise SystemExit("config has no usable lib/lef for STA")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else config_path.parent / "time_rpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    max_rpt = out_dir / "paths_max.rpt"
    min_rpt = out_dir / "paths_min.rpt"

    openroad = find_openroad(os.environ.get("ORFS_ROOT", ""))
    group_flag, endpoint_flag = report_checks_flags(openroad)
    print(f"[timing] report_checks flags: {endpoint_flag} {group_flag}", flush=True)
    script = build_tcl(
        libs=libs, lefs=lefs, route_def=route_def, spef=spef, sdc=sdc,
        max_rpt=max_rpt, min_rpt=min_rpt, max_paths=args.max_paths,
        endpoint_paths=args.endpoint_paths,
        group_flag=group_flag, endpoint_flag=endpoint_flag,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
        handle.write(script)
        tcl_path = Path(handle.name)
    try:
        result = subprocess.run(
            [openroad, "-no_init", "-exit", str(tcl_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            check=False, timeout=args.timeout,
        )
    finally:
        tcl_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise SystemExit(
            f"OpenSTA run failed (rc={result.returncode}):\n{result.stdout[-4000:]}"
        )
    captured = result.stdout or ""
    max_text = slice_between(captured, MAX_BEGIN, MAX_END)
    min_text = slice_between(captured, MIN_BEGIN, MIN_END)
    if not max_text or not min_text:
        raise SystemExit(
            "OpenSTA output did not contain both report markers; "
            f"rc={result.returncode}\n{captured[-4000:]}"
        )
    max_rpt.write_text(max_text, encoding="utf-8")
    min_rpt.write_text(min_text, encoding="utf-8")
    max_count = count_paths(max_rpt)
    min_count = count_paths(min_rpt)
    max_endpoints = count_distinct_endpoints(max_rpt)
    min_endpoints = count_distinct_endpoints(min_rpt)
    if max_count <= 0 or min_count <= 0:
        # Fail closed: an empty report would otherwise be attested as valid
        # provenance for zero timing labels.
        raise SystemExit(
            f"OpenSTA produced no paths (max={max_count}, min={min_count}); "
            "check that the SDC constrains this design.\n"
            f"{result.stdout[-2000:]}"
        )

    manifest = {
        "schema_version": TIMING_MANIFEST_SCHEMA,
        "source_contract": TIMING_SOURCE_CONTRACT,
        "sample_id": str(cfg.get("sample_id") or cfg.get("design_name") or config_path.stem),
        "generator": "def-graph/scripts/stage_dataset/emit_timing_reports.py",
        "inputs": {
            "route_def": {"path": str(route_def), "sha256": sha256_file(route_def)},
            "spef": {"path": str(spef), "sha256": sha256_file(spef)},
            "sdc": {"path": str(sdc), "sha256": sha256_file(sdc)},
            "sdc_source": str(sdc),
        },
        "reports": {
            "paths_max": {"path": str(max_rpt), "sha256": sha256_file(max_rpt)},
            "paths_min": {"path": str(min_rpt), "sha256": sha256_file(min_rpt)},
        },
        "analysis": {
            "max_semantics": "setup",
            "min_semantics": "hold",
            "max_path_count": max_count,
            "min_path_count": min_count,
            "max_paths_requested": args.max_paths,
            "endpoint_paths_requested": args.endpoint_paths,
            "distinct_max_endpoints": max_endpoints,
            "distinct_min_endpoints": min_endpoints,
        },
    }
    manifest_path = out_dir / "timing_manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)

    print(
        f"[timing] paths_max={max_count} (endpoints={max_endpoints}) "
        f"paths_min={min_count} (endpoints={min_endpoints})"
    )
    print(f"[timing] {manifest_path}")

    if args.update_config:
        # timing_require_manifest stays true: once reports exist, the label
        # stage must keep proving they belong to this sample rather than
        # accepting them on mere existence.
        cfg.update(
            {
                "timing_max_rpt": str(max_rpt),
                "timing_min_rpt": str(min_rpt),
                "timing_manifest": str(manifest_path),
                "timing_require_manifest": True,
                "timing_enabled": True,
            }
        )
        temporary = config_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(config_path)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"[timing] config updated: {config_path}")


if __name__ == "__main__":
    main()
