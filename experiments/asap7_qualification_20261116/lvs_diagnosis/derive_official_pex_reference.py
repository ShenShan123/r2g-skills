"""Generate GCD transistor references solely from published vendor PEX cells."""
from collections import Counter
import hashlib
import json
import re
from derive_oai21_reference import HERE, ROOT, UPSTREAM, COMMIT, circuits, derive


def main():
    original = ROOT / "gcd_calibre_input/gcd_device_complete.cdl"
    text = original.read_text()
    source = circuits(text)
    pex = circuits(UPSTREAM.read_text())
    usage = Counter(line.split()[-1] for line in source["gcd"]["lines"] if line.startswith("X"))
    changes = []
    for cell_name, count in sorted(usage.items()):
        assert cell_name.lower() in pex
        assert set(source[cell_name.lower()]["ports"]) == set(pex[cell_name.lower()]["ports"])
        mos, resistors, capacitors = derive(cell_name, pex)
        pattern = re.compile(r"(?im)^\.SUBCKT " + re.escape(cell_name) + r"\b[^\n]*\n.*?^\.ENDS[^\n]*", re.S)
        matches = list(pattern.finditer(text))
        assert len(matches) == 1
        block = matches[0].group()
        header = block.splitlines()[0]
        new_block = header + "\n" + "\n".join(" ".join(d) for d in mos) + "\n.ENDS"
        text = text[:matches[0].start()] + new_block + text[matches[0].end():]
        changes.append({"cell": cell_name, "instances": count, "transistors_retained": len(mos),
                        "parasitic_resistors_contracted": resistors,
                        "parasitic_capacitors_omitted": capacitors,
                        "top_level_cell_pin_order_preserved": header})
    assert circuits(text)["gcd"] == source["gcd"]
    out = HERE / "gcd_official_pex_all_cells.cdl"
    out.write_text("* References derived from independently published official xACT PEX; no tested layout extraction used.\n" + text)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    provenance = {
        "source_url": f"https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/{COMMIT}/CDL/xAct3D_extracted/asap7sc7p5t_28_R.sp",
        "source_sha256": digest(UPSTREAM), "original_sha256": digest(original), "candidate_sha256": digest(out),
        "cell_types_referenced": len(changes), "design_instance_count": sum(usage.values()),
        "original_top_level_cdl_unchanged": True,
        "tested_design_extraction_used_as_reference": False,
        "method": "Contract only published parasitic R connectivity, omit parasitic C, retain every published MOS and model/L/W/nfin. Preserve original named cell port order.",
        "total_referenced_transistors_with_instances": sum(c["instances"] * c["transistors_retained"] for c in changes),
        "cells": changes,
    }
    (HERE / "official_pex_all_cells_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({k:v for k,v in provenance.items() if k != "cells"}, indent=2))


if __name__ == "__main__":
    main()
