#!/usr/bin/env python3
"""Normalize proven Sky130 LVS representation differences, with a receipt.

This does not alter design connectivity.  It reconciles three standard-cell
library/extractor conventions before Netgen compares the circuits:

* Magic emits Sky130 MOS devices as four-terminal ``X`` subcircuit calls while
  the SkyWater CDL uses ``M`` devices and short model names.  The former become
  equivalent four-terminal ``M`` records and the latter are qualified with the
  canonical ``sky130_fd_pr__`` model prefix.
* Magic's ``special_nfet_01v8`` extraction class is the ordinary 1.8 V NFET
  used by the transistor-level cell SPICE.
* Magic emits diode area/perimeter in base pico/micro units without suffixes.
* SkyWater ``conb`` SPICE variants use two- or three-terminal ``short``
  proxies for the two-terminal poly geometry extracted by Magic.

The original GDS, powered Verilog, extracted SPICE digest, and cell-library
digest remain recorded by ``run_netgen_lvs.sh``.  This helper additionally
records exact transform counts and before/after digests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SPECIAL_NFET = "sky130_fd_pr__special_nfet_01v8"
REGULAR_NFET = "sky130_fd_pr__nfet_01v8"
DIODE_BASE = "sky130_fd_pr__diode_pw2nd"
DIODE_MAGIC = "sky130_fd_pr__diode_pw2nd_05v5"
MOS_MODEL_MAP = {
    "nfet_01v8": "sky130_fd_pr__nfet_01v8",
    "nfet_01v8_lvt": "sky130_fd_pr__nfet_01v8_lvt",
    "pfet_01v8": "sky130_fd_pr__pfet_01v8",
    "pfet_01v8_hvt": "sky130_fd_pr__pfet_01v8_hvt",
    "pfet_01v8_lvt": "sky130_fd_pr__pfet_01v8_lvt",
    "pfet_01v8_mvt": "sky130_fd_pr__pfet_01v8_mvt",
}
FULL_MOS_MODELS = tuple(MOS_MODEL_MAP.values())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def normalize_layout(text: str) -> tuple[str, dict[str, int]]:
    """Normalize an already X->D-converted Magic extraction."""
    text, special = re.subn(rf"\b{re.escape(SPECIAL_NFET)}\b", REGULAR_NFET, text)
    mos_model_pattern = "|".join(re.escape(model) for model in FULL_MOS_MODELS)
    layout_mos = re.compile(
        rf"^[Xx](\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+"
        rf"(?:{mos_model_pattern})(?=\s|$))",
        re.MULTILINE,
    )
    # Magic's X record and the CDL's M record both carry exactly D/G/S/B plus
    # the model and properties.  Changing only the record designator prevents
    # Netgen from inventing placeholder/proxy pins for an undefined subcircuit;
    # no node, model, or property is added, removed, or reordered.
    text, mos_x_to_m = layout_mos.subn(r"M\1", text)
    unit_counts = {"diode_pj_unit": 0, "diode_area_unit": 0}
    diode_line = re.compile(
        rf"^(D\S+\s+\S+\s+\S+\s+{re.escape(DIODE_MAGIC)}\s+)(.*)$",
        re.MULTILINE,
    )

    def fix_units(match: re.Match[str]) -> str:
        props = match.group(2)

        def add_unit(prop_match: re.Match[str]) -> str:
            name, value = prop_match.groups()
            if not re.fullmatch(r"[0-9.eE+-]+", value):
                return prop_match.group(0)
            suffix = "u" if name == "pj" else "p"
            unit_counts[f"diode_{name}_unit"] += 1
            return f"{name}={value}{suffix}"

        props = re.sub(r"\b(pj|area)=([^\s]+)", add_unit, props)
        return match.group(1) + props

    text, diode_lines = diode_line.subn(fix_units, text)
    return text, {
        "special_nfet_to_nfet": special,
        "layout_mos_x_to_m": mos_x_to_m,
        "diode_lines_checked": diode_lines,
        **unit_counts,
    }


def normalize_library(text: str) -> tuple[str, dict[str, int]]:
    """Normalize SkyWater cell SPICE to Magic's primitive representation."""
    short_model_pattern = "|".join(re.escape(model) for model in MOS_MODEL_MAP)
    library_mos = re.compile(
        rf"^(M\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+)"
        rf"({short_model_pattern})(?=\s|$)",
        re.MULTILINE | re.IGNORECASE,
    )

    def qualify_mos_model(match: re.Match[str]) -> str:
        return match.group(1) + MOS_MODEL_MAP[match.group(2).lower()]

    text, qualified_mos = library_mos.subn(qualify_mos_model, text)
    short_re = re.compile(
        r"^(X\S+)\s+(\S+)\s+(\S+)\s+\S+\s+short\s+(.*)$", re.MULTILINE
    )
    text, short_count = short_re.subn(
        rf"\1 \2 \3 {REGULAR_NFET.replace('nfet_01v8', 'res_generic_po')} \4",
        text,
    )

    # The HS conb CDL uses ``rI12 VGND LO short`` while Magic extracts the
    # actual two-terminal poly geometry.  Scope this conversion to the known
    # HS constant cell: a generic ``short`` elsewhere must not silently become
    # a physical resistor model.  The dimensions are the fixed conb geometry
    # observed in the official cell layout/CDL pair.
    conb_block_re = re.compile(
        r"^\.subckt\s+sky130_fd_sc_hs__conb_1\b.*?^\.ends(?:\s+\S+)?\s*$",
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    two_terminal_short_count = 0

    def fix_hs_conb(block_match: re.Match[str]) -> str:
        nonlocal two_terminal_short_count
        block = block_match.group(0)
        resistor_re = re.compile(
            r"^[Rr](\S+)\s+(\S+)\s+(\S+)\s+short\s*$", re.MULTILINE
        )

        def replace_resistor(match: re.Match[str]) -> str:
            nonlocal two_terminal_short_count
            two_terminal_short_count += 1
            return (
                f"X{match.group(1)} {match.group(2)} {match.group(3)} "
                "sky130_fd_pr__res_generic_po w=0.51 l=0.045"
            )

        return resistor_re.sub(replace_resistor, block)

    text = conb_block_re.sub(fix_hs_conb, text)
    short_count += two_terminal_short_count
    diode_re = re.compile(
        rf"^[Xx](\S*)\s+(\S+)\s+(\S+)\s+{re.escape(DIODE_BASE)}\s*(.*)$",
        re.MULTILINE,
    )

    def fix_diode(match: re.Match[str]) -> str:
        props = re.sub(r"\bp=", "pj=", match.group(4))
        props = re.sub(r"\ba=", "area=", props)
        return (
            f"D{match.group(1) or '0'} {match.group(2)} {match.group(3)} "
            f"{DIODE_MAGIC} {props}"
        ).rstrip()

    text, diode_count = diode_re.subn(fix_diode, text)
    return text, {
        "library_mos_model_qualified": qualified_mos,
        "short_to_poly_resistor": short_count,
        "two_terminal_conb_short_to_poly_resistor": two_terminal_short_count,
        "library_diode_x_to_d": diode_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("layout", "library"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    source_text = args.source.read_text()
    if args.mode == "layout":
        output, counts = normalize_layout(source_text)
        destination = args.destination or args.source
    else:
        if args.destination is None:
            parser.error("library mode requires destination")
        output, counts = normalize_library(source_text)
        destination = args.destination

    _atomic_write(destination, output)
    receipt = {
        "schema": "r2g.sky130_lvs_normalization.v1",
        "mode": args.mode,
        "source": str(args.source.resolve()),
        "destination": str(destination.resolve()),
        "source_sha256": _sha(source_text),
        "normalized_sha256": _sha(output),
        "transforms": counts,
    }
    if args.receipt:
        _atomic_write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
