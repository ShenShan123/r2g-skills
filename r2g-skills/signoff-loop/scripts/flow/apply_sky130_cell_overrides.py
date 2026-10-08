#!/usr/bin/env python3
"""Replace the CDL definition of listed sky130 standard cells with their open_pdks SPICE definition.

For a few multi-finger cells the SkyWater CDL describes a different transistor topology
than the cell layout: a shared-node ``m=2`` series stack where the layout (and the
open_pdks transistor-level SPICE, which is extracted from it) has two split stacks. Netgen
merges parallel devices but not split series stacks, so every design instantiating such a
cell fails LVS "Top level cell failed pin matching" although the layout is correct. The
cell list and its evidence are in ``sky130_cdl_topology_overrides.json``; see
references/failure-patterns.md, "sky130 LVS" (CDL topology override).

Operates on the already library-normalised CDL (in place). Each replacement definition is
normalised with the flow's layout-side normaliser (``X`` MOS calls -> ``M`` records,
``special_nfet`` -> ``nfet``), the same treatment Magic's extraction receives. Fails closed:
a listed cell missing from either netlist keeps its CDL definition and is reported. Writes a
receipt with before/after digests per cell.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_sky130_lvs_spice import normalize_layout  # noqa: E402

DEFAULT_LIST = Path(__file__).resolve().parent / "sky130_cdl_topology_overrides.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _join_continuations(text: str) -> str:
    """SPICE continuation lines ('+ ...') joined onto their record, so one block = whole records."""
    return re.sub(r"\n\+", " ", text)


def subckt_blocks(text: str) -> dict[str, tuple[int, int]]:
    """{cell name: (start, end)} of every .subckt ... .ends block (case-insensitive keywords)."""
    blocks = {}
    for m in re.finditer(r"(?im)^\.subckt[ \t]+(\S+)[^\n]*\n.*?^\.ends\b[^\n]*(?:\n|$)", text, re.S):
        blocks[m.group(1)] = (m.start(), m.end())
    return blocks


def apply_overrides(cdl_text: str, pdk_text: str, cells: list[str]) -> tuple[str, dict]:
    """Return (new CDL text, receipt fields). Only the listed cells' blocks change."""
    pdk_text = _join_continuations(pdk_text)
    pdk_blocks = subckt_blocks(pdk_text)
    cdl_blocks = subckt_blocks(cdl_text)
    replaced, missing = {}, {}
    pieces, last = [], 0
    for name, (start, end) in sorted(cdl_blocks.items(), key=lambda kv: kv[1][0]):
        if name not in cells:
            continue
        if name not in pdk_blocks:
            missing[name] = "absent from the open_pdks SPICE"
            continue
        ps, pe = pdk_blocks[name]
        new_block, counts = normalize_layout(pdk_text[ps:pe])
        if not new_block.endswith("\n"):
            new_block += "\n"
        old_block = cdl_text[start:end]
        pieces.append(cdl_text[last:start])
        pieces.append(new_block)
        last = end
        replaced[name] = {"cdl_block_sha256": _sha(old_block), "pdk_block_sha256": _sha(new_block),
                          "normalization": counts}
    pieces.append(cdl_text[last:])
    for name in cells:
        if name not in cdl_blocks and name not in missing:
            missing[name] = "absent from the CDL"
    return "".join(pieces), {"replaced": replaced, "not_replaced": missing}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("normalized_cdl", type=Path, help="library-normalised CDL, rewritten in place")
    ap.add_argument("pdk_spice", type=Path, help="open_pdks transistor-level SPICE of the library")
    ap.add_argument("library", help="standard-cell library name, e.g. sky130_fd_sc_hd")
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST)
    ap.add_argument("--receipt", type=Path, required=True)
    a = ap.parse_args()
    spec = json.loads(a.list.read_text())
    cells = list((spec.get("libraries") or {}).get(a.library) or [])
    before = a.normalized_cdl.read_text()
    receipt = {"schema": "r2g.sky130_cell_override_receipt.v1", "library": a.library,
               "override_list": str(a.list), "override_list_sha256": _sha(a.list.read_text()),
               "pdk_spice": str(a.pdk_spice), "cdl_before_sha256": _sha(before)}
    if not cells:
        receipt.update(replaced={}, not_replaced={}, cdl_after_sha256=receipt["cdl_before_sha256"],
                       note="no overrides listed for this library")
    else:
        pdk = a.pdk_spice.read_text()
        after, fields = apply_overrides(before, pdk, cells)
        tmp = a.normalized_cdl.with_name(a.normalized_cdl.name + ".tmp")
        tmp.write_text(after)
        tmp.replace(a.normalized_cdl)
        receipt.update(fields, pdk_spice_sha256=_sha(pdk), cdl_after_sha256=_sha(after))
    a.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"replaced": sorted(receipt["replaced"]), "not_replaced": receipt["not_replaced"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
