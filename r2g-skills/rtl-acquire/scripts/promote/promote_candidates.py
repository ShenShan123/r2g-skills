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
    default_downloads_root,
    default_out_root,
    resolve_str_env,
    run_orfs_script,
    signoff_loop_dir,
)
from common.clock_infer import infer_clock_ports  # noqa: E402
from common.rtl_readiness import (  # noqa: E402
    assess as assess_rtl_readiness,
    blocks_promotion,
    find_status_root,
)


def _candidate_repo_dir(design_dir: Path, rtl_files: list[Path]) -> Path | None:
    """The upstream project root a candidate came from, for status-file
    inspection: the clone directly under the downloads root when there is one,
    else the nearest ancestor of the RTL carrying a status file (a `local_tree`
    candidate has no downloads-root ancestor at all), else the corpus design dir
    (which vendors the sources and any README copied with them)."""
    try:
        droot = default_downloads_root().resolve()
    except Exception:
        droot = None
    for f in rtl_files:
        try:
            p = Path(f).resolve()
        except OSError:
            continue
        if droot is not None:
            repo = next((anc for anc in p.parents if anc.parent == droot), None)
            if repo is not None:
                return repo
        found = find_status_root(p)
        if found is not None:
            return found
    return design_dir if design_dir.is_dir() else None


def _read_synth_log(design_dir: Path) -> str | None:
    """The candidate's synthesis log — the authoritative structural evidence
    (selected by stage outcome; see expand_candidates.synth_log_from)."""
    p = design_dir / "synth.log"
    try:
        return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else None
    except OSError:
        return None

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


def _readable_file(p: Path) -> bool:
    """`p.is_file()` that treats an unreadable mount as absent, not fatal.

    P1-N6 (failure-patterns.md #52): Path.is_file() swallows only ENOENT,
    ENOTDIR, EBADF and ELOOP — EACCES propagates. A corpus relocated from another
    user's home therefore raised an uncaught PermissionError out of promote_one,
    and because main() loops over candidates without a guard, ONE unreadable
    entry aborted an entire `--all` campaign.
    """
    try:
        return p.is_file()
    except OSError:
        return False


def resolve_candidate_rtl(rtl_files: list[str], candidate_dir: Path) -> list[dict]:
    """Locate each synth-proven RTL file, preferring the corpus's OWN copy.

    P1-N6: candidate metadata records acquisition-time ABSOLUTE paths and treats
    them as authoritative forever. Every one of the 708 `design_meta.json` in
    this checkout points at `/home/yuany/...`, which does not exist here — while
    every one of those 708 candidates has a complete local `rtl/` beside it. A
    self-contained corpus was unusable purely because it remembered where its
    bytes used to live.

    Resolution order per file:
      1. the recorded path, if readable (unrelocated corpus — unchanged behavior);
      2. `<candidate_dir>/rtl/<basename>` — the flat vendored layout;
      3. the LONGEST tail of the recorded path that exists under `rtl/` — some
         candidates vendor a nested tree (`rtl/peripherals_part/pkt_part/X.sv`),
         and matching the longest suffix picks the right one when several files
         share a basename;
      4. a unique recursive basename match, as a last resort. AMBIGUOUS matches
         (same basename in more than one subdirectory) are deliberately left
         unresolved rather than guessed — picking one at random is exactly the
         silent-wrong-value failure this skill exists to avoid.

    The ORIGINAL path string is preserved as `key` because that is what the
    source_manifest is keyed on — resolution changes where bytes are READ from,
    never which digest they are checked against. Relocation must not become a
    way to launder a byte change.
    """
    vendored_dir = candidate_dir / "rtl"
    out: list[dict] = []
    for raw in rtl_files:
        recorded = Path(os.path.expandvars(os.path.expanduser(raw)))
        entry = {"key": raw, "recorded": recorded, "path": None, "source": "missing"}
        if _readable_file(recorded):
            entry.update(path=recorded, source="recorded")
            out.append(entry)
            continue

        local = vendored_dir / recorded.name
        if _readable_file(local):
            entry.update(path=local, source="vendored")
            out.append(entry)
            continue

        parts = recorded.parts
        hit = None
        for k in range(min(len(parts), 8), 0, -1):        # longest tail first
            cand = vendored_dir.joinpath(*parts[-k:])
            if _readable_file(cand):
                hit = cand
                break
        if hit is None:
            try:
                matches = [p for p in vendored_dir.rglob(recorded.name)
                           if _readable_file(p)]
            except OSError:
                matches = []
            if len(matches) == 1:
                hit = matches[0]
            elif len(matches) > 1:
                entry["source"] = "ambiguous"
                entry["candidates"] = [str(p) for p in matches[:4]]
        if hit is not None:
            entry.update(path=hit, source="vendored")
        out.append(entry)
    return out


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


