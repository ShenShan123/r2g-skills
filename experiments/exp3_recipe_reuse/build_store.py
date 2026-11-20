#!/usr/bin/env python3
"""Build the accumulated recipe store for the baseline re-run: a copy of the runtime's shipped knowledge.sqlite
plus every distilled, gate-passing recipe from the Exp3 LLM-fallback attempts (with its independent-unit
evidence). Only recipes verified on >= ADMIT_UNITS units are OFFERED by R2G (retrieve's rule).
  build_store.py --shipped SHIPPED.sqlite --out STORE.sqlite --inputs DIR --tally-inputs DIR --tally-csv CSV
"""
import argparse, json, shutil, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate as S
lr = S.lr

ap = argparse.ArgumentParser()
for k in ("--shipped", "--out", "--inputs", "--tally-inputs", "--tally-csv"):
    ap.add_argument(k, required=True)
ap.add_argument("--splits-dev", default=str(S.REPO / "experiments" / "splits" / "dev.txt"))
a = ap.parse_args()
A = S.build_attempts(a.inputs, a.tally_inputs, a.tally_csv)
units = S.units_of([x["case_id"] for x in A], a.splits_dev, {x["case_id"]: x.get("rtl_hash") for x in A if x.get("rtl_hash")})
shutil.copy(a.shipped, a.out)
c = sqlite3.connect(a.out); lr.ensure_schema(c)
n = 0
for x in A:
    if "recipe" in x.get("distill", {}):
        lr.record(c, x["distill"]["recipe"], x["case_id"] + "/" + S.MODEL[x["model"]], units[x["case_id"]],
                  {"model": x["model"], "root": x["root"], "llm_cost": x["cost"]}); n += 1
rows = c.execute("SELECT r.recipe_id, r.platform, r.signature_json, r.strategy_json, COUNT(DISTINCT e.unit) "
                 "FROM learned_recipes r JOIN learned_recipe_evidence e USING(recipe_id) GROUP BY r.recipe_id").fetchall()
val = [r for r in rows if r[4] >= lr.ADMIT_UNITS]
manifest = {"donor_attempts": n, "recipes": len(rows), "validated": [
    {"recipe_id": r[0], "platform": r[1], "symptom": json.loads(r[2]), "edits": json.loads(r[3])["config_edits"], "units": r[4]} for r in val]}
c.close()
Path(a.out).with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1))
print(json.dumps(manifest, indent=1))
