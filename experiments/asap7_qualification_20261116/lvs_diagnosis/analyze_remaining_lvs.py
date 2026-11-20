"""Diagnose the original GCD LVS and independently validate the OAI21 candidate."""
from collections import Counter
from decimal import Decimal
from itertools import permutations
import json
import re
from derive_oai21_reference import HERE, ROOT, CELL, circuits


def parse_mos(cell):
    result = []
    for line in cell["lines"]:
        fields = line.split("$", 1)[0].split()
        if fields[0][0].upper() != "M":
            continue
        params = {}
        for f in fields[6:]:
            key, value = f.split("=")
            params[key.lower()] = str(Decimal(value).normalize())
        result.append({"nodes": fields[1:5], "model": fields[5], "params": params})
    return result


def canonical_mos(devices, mapping):
    result = []
    for dev in devices:
        d, g, s, b = [mapping.get(n, n) for n in dev["nodes"]]
        result.append((min(d, s), max(d, s), g, b, dev["model"], tuple(sorted(dev["params"].items()))))
    return Counter(result)


def validate_candidate(candidate, extracted):
    a, b = parse_mos(candidate), parse_mos(extracted)
    ports = set(candidate["ports"])
    assert ports == set(extracted["ports"])
    ai = sorted({n for d in a for n in d["nodes"]} - ports)
    bi = sorted({n for d in b for n in d["nodes"]} - ports)
    assert len(ai) == len(bi)
    target = canonical_mos(b, {})
    for order in permutations(bi):
        mapping = dict(zip(ai, order))
        if canonical_mos(a, mapping) == target:
            return {"transistor_graph_matches": True, "internal_net_mapping": mapping, "ports_identical_by_name": True,
                    "bulk_connections_match": True, "all_l_w_nfin_match": True, "transistors_compared": len(a)}
    return {"transistor_graph_matches": False}


def main():
    evidence = ROOT / "calibre_evidence/gcd_full/lvs_device_complete"
    report = (evidence / "mask.lvs.rep").read_text()
    extracted = circuits((evidence / "mask.lvs.rep.sp").read_text())
    reference = circuits((ROOT / "gcd_calibre_input/gcd_device_complete.cdl").read_text())
    candidate = circuits((HERE / "gcd_oai21_official_pex.cdl").read_text())
    source_instances = []
    for line in reference["gcd"]["lines"]:
        fields = line.split()
        if fields[0].startswith("X") and fields[-1] == CELL:
            source_instances.append(fields[0])
    layout_instances = []
    for line in extracted["gcd"]["lines"]:
        fields = line.split("$", 1)[0].split()
        if fields[0].startswith("X") and fields[-1] == CELL:
            layout_instances.append(fields[0])
    unmatched = []
    for line in report.splitlines():
        if "** no similar net **" in line:
            unmatched.append(line.strip())
    result = {
        "root_cause": "OAI21x1 CDL/LVS and published GDS use logically equivalent but structurally distinct transistor series placement",
        "cell": CELL,
        "source_instances": source_instances,
        "layout_instances": layout_instances,
        "instance_count_each_side": len(source_instances),
        "source_nmos": "Y -- (A1 || A2) -- net11 -- B -- VSS",
        "layout_and_official_pex_nmos": "Y -- B -- internal -- (A1 || A2) -- VSS",
        "source_pmos": "Single 6-fin series A1/A2 branch and 6-fin B branch",
        "layout_and_official_pex_pmos": "Two 3-fin A1/A2 series branches and two 3-fin B branches",
        "unmatched_nets": unmatched,
        "unmatched_decomposition": {"internal_OAI21_each_side": 10, "external_each_side": 4},
        "property_error_caveat": "Original report pairs layout X3562 (OAI21x1) with source X_633_ (HAxp5); the width mismatch is downstream of the erroneous cross-cell correspondence, not independently established as a physical dimension defect.",
        "candidate_validation_against_extraction": validate_candidate(candidate[CELL.lower()], extracted[CELL.lower()]),
        "validation_only": "The tested layout extraction is used only for this separate comparison, never as input to candidate reference generation.",
        "required_next_check": "Run the unchanged complete LVS deck on the candidate reference; do not treat local graph equivalence as a top-level LVS match.",
    }
    assert result["candidate_validation_against_extraction"]["transistor_graph_matches"]
    (HERE / "diagnosis.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
