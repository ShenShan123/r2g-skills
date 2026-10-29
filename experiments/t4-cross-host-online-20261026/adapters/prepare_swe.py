"""Fetch two fixed official Verified rows; keep evaluator-only patches private."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = ("https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Verified"
       "&config=default&split=test&offset=0&length=2")


def prepare():
    private = ROOT / "inputs/evaluator-private"
    private.mkdir(mode=0o700, exist_ok=True)
    raw = private / "verified-first2.json"
    if raw.exists(): raise ValueError("already acquired; no silent resampling")
    start = time.time()
    with urllib.request.urlopen(URL, timeout=120) as response:
        body = response.read()
    data = json.loads(body)
    rows = [entry["row"] for entry in data["rows"]]
    if len(rows) != 2 or len({row["instance_id"] for row in rows}) != 2:
        raise ValueError("unexpected dataset rows")
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out: out.write(body)
    public = [{k: row[k] for k in ["instance_id", "repo", "base_commit", "problem_statement"]}
              for row in rows]
    receipt = {"source": URL, "selection": "first two published rows; no outcome-based selection",
               "sha256": hashlib.sha256(body).hexdigest(), "acquisition_wall_s": time.time()-start,
               "paper_data": False, "public_tasks": public,
               "private_patch_fields_not_exposed": True}
    (ROOT / "inputs/swe-verified-first2.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"instances": [row["instance_id"] for row in rows], "new_model_requests": 0}))


if __name__ == "__main__": prepare()
