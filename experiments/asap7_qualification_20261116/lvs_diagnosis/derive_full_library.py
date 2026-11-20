"""Build the reusable complete RVT transistor CDL from pinned official PEX."""
import hashlib
import json
import re
from derive_oai21_reference import HERE, ROOT, UPSTREAM, COMMIT, circuits, derive


def main():
    original = ROOT / "gcd_smoke/lvs/asap7_R_all_physical.cdl"
    text = original.read_text()
    source = circuits(text)
    pex = circuits(UPSTREAM.read_text())
    physical_only = {"tapcell_asap7_75t_r", "filler_asap7_75t_r", "fillerxp5_asap7_75t_r"}
    records = []
    for name, cell in source.items():
        if name in physical_only:
            assert not cell["lines"], (name, cell["lines"])
            continue
        assert name in pex
        port_map = {n.upper(): n for n in cell["ports"]}
        assert set(port_map) == {n.upper() for n in pex[name]["ports"]}
        mos, resistors, capacitors = derive(name, pex)
        for d in mos:
            d[1:5] = [port_map.get(n.upper(), n) for n in d[1:5]]
        pattern = re.compile(r"(?im)^\.SUBCKT " + re.escape(name) + r"\b[^\n]*\n.*?^\.ENDS[^\n]*", re.S)
        match = pattern.search(text)
        assert match
        header = match.group().splitlines()[0]
        block = header + "\n" + "\n".join(" ".join(d) for d in mos) + "\n.ENDS"
        text = text[:match.start()] + block + text[match.end():]
        records.append({"cell": header.split()[1], "transistors": len(mos), "resistors_contracted": resistors,
                        "capacitors_omitted": capacitors, "port_order_preserved": cell["ports"]})
    out = HERE / "asap7_R_official_pex_full.cdl"
    out.write_text("* Complete RVT library from independently published official xACT PEX, commit " + COMMIT + ".\n" + text)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {"original_sha256": digest(original), "upstream_pex_sha256": digest(UPSTREAM), "output_sha256": digest(out),
              "source_commit": COMMIT,
              "source_url": f"https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/{COMMIT}/CDL/xAct3D_extracted/asap7sc7p5t_28_R.sp",
              "cells_derived": len(records), "physical_zero_transistor_cells_retained": sorted(physical_only),
              "referenced_transistors": sum(c["transistors"] for c in records),
              "tested_design_extraction_used_as_reference": False, "cells": records}
    (HERE / "full_library_provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "cells"}, indent=2))


if __name__ == "__main__":
    main()