def vendor_headers(header_manifest: list[dict], candidate_dir: Path,
                   rtl_dir: Path) -> tuple[list[Path], list[str]]:
    """Copy the frozen header closure into <project>/rtl/ so the project is
    self-contained. Returns (vendored, unresolved_keys).

    P0-R5: `vendor_rtl` copied `rtl_files` only, and promotion appended the synth
    project's EXTERNAL VERILOG_INCLUDE_DIRS to the promoted config — so a header
    living outside the corpus could change after the synth proof and silently
    alter the elaborated circuit. Headers keep their EXACT basename (an `include`
    resolves by name, so the uniquifying rename vendor_rtl applies would break
    them); a genuine basename collision is reported as unresolved rather than
    silently resolved to whichever copy happened to be written last.
    """
    rtl_dir.mkdir(parents=True, exist_ok=True)
    vendored: list[Path] = []
    unresolved: list[str] = []
    placed: dict[str, Path] = {}
    for entry in header_manifest or []:
        key = str(entry.get("path") or "")
        if not key:
            continue
        src = Path(os.path.expandvars(os.path.expanduser(key)))
        if not _readable_file(src):
            src = candidate_dir / "rtl" / Path(key).name       # relocated corpus
        if not _readable_file(src):
            unresolved.append(key)
            continue
        name = Path(key).name
        prior = placed.get(name)
        if prior is not None:
            try:
                if prior.read_bytes() != src.read_bytes():
                    unresolved.append(f"{key} (basename collision with {prior})")
            except OSError:
                unresolved.append(key)
            continue
        dst = rtl_dir / name
        try:
            shutil.copyfile(src, dst)
        except OSError:
            unresolved.append(key)
            continue
        placed[name] = dst
        vendored.append(dst)
    return vendored, unresolved


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
    # Resolve against the corpus's own vendored rtl/ when the recorded
    # acquisition-time absolute path is gone or unreadable (P1-N6). The manifest
    # key stays the RECORDED path — relocation changes where bytes are read
    # from, never which digest they are verified against.
    resolved = resolve_candidate_rtl(list(meta.get("rtl_files") or []),
                                     out_root / design)
    rtl_files = [e["path"] for e in resolved if e["path"] is not None]
    missing = [e["key"] for e in resolved if e["path"] is None]
    if not resolved or missing:
        result["status"] = "rtl_files_unresolved"
        result["reason"] = (f"rtl_files could not be resolved from the recorded path "
                            f"or the corpus's vendored rtl/: {missing or 'none listed'}")
        return result
    relocated = [e["key"] for e in resolved if e["source"] == "vendored"]
    if relocated:
        result["source_relocated"] = len(relocated)
        print(f"NOTE: {design}: {len(relocated)} of {len(resolved)} rtl_files resolved "
              f"from the corpus's vendored rtl/ (recorded paths unreachable — "
              f"relocated corpus)", file=sys.stderr)
    # Byte provenance (2026-07-16 full-pipeline issue 1): the promoted project must
    # vendor the EXACT bytes that earned the synth-only success. rtl_signature is
    # path-based (a dedup key), so nothing else binds them — re-digest each file
    # against the synth-time source_manifest and refuse a candidate whose RTL
    # changed since it was proven. Legacy candidates (pre-manifest expansions)
    # carry no manifest: grandfathered with an explicit unverified stamp.
    manifest = {str(e.get("path")): e.get("sha256")
                for e in (meta.get("source_manifest") or []) if e.get("sha256")}
    if manifest:
        # COVERAGE first (2026-07-19 audit P0-R4, failure-patterns #52): the loop
        # below used to digest only files the manifest happened to mention and
        # then stamp source_bytes_verified=True regardless. A real 5-file
        # eth_rxethmac candidate supplied with a 1-file manifest therefore
        # promoted with source_bytes_verified=true and rtl_file_count=5 — four
        # files free to change while the result positively claimed full
        # verification. A partial proof is not a proof: require an entry for
        # EVERY rtl_file before any per-file comparison is meaningful.
        uncovered = [e["key"] for e in resolved if e["key"] not in manifest]
        if uncovered:
            result["status"] = "source_manifest_incomplete"
            result["reason"] = (
                f"source_manifest_incomplete: {len(uncovered)} of {len(resolved)} "
                f"rtl_files have no manifest digest (e.g. {uncovered[:2]}); "
                f"re-expand to regenerate a complete manifest before promoting")
            return result
        changed = []
        for e in resolved:
            want = manifest[e["key"]]
            try:
                got = hashlib.sha256(e["path"].read_bytes()).hexdigest()
            except OSError:
                got = None
            if got != want:
                changed.append(e["key"])
        if changed:
            result["status"] = "rtl_bytes_changed_since_synth"
            result["reason"] = (f"rtl_bytes_changed_since_synth: {len(changed)} file(s) "
                                f"differ from the synth-time source_manifest "
                                f"(e.g. {changed[:2]}); re-expand before promoting")
            return result
        result["source_bytes_verified"] = True
    else:
        # A legacy candidate carries no manifest, so its synth-proven bytes cannot
        # be reconstructed at all. Recording that honestly is not the same as
        # ENFORCING it (2026-07-19 audit P0-R6): promotion used to continue
        # straight into project creation and vendoring with the false stamp, so an
        # automatic campaign could publish a design whose provenance is
        # unknowable. Block by default and offer a logged operator override, which
        # is exactly how the license gate already fails closed on legacy
        # 'unknown' candidates.
        result["source_bytes_verified"] = False   # legacy candidate: honest stamp
        if not getattr(args, "allow_unverified_source", False):
            result["status"] = "source_manifest_missing"
            result["reason"] = (
                "source_manifest_missing: legacy candidate has no synth-time "
                "source_manifest, so its proven bytes cannot be reconstructed; "
                "re-expand it, or pass --allow-unverified-source to promote "
                "anyway (the unverified stamp is kept in every downstream manifest)")
            return result
        result["source_verification_override"] = "operator:--allow-unverified-source"
        print(f"WARNING: {design}: promoting with UNVERIFIED source bytes "
              f"(no synth-time manifest) per --allow-unverified-source; "
              f"source_bytes_verified=false is retained downstream", file=sys.stderr)
    # --- Semantic RTL readiness (RMD-HO-P0-01, failure-patterns #59) --------
    # Byte provenance proves WHICH RTL we have, never whether that RTL is a
    # finished design. secworks_sha3 states "Not completed. Does not work. Do.
    # Not. Use." in its own README and synthesized with substantial undriven
    # internal logic — and promoted anyway, burning physical-design resources
    # until detailed routing reported 8,726 violations. Routing blocked
    # publication that time, but physical cleanliness is not a functional-
    # correctness proof: a structurally incomplete design that DID route clean
    # would have entered the graph corpus as training data.
    #
    # The verdict combines an explicit repository status declaration with
    # structural synthesis evidence — neither alone rejects, because README
    # keywords appear in historical notes and a few undriven wires are routine.
    readiness = assess_rtl_readiness(
        repo_dir=_candidate_repo_dir(out_root / design, rtl_files),
        synth_log=_read_synth_log(out_root / design),
        declared_dependencies=set(meta.get("declared_dependencies") or []))
    result["rtl_readiness"] = readiness["rtl_readiness"]
    result["rtl_readiness_evidence"] = readiness
    if blocks_promotion(readiness) and not getattr(args, "allow_unready_rtl", False):
        result["status"] = readiness["rtl_readiness"]
        result["reason"] = (
            f"{readiness['rtl_readiness']}: {readiness['reason']}. Pass "
            f"--allow-unready-rtl to promote anyway (the verdict is retained in "
            f"every downstream manifest and blocks strict_clean publication)")
        return result
    if blocks_promotion(readiness):
        result["rtl_readiness_override"] = "operator:--allow-unready-rtl"
        print(f"WARNING: {design}: promoting despite rtl_readiness="
              f"{readiness['rtl_readiness']} per --allow-unready-rtl; the verdict "
              f"is retained downstream", file=sys.stderr)

    synth_cfg = parse_synth_config(Path(str(meta.get("design_config") or "")))
    # --- Frozen compilation inputs (P0-N2, failure-patterns.md #52) ---------
    # RTL bytes were never the whole compilation input. Top parameters, defines,
    # the frontend and the include ORDER decide what gets elaborated, and all of
    # them were re-read from the synth project's MUTABLE config.mk at this point.
    # The audit qualified a design at `WIDTH 8`, edited config.mk to `WIDTH 16`
    # without touching an RTL byte, and promotion carried WIDTH=16 into the full
    # flow while reporting source_bytes_verified=true.
    #
    # When a candidate carries a frozen compile_manifest, it WINS over the live
    # config.mk, and a disagreement blocks: the live file is round state, the
    # manifest is the proof. Legacy candidates without one keep the old
    # config.mk path — they already require --allow-unverified-source above, so
    # this adds no new hole.
    compile_man = meta.get("compile_manifest") if isinstance(
        meta.get("compile_manifest"), dict) else None
    if compile_man:
        drift = []
        for cfg_key, man_key in (("VERILOG_TOP_PARAMS", "top_parameters"),
                                 ("SYNTH_HDL_FRONTEND", "synth_frontend"),
                                 ("SYNTH_MEMORY_MAX_BITS", "synth_memory_max_bits")):
            live = synth_cfg.get(cfg_key)
            if live is None:
                continue
            frozen = compile_man.get(man_key)
            if man_key == "top_parameters":
                # config.mk renders params as `NAME VALUE` pairs; compare as a map.
                toks = str(live).split()
                live_map = {toks[i]: toks[i + 1] for i in range(0, len(toks) - 1, 2)}
                if live_map != {str(k): str(v) for k, v in (frozen or {}).items()}:
                    drift.append(f"{cfg_key}: config.mk={live_map} != proven={frozen}")
            elif frozen is not None and str(live) != str(frozen):
                drift.append(f"{cfg_key}: config.mk={live!r} != proven={frozen!r}")
        if drift:
            result["status"] = "compile_inputs_changed_since_synth"
            result["reason"] = (
                "compile_inputs_changed_since_synth: the synth project's config.mk no "
                "longer matches the frozen compilation inputs that earned the proof "
                f"({'; '.join(drift)}); re-expand before promoting")
            return result
        result["compile_inputs_verified"] = True
        result["compile_config_digest"] = compile_man.get("config_digest")
    else:
        result["compile_inputs_verified"] = False
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
    # Vendor the frozen header closure too (P0-R5): a promoted project that still
    # reads headers from an external tree can elaborate a different circuit the
    # moment that tree changes, and does not survive being moved or archived.
    header_manifest = (compile_man or {}).get("header_manifest") or []
    vendored_headers, unresolved_headers = vendor_headers(
        header_manifest, out_root / design, project / "rtl")
    if unresolved_headers:
        result["status"] = "header_closure_unresolved"
        result["reason"] = (
            f"header_closure_unresolved: {len(unresolved_headers)} synth-proven "
            f"header(s) could not be vendored (e.g. {unresolved_headers[:2]}); the "
            f"promoted project would depend on an external tree — re-expand")
        return result
    if vendored_headers:
        result["vendored_header_count"] = len(vendored_headers)

    # 3. config.mk from the signoff-loop template + carried synth knobs
    assets = signoff_loop_dir() / "assets"
    sdc_path = project / "constraints" / "constraint.sdc"
    extra: dict[str, str] = {}
    # Compilation knobs come from the FROZEN manifest when there is one (P0-N2);
    # the live config.mk is only the legacy fallback (and has already been checked
    # for drift against the manifest above).
    if compile_man:
        if compile_man.get("top_parameters"):
            extra["VERILOG_TOP_PARAMS"] = " ".join(
                f"{k} {v}" for k, v in sorted(compile_man["top_parameters"].items()))
        if compile_man.get("synth_memory_max_bits"):
            extra["SYNTH_MEMORY_MAX_BITS"] = str(compile_man["synth_memory_max_bits"])
        if compile_man.get("synth_frontend"):
            extra["SYNTH_HDL_FRONTEND"] = str(compile_man["synth_frontend"])
    else:
        for key in ("SYNTH_MEMORY_MAX_BITS", "SYNTH_HDL_FRONTEND", "VERILOG_TOP_PARAMS"):
            if synth_cfg.get(key):
                extra[key] = synth_cfg[key]
    if meta.get("synth_memory_max_bits") and "SYNTH_MEMORY_MAX_BITS" not in extra:
        extra["SYNTH_MEMORY_MAX_BITS"] = str(meta["synth_memory_max_bits"])
    if meta.get("synth_frontend") and "SYNTH_HDL_FRONTEND" not in extra:
        extra["SYNTH_HDL_FRONTEND"] = str(meta["synth_frontend"])
    # SELF-CONTAINED include path (P0-R5): the vendored rtl/ only. Carrying the
    # synth-time EXTERNAL dirs made the promoted project depend on a tree that can
    # change under it — the audit's acceptance test is that the project must
    # synthesize with external source access disabled. A candidate whose headers
    # could not be vendored has already been rejected above, so nothing is lost.
    include_dirs = [str((project / "rtl").resolve())]
    if not compile_man:
        # Legacy candidate (no frozen header closure to vendor): keep the old
        # behavior rather than break a promotion that used to work, and record
        # that the project is NOT self-contained.
        for d in (synth_cfg.get("VERILOG_INCLUDE_DIRS") or "").split():
            if d not in include_dirs and Path(d).is_dir():
                include_dirs.append(d)
        if len(include_dirs) > 1:
            result["external_include_dirs"] = include_dirs[1:]
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
        # source_bytes_verified must ride the PROJECT manifest, not just
        # reports/promote.json (2026-07-19 audit P0-R6, failure-patterns #52):
        # metadata.json is what downstream readers open, so omitting the stamp
        # left them with no contract at all — an unverified project was
        # indistinguishable from a verified one. Carry the override too, so a
        # deliberately-unverified promotion is self-describing.
        meta_out = {"design_name": design, "status": result["status"],
                    "promoted_from": str(out_root / design),
                    "promoted_at": result["promoted_at"],
                    "synth_variant": variant, "top": top, "platform": platform,
                    "source_bytes_verified": bool(result.get("source_bytes_verified")),
                    # RMD-HO-P0-01: the readiness verdict must ride the manifest
                    # downstream readers open, exactly like source_bytes_verified —
                    # a project promoted under an override must be distinguishable
                    # from one that passed the gate. The publish gate keys on this.
                    "rtl_readiness": result.get("rtl_readiness")}
        if result.get("rtl_readiness_override"):
            meta_out["rtl_readiness_override"] = result["rtl_readiness_override"]
        if result.get("source_verification_override"):
            meta_out["source_verification_override"] = \
                result["source_verification_override"]
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
    ap.add_argument("--allow-unready-rtl", action="store_true",
                    help="promote despite an rtl_readiness verdict of "
                         "manual_review / rejected_semantic_incomplete "
                         "(RMD-HO-P0-01 gate override; the verdict is retained in "
                         "every downstream manifest and blocks strict_clean publication)")
    ap.add_argument("--allow-unverified-source", action="store_true",
                    help="promote a legacy candidate that has no synth-time "
                         "source_manifest (its proven bytes cannot be "
                         "reconstructed); source_bytes_verified=false is kept in "
                         "every downstream manifest. Operator recovery only.")
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
        # One unpromotable candidate must never abort the campaign (P1-N6): an
        # unreadable relocated source used to raise PermissionError straight out
        # of promote_one, killing an entire `--all` run. Every failure is a
        # structured per-candidate outcome, exactly like the gate rejections.
        try:
            res = promote_one(design, out_root=out_root, base_dir=base_dir,
                              args=args, index_row=index.get(design))
        except Exception as e:  # noqa: BLE001
            res = {"design": design, "promoted_at": now_iso(), "status": "failed",
                   "reason": f"unhandled promotion error: {type(e).__name__}: {e}"}
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
