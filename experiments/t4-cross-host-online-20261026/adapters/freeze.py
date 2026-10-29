"""Bind the first integration runtime; no provider calls."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    path = ROOT / "inputs/scout-runtime-freeze-r2.json"
    if path.exists(): raise ValueError("runtime freeze already exists; do not overwrite")
    files = {p for base in [ROOT / "adapters", ROOT / "code/memory", ROOT / "sources/rtlscout"]
             for p in base.rglob("*.py") if "__pycache__" not in p.parts}
    files.update(p for base in [ROOT / "sources/rtlscout/benchmarks/simple_adder",
                               ROOT / "sources/rtlscout/benchmarks/fifo_sync4"]
                 for p in base.rglob("*") if p.is_file())
    files.update([ROOT / "inputs/first-batch.json", ROOT / "inputs/empty.env"])
    data = {"schema": "table4-scout-integration-freeze-v1", "paper_data": False,
            "host_commit": subprocess.check_output(["git", "-C", str(ROOT / "sources/rtlscout"),
                                                     "rev-parse", "HEAD"], text=True).strip(),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def validate():
    data = json.loads((ROOT / "inputs/scout-runtime-freeze-r2.json").read_text())
    bad = [name for name, value in data["files"].items() if not (ROOT / name).is_file()
           or sha(ROOT / name) != value]
    if bad: raise ValueError("frozen runtime changed: " + ", ".join(bad))
    return data


if __name__ == "__main__":
    print(json.dumps({"files": len(prepare()["files"])}))
