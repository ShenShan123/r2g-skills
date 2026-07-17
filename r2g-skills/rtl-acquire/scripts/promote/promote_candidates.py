#!/usr/bin/env python3
"""Promote synth-proven rtl-acquire candidates into signoff-loop full-flow projects.

One-click bridge across the skill boundary: rtl-acquire proves a candidate
synthesizes (corpus index.csv status == success); this script turns it into a
ready-to-run ORFS full-flow project under design_cases/ so signoff-loop (or
engineer_loop) can take it RTL->GDS without hand-writing config.mk/SDC.

Per design it:
  1. gates on corpus index.csv status == "success" (optionally also on the
     publish-eligibility CSV via --require-publish-eligible);
  2. reads <corpus>/<design>/design_meta.json — the proven inputs: top,
     rtl_files (post sv2v/vhd2vl fallback), synth_memory_max_bits,
     synth_frontend, top_parameters, synth config.mk path;
  3. creates the project skeleton via signoff-loop init_project.py;
  4. VENDORS the proven RTL into <project>/rtl/ — the synth workspace's
     _tmp_cfg conversions are cleanable scratch, a promoted project must be
     self-contained;
  5. emits constraints/config.mk from signoff-loop assets/config-template.mk:
     carries DESIGN_NAME=top, VERILOG_FILES (vendored, absolute),
     VERILOG_INCLUDE_DIRS, ABC_AREA, SYNTH_MEMORY_MAX_BITS, SYNTH_HDL_FRONTEND,
     VERILOG_TOP_PARAMS; ADDS the floorplan directive (CORE_UTILIZATION) +
     PLACE_DENSITY_LB_ADDON; DROPS R2G_FLOW_SCOPE=synth_only (a promoted
     project is full-flow — the scope marker would misclassify its ingest);
  6. emits constraints/constraint.sdc from assets/constraint-template.sdc with
     a detected clock port (same candidate list the synth stage probes), or a
     virtual clock when the top has no clock port;
  7. runs signoff-loop validate_config.py — the built-in readiness gate (its
     clock-port check catches a wrong SDC guess before ORFS burns a run);
  8. optionally (--run) kicks run_orfs.sh full flow immediately.

The result is recorded in <project>/reports/promote.json and the project's
metadata.json (status: promoted, provenance back to the corpus).

usage:
  promote_candidates.py picorv32_core other_design      # named designs
  promote_candidates.py --all                           # every eligible design
  promote_candidates.py --all --require-publish-eligible --run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skill_env import (  # noqa: E402
    REPO_ROOT,
    default_out_root,
    resolve_str_env,
    run_orfs_script,
    signoff_loop_dir,
)
from common.clock_infer import infer_clock_ports  # noqa: E402

# Same probe list the synth stage's make_minimal_sdc uses (expand_candidates.py)
# — a promoted design's clock detection must agree with what already synthesized.
CLOCK_PORT_CANDIDATES = [
    "clk", "clock", "i_clk", "i_clock", "clock_i", "clk_i",
    "wb_clk_i", "wb_clk", "clock_in", "core_clk", "CK",
]

VIRTUAL_CLOCK_SDC = """current_design {design}

