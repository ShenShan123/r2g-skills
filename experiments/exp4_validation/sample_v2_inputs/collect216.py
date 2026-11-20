import json, glob, os, re, sys
from pathlib import Path
D = Path("/proj/workarea/yangao")
ITEM = re.compile(r"<item>")
NEED = ["6_final.def","6_final.spef","6_final.v","6_final.sdc","5_route.odb","3_place.odb","config.mk","constraint.sdc"]
def recovered(run, rec):
    if rec in ("clean","violations"): return None
    rep, gds = run/"6_drc.lyrdb", run/"6_final.gds"
    if not (rep.is_file() and gds.is_file()): return None
    if rep.stat().st_mtime < gds.stat().st_mtime: return None
    try: n = len(ITEM.findall(rep.read_text(errors="replace")))
    except Exception: return None
    return "clean" if n == 0 else "violations"
out = []
for plat, root in (("sky130hd","rerun_sky130hd_20261022"),("gf180","rerun_gf180_20261026")):
    for f in sorted(glob.glob(str(D/root/"runs"/"*"/"result.json"))):
        run = Path(f).parent
        try: d = json.load(open(f))
        except Exception: continue
        rec = recovered(run, d.get("drc_status"))
        out.append(dict(platform=plat, root=str(D/root), run=run.name,
            key="__".join(run.name.split("__")[:-2]), cells=d.get("cells"),
            drc_status=d.get("drc_status"), drc_recovered=rec, drc_checker=d.get("drc_checker"),
            lvs_status=d.get("lvs_status"), period=d.get("period"),
            missing=[n for n in NEED if not (run/n).is_file()]))
json.dump(dict(rows=out, dev=open(D/"splits/dev.txt").read().split(),
               dev_sha=__import__("hashlib").sha256(open(D/"splits/dev.txt","rb").read()).hexdigest()), sys.stdout)
