#!/usr/bin/env python3
"""Win 5 (5a) — PRE-ROUTE feature extractor.

Emits a feature vector available at SUGGESTION time — from the synthesized netlist
+ spec, BEFORE place-and-route — so suggest_config can retrieve nearest prior clean
runs by topology instead of by source-repo name prefix (infer_family). This is the
predictive counterpart to features/metadata.py, which is descriptive: metadata.py
reads the POST-route 6_final.def (instance/area/etc. that only exist once a design
has already closed), so it can never seed a pre-route config. The fields here are
all derivable before PnR:

  instance_count      synth cell count (Yosys "Number of cells")
  primary_io          top-module port count (synth netlist), else None
  est_logic_depth     coarse proxy ceil(log2(instance_count)) — a rough bound on
                      combinational depth for a balanced netlist; NOT lifted from a
                      routed DEF. Documented as an estimate.
  target_utilization  CORE_UTILIZATION from config.mk
  clock_period_ns     CLOCK_PERIOD from config.mk / constraint.sdc
  routing_layers      MAX_ROUTING_LAYER from config, else a platform default

Missing inputs -> None for that field (the retrieval imputes corpus means), so the
extractor never raises on a partial project.

Usage: presynth.py <project-dir> [output.json]   (default: reports/presynth_features.json)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

FEATURE_KEYS = ("instance_count", "primary_io", "est_logic_depth",
                "target_utilization", "clock_period_ns", "routing_layers")

# Conservative per-platform default top routing layer (used only when the config
# does not pin MAX_ROUTING_LAYER). sky130 has 5 usable signal layers; nangate45 10.
_PLATFORM_DEFAULT_LAYERS = {
    "sky130hd": 5, "sky130hs": 5, "nangate45": 10, "asap7": 7,
    "gf180": 5, "ihp-sg13g2": 5,
}


def _parse_config_mk(path: Path) -> dict:
    fields: dict[str, str] = {}
    if not path.exists():
        return fields
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?(\w+)\s*=\s*(.*)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _cell_count(synth_dir: Path) -> int | None:
    log = synth_dir / "synth.log"
    if not log.exists():
        return None
    text = log.read_text(encoding="utf-8", errors="ignore")
    last = None
    for m in re.finditer(r"Number of cells:\s+(\d+)", text):
        last = int(m.group(1))
    return last


_PORT_RE = re.compile(r"\b(?:input|output|inout)\b", re.MULTILINE)


def _primary_io(project: Path) -> int | None:
    """Best-effort top-module port count from a synthesized netlist. Scans the
    first synth/output Verilog found; None when no netlist is available."""
    candidates: list[Path] = []
    for sub in ("synth", "results"):
        d = project / sub
        if d.is_dir():
            candidates += sorted(d.rglob("*.v"))
    for nl in candidates:
        try:
            text = nl.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        n = len(_PORT_RE.findall(text))
        if n:
            return n
    return None


def _clock_period(project: Path, cfg: dict) -> float | None:
    if cfg.get("CLOCK_PERIOD"):
        try:
            return float(cfg["CLOCK_PERIOD"])
        except ValueError:
            pass
    sdc = project / "constraints" / "constraint.sdc"
    if sdc.exists():
        m = re.search(r"set\s+clk_period\s+([\d.]+)", sdc.read_text(errors="ignore"))
        if m:
            return float(m.group(1))
        m = re.search(r"create_clock[^\n]*-period\s+([\d.]+)",
                      sdc.read_text(errors="ignore"))
        if m:
            return float(m.group(1))
    return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_presynth_features(project: Path) -> dict:
    project = Path(project)
    cfg = _parse_config_mk(project / "constraints" / "config.mk")
    platform = cfg.get("PLATFORM", "asap7")
    cells = _cell_count(project / "synth")
    io = _primary_io(project)
    layers = cfg.get("MAX_ROUTING_LAYER")
    routing_layers = (int(layers) if layers and layers.isdigit()
                      else _PLATFORM_DEFAULT_LAYERS.get(platform))
    est_depth = int(math.ceil(math.log2(cells))) if cells and cells > 1 else None
    return {
        "design_name": cfg.get("DESIGN_NAME", "unknown"),
        "platform": platform,
        "instance_count": cells,
        "primary_io": io,
        "est_logic_depth": est_depth,
        "target_utilization": _to_float(cfg.get("CORE_UTILIZATION")),
        "clock_period_ns": _clock_period(project, cfg),
        "routing_layers": routing_layers,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("output_file", nargs="?", default=None,
                    help="default: <project>/reports/presynth_features.json")
    args = ap.parse_args(argv)
    feats = extract_presynth_features(args.project)
    out = (Path(args.output_file) if args.output_file
           else args.project / "reports" / "presynth_features.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(feats, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}: " + ", ".join(
        f"{k}={feats.get(k)}" for k in FEATURE_KEYS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