# No clock port detected on the top module ({top}) — combinational or
# self-timed design. A virtual clock still constrains I/O paths so timing
# reports stay meaningful. Replace with a real create_clock if the design
# does have a clock under a non-standard port name.
set clk_name  virtual_clk
set clk_period {period}
create_clock -name $clk_name -period $clk_period
set_input_delay  [expr $clk_period * 0.2] -clock $clk_name [all_inputs]
set_output_delay [expr $clk_period * 0.2] -clock $clk_name [all_outputs]
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_index(out_root: Path) -> dict[str, dict]:
    index_csv = out_root / "index.csv"
    rows: dict[str, dict] = {}
    if index_csv.exists():
        with open(index_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("design"):
                    rows[row["design"]] = row
    return rows


def load_publish_eligible(path: Path) -> set[str]:
    eligible: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            flag = str(row.get("publish_eligible", "")).strip().lower()
            if flag in {"1", "true", "yes"}:
                eligible.add(row.get("design", ""))
    return eligible


def parse_synth_config(config_mk: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not config_mk.is_file():
        return fields
    for line in config_mk.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*(?:export\s+)?(\w+)\s*=\s*(.*)", line)
        if m and not line.strip().startswith("#"):
            fields[m.group(1)] = m.group(2).strip()
    return fields


def detect_clock_port(top: str, rtl_files: list[Path]) -> str:
    """First CLOCK_PORT_CANDIDATES entry that is a port of the top module.
    Scans the top module's header + input declarations; tolerant of both
    ANSI and non-ANSI port styles."""
    header_ports: set[str] = set()
    input_ports: set[str] = set()
    mod_re = re.compile(
        r"(?ms)^\s*module\s+" + re.escape(top) + r"\b[^;]*?\((.*?)\)\s*;")
    for path in rtl_files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = mod_re.search(text)
        if not m:
            continue
        header = re.sub(r"//.*", "", m.group(1))
        for tok in re.split(r"[,\s]+", re.sub(r"\[[^\]]*\]", " ", header)):
            tok = tok.strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", tok or ""):
                header_ports.add(tok)
        # non-ANSI / body declarations inside this module's text region
        endm = text.find("endmodule", m.end())
        body = text[m.start(): endm if endm != -1 else len(text)]
        for dm in re.finditer(
                r"(?m)^\s*input\s+(?:wire\s+|logic\s+|reg\s+)?(?:\[[^\]]*\]\s*)?"
                r"([A-Za-z_][A-Za-z0-9_$]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_$]*)*)", body):
            for name in re.split(r"\s*,\s*", dm.group(1)):
                input_ports.add(name.strip())
        break
    ports = input_ports or header_ports
    for cand in CLOCK_PORT_CANDIDATES:
        if cand in ports:
            return cand
    # Event-control inference (2026-07-16 issue 5): the fixed name list missed
    # every non-standard clock (ethmac's `Clk`). A SINGLE top-body edge-driven
    # input is adopted; an ambiguous (>1) result stays "" — promotion then
    # requires an explicit --clock-port (multi-clock is out of scope anyway).
    inferred = infer_clock_ports(top, [p.read_text(encoding="utf-8", errors="ignore")
                                       for p in rtl_files if p.is_file()])
    if len(inferred) == 1:
        return inferred[0]
    return ""


def vendor_rtl(rtl_files: list[Path], rtl_dir: Path) -> list[Path]:
    """Copy the proven RTL into <project>/rtl/, keeping basenames unique."""
    rtl_dir.mkdir(parents=True, exist_ok=True)
    vendored: list[Path] = []
    used: set[str] = set()
    for src in rtl_files:
        name = src.name
        stem, suffix = os.path.splitext(name)
        n = 1
        while name in used:
            name = f"{stem}_{n}{suffix}"
            n += 1
        used.add(name)
        dst = rtl_dir / name
        shutil.copyfile(src, dst)
        vendored.append(dst)
    return vendored


def render_config_mk(template: str, *, design: str, platform: str,
                     verilog_files: list[Path], sdc_path: Path,
                     core_utilization: int, place_density: float,
                     abc_area: int, extra: dict[str, str]) -> str:
    text = (template
            .replace("{{DESIGN_NAME}}", design)
            .replace("{{PLATFORM}}", platform)
            .replace("{{VERILOG_FILES}}", " ".join(str(p) for p in verilog_files))
            .replace("{{SDC_FILE}}", str(sdc_path))
            .replace("{{CORE_UTILIZATION}}", str(core_utilization))
            .replace("{{PLACE_DENSITY_LB_ADDON}}", f"{place_density:.2f}"))
    text = re.sub(r"(?m)^export ABC_AREA = .*$", f"export ABC_AREA = {abc_area}", text)
    if extra:
        lines = ["", "# --- Promoted from rtl-acquire (proven synth inputs) ---"]
        lines += [f"export {k} = {v}" for k, v in extra.items()]
        text += "\n".join(lines) + "\n"
    return text


def promote_one(design: str, *, out_root: Path, base_dir: Path, args,
                index_row: dict | None) -> dict:
    result: dict = {"design": design, "promoted_at": now_iso(), "status": "failed"}
    meta_path = out_root / design / "design_meta.json"

    status = (index_row or {}).get("status", "")
    if not status and meta_path.is_file():
        try:
            status = str(json.loads(meta_path.read_text(encoding="utf-8")).get("status", ""))
        except Exception:
            status = ""
    if status != "success":
        result["reason"] = f"not eligible: corpus status={status or 'unknown'!r} (need success)"
        return result
    if not meta_path.is_file():
        result["reason"] = f"no design_meta.json under {out_root / design}"
        return result
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    top = str(meta.get("top") or design)
    rtl_files = [Path(os.path.expandvars(os.path.expanduser(p)))
                 for p in meta.get("rtl_files") or []]
    missing = [str(p) for p in rtl_files if not p.is_file()]
    if not rtl_files or missing:
        result["reason"] = f"rtl_files missing on disk: {missing or 'none listed'}"
        return result
    # Byte provenance (2026-07-16 full-pipeline issue 1): the promoted project must
    # vendor the EXACT bytes that earned the synth-only success. rtl_signature is
    # path-based (a dedup key), so nothing else binds them — re-digest each file
    # against the synth-time source_manifest and refuse a candidate whose RTL
    # changed since it was proven. Legacy candidates (pre-manifest expansions)
    # carry no manifest: grandfathered with an explicit unverified stamp.
    manifest = {str(e.get("path")): e.get("sha256")
                for e in (meta.get("source_manifest") or []) if e.get("sha256")}
    if manifest:
        changed = []
        for p in rtl_files:
            want = manifest.get(str(p))
            if want:
                try:
                    got = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError:
                    got = None
                if got != want:
                    changed.append(str(p))
        if changed:
            result["status"] = "rtl_bytes_changed_since_synth"
            result["reason"] = (f"rtl_bytes_changed_since_synth: {len(changed)} file(s) "
                                f"differ from the synth-time source_manifest "
                                f"(e.g. {changed[:2]}); re-expand before promoting")
            return result
        result["source_bytes_verified"] = True
    else:
        result["source_bytes_verified"] = False   # legacy candidate: honest stamp
    synth_cfg = parse_synth_config(Path(str(meta.get("design_config") or "")))
    # Unconstrained-clock gate (2026-07-16 full-pipeline issue 5): a SEQUENTIAL
    # design falling back to a virtual clock has meaningless setup/hold labels
    # downstream (ethmac: 119 unclocked registers, STA-0450, silently promoted).
    # Combinational designs (seq_cells==0) keep the virtual-clock path.
    clock_port = args.clock_port or detect_clock_port(top, rtl_files)
    try:
        seq_cells = int((index_row or {}).get("seq_cells")
                        or (meta.get("seq_cells") if isinstance(meta.get("seq_cells"),
                                                                (int, str)) else 0) or 0)
    except (TypeError, ValueError):
        seq_cells = 0
    if not clock_port and seq_cells > 0 and not args.allow_virtual_clock:
        inferred = infer_clock_ports(
            top, [p.read_text(encoding="utf-8", errors="ignore")
                  for p in rtl_files if p.is_file()])
        result["status"] = "rejected_unconstrained_clock"
        result["reason"] = (
            f"rejected_unconstrained_clock: {seq_cells} sequential cells but no clock "
            f"port resolved (event-control candidates: {inferred or 'none'}); pass "
            f"--clock-port <name> or --allow-virtual-clock for a deliberately "
            f"self-timed design")
        return result

    platform = args.platform or str(meta.get("platform") or
                                    synth_cfg.get("PLATFORM") or "nangate45")
    project = base_dir / design
    if (project / "constraints" / "config.mk").exists() and not args.force:
        result["reason"] = f"{project}/constraints/config.mk exists (use --force to overwrite)"
        return result

    if args.dry_run:
        result.update(status="would_promote", top=top, platform=platform,
                      rtl_file_count=len(rtl_files))
        return result

    # 1. skeleton via the documented signoff-loop entry point
    init_py = signoff_loop_dir() / "scripts" / "project" / "init_project.py"
    subprocess.run([sys.executable, str(init_py), design, str(base_dir)],
                   check=True, capture_output=True)

    # 2. vendor the proven RTL (self-contained project; the synth workspace's
    #    _tmp_cfg conversions are cleanable scratch)
    vendored = vendor_rtl(rtl_files, project / "rtl")

    # 3. config.mk from the signoff-loop template + carried synth knobs
    assets = signoff_loop_dir() / "assets"
    sdc_path = project / "constraints" / "constraint.sdc"
    extra: dict[str, str] = {}
    for key in ("SYNTH_MEMORY_MAX_BITS", "SYNTH_HDL_FRONTEND", "VERILOG_TOP_PARAMS"):
        if synth_cfg.get(key):
            extra[key] = synth_cfg[key]
    if meta.get("synth_memory_max_bits") and "SYNTH_MEMORY_MAX_BITS" not in extra:
        extra["SYNTH_MEMORY_MAX_BITS"] = str(meta["synth_memory_max_bits"])
    if meta.get("synth_frontend") and "SYNTH_HDL_FRONTEND" not in extra:
        extra["SYNTH_HDL_FRONTEND"] = str(meta["synth_frontend"])
    include_dirs = [str((project / "rtl").resolve())]
    for d in (synth_cfg.get("VERILOG_INCLUDE_DIRS") or "").split():
        if d not in include_dirs and Path(d).is_dir():
            include_dirs.append(d)
    extra["VERILOG_INCLUDE_DIRS"] = " ".join(include_dirs)
    # ABC_AREA: same derivation write_project used for the proven synth run
    variant = str(meta.get("synth_variant") or synth_cfg.get("SYNTH_VARIANT") or "")
    abc_area = 1 if variant in {"area", "abc_area1", "yosys_abc_area1"} \
        else int(synth_cfg.get("ABC_AREA", "1") or 1)
    config_text = render_config_mk(
        (assets / "config-template.mk").read_text(encoding="utf-8"),
        design=top, platform=platform, verilog_files=vendored, sdc_path=sdc_path,
        core_utilization=args.core_utilization, place_density=args.place_density,
        abc_area=abc_area, extra=extra)
    (project / "constraints" / "config.mk").write_text(config_text, encoding="utf-8")

    # 4. constraint.sdc: the clock port resolved (and gate-checked) up front —
    # detection ran on rtl_files, whose bytes the source_manifest just verified
    # identical to what vendor_rtl copied.
    if clock_port:
        sdc_text = ((assets / "constraint-template.sdc").read_text(encoding="utf-8")
                    .replace("{{DESIGN_NAME}}", top)
                    .replace("{{CLOCK_PORT}}", clock_port)
                    .replace("{{CLOCK_PERIOD}}", f"{args.clock_period:g}"))
    else:
        sdc_text = VIRTUAL_CLOCK_SDC.format(design=top, top=top,
                                            period=f"{args.clock_period:g}")
    sdc_path.write_text(sdc_text, encoding="utf-8")

    # 5. validate_config.py — the readiness gate (clock-port check included)
    validate_py = signoff_loop_dir() / "scripts" / "project" / "validate_config.py"
    val = subprocess.run([sys.executable, str(validate_py), str(project)],
                         capture_output=True, text=True)
    result.update(
        status="promoted" if val.returncode == 0 else "validate_failed",
        top=top, platform=platform, project=str(project),
        rtl_file_count=len(vendored),
        clock_port=clock_port or "(virtual)",
        validate_rc=val.returncode,
        validate_tail=(val.stdout + val.stderr).strip().splitlines()[-8:],
    )

    # 6. provenance stamps
    def _dump_manifests() -> None:
        meta_out = {"design_name": design, "status": result["status"],
                    "promoted_from": str(out_root / design),
                    "promoted_at": result["promoted_at"],
                    "synth_variant": variant, "top": top, "platform": platform}
        (project / "metadata.json").write_text(json.dumps(meta_out, indent=2),
                                               encoding="utf-8")
        (project / "reports").mkdir(exist_ok=True)
        (project / "reports" / "promote.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

    _dump_manifests()

    # 7. optional immediate full flow
    if args.run and result["status"] == "promoted":
        rc = subprocess.run(["bash", str(run_orfs_script()), str(project),
                             platform, design]).returncode
        result["orfs_rc"] = rc
        if rc != 0:
            result["status"] = "promoted_flow_failed"
        # Re-dump so the ON-DISK manifest reflects the flow outcome, not a stale
        # status='promoted' (failure-patterns.md #38 / codex #2). A later reader
        # of promote.json/metadata.json must not trust a manifest that missed the
        # flow failure.
        _dump_manifests()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("designs", nargs="*", help="corpus design names to promote")
    ap.add_argument("--all", action="store_true",
                    help="promote every corpus design with index status == success")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="corpus root (default: $R2G_ACQUIRE_OUT)")
    ap.add_argument("--base-dir", type=Path, default=None,
                    help="where promoted projects land (default: <repo>/design_cases)")
    ap.add_argument("--platform", default="",
                    help="target ORFS platform (default: the candidate's synth platform)")
    ap.add_argument("--clock-port", default="", help="override clock-port detection")
    ap.add_argument("--allow-virtual-clock", action="store_true",
                    help="promote a SEQUENTIAL design under a virtual clock anyway "
                         "(deliberately self-timed; setup/hold labels will not be "
                         "meaningful — 2026-07-16 issue 5 gate override)")
    ap.add_argument("--clock-period", type=float,
                    default=float(resolve_str_env("R2G_PROMOTE_CLOCK_PERIOD", "10.0")),
                    help="SDC clock period in ns (default 10.0)")
    ap.add_argument("--core-utilization", type=int,
                    default=int(resolve_str_env("R2G_PROMOTE_CORE_UTILIZATION", "30")))
    ap.add_argument("--place-density", type=float, default=0.20,
                    help="PLACE_DENSITY_LB_ADDON (Hard Rule: never below 0.10)")
    ap.add_argument("--require-publish-eligible", action="store_true",
                    help="additionally gate on the publish-eligibility CSV")
    ap.add_argument("--publish-eligible-csv", type=Path, default=None)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing promoted project's constraints")
    ap.add_argument("--run", action="store_true",
                    help="kick run_orfs.sh full flow after a successful promote")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.place_density < 0.10:
        ap.error("--place-density below 0.10 violates the placer Hard Rule")
    out_root = args.out_root or default_out_root()
    base_dir = args.base_dir or (REPO_ROOT / "design_cases")
    index = load_index(out_root)

    if args.all:
        names = sorted(d for d, row in index.items() if row.get("status") == "success")
    else:
        names = args.designs
    if not names:
        print("nothing to promote (no designs given and --all found no "
              f"status==success rows in {out_root / 'index.csv'})", file=sys.stderr)
        return 1

    if args.require_publish_eligible:
        csv_path = args.publish_eligible_csv or (out_root / "publish_eligible_designs.csv")
        if not csv_path.is_file():
            print(f"ERROR: --require-publish-eligible but {csv_path} not found "
                  "(run publish/build_publish_candidates.py first, or pass "
                  "--publish-eligible-csv)", file=sys.stderr)
            return 1
        eligible = load_publish_eligible(csv_path)
        skipped = [n for n in names if n not in eligible]
        names = [n for n in names if n in eligible]
        for n in skipped:
            print(f"  SKIP {n}: not publish-eligible per {csv_path.name}")

    results = []
    for design in names:
        res = promote_one(design, out_root=out_root, base_dir=base_dir,
                          args=args, index_row=index.get(design))
        results.append(res)
        tag = res["status"].upper()
        print(f"  {tag:22s} {design}"
              + (f" -> {res.get('project')}" if res.get("project") else "")
              + (f" [{res.get('reason')}]" if res.get("reason") else ""))

    ok = sum(1 for r in results if r["status"] in ("promoted", "would_promote"))
    print(f"promoted {ok}/{len(results)} design(s) into {base_dir}")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
