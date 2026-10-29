"""Prepared integration runtime binding; does not grant a launch or gate waiver."""
import hashlib
import json
from pathlib import Path

from bridge import ROOT

PATH = ROOT / "inputs/rtlfixer-runtime-freeze.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    if PATH.exists():
        raise ValueError("never overwrite an integration runtime freeze")
    bases = [ROOT / "adapters", ROOT / "code/memory", ROOT / "sources/rtlfixer/src",
             ROOT / "sources/verilog-eval/verilog_eval"]
    files = {p for base in bases for p in base.rglob("*.py") if "__pycache__" not in p.parts}
    files.update([ROOT / "inputs/first-batch.json", ROOT / "inputs/rtlfixer-first2.json",
                  ROOT / "sources/rtlfixer/src/benchmarks/verilogeval-syntax.jsonl"])
    data = {"schema": "table4-rtlfixer-integration-runtime-v1", "paper_data": False,
            "qualified_for_full_mechanism_claim": False,
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}}
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def validate():
    data = json.loads(PATH.read_text())
    changed = [name for name, value in data["files"].items()
               if not (ROOT / name).is_file() or sha(ROOT / name) != value]
    if changed:
        raise ValueError("unregistered runtime changes: " + ",".join(changed))
    return data


if __name__ == "__main__":
    print(json.dumps({"files": len(prepare()["files"])}))
