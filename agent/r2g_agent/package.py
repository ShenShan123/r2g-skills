"""Bundle a job's passing designs into one downloadable dataset archive.

Only designs whose status is PASSED (dual-clean sign-off AND a green dataset
verifier) are included -- the archive is the deliverable, so it carries
nothing that was not judged clean. index.json records, per design, the verdicts
and the sizing it was built under, so a user can filter without unpacking.
"""
from __future__ import annotations

import json
import tarfile
import time
from pathlib import Path

from .jobs import PASSED, JobStore
from .paths import r2g_home

REPORTS = ("drc.json", "lvs.json", "ppa.json", "graph_dataset.json", "verify.json",
           "signoff_manifest.json", "layout.png")


def package_job(store: JobStore, jid: str) -> Path:
    job = store.job(jid)
    out_dir = r2g_home() / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("r2g_dataset_%s.tar.gz" % jid)
    index = {"job": jid, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
             "platform": job["spec"].get("platform"), "output": job["spec"].get("output", "graph"),
             "designs": []}
    with tarfile.open(out, "w:gz") as tar:
        for d in job["designs"]:
            if d["status"] != PASSED:
                continue
            p = Path(d["project"])
            root = "r2g_dataset_%s/%s" % (jid, d["name"])
            if (job["spec"] or {}).get("output") == "image":
                for f in ("features.npz", "labels.npz", "image_meta.json", "verify.json"):
                    if (p / "images" / f).is_file():
                        tar.add(p / "images" / f, arcname=root + "/images/" + f)
                if (p / "images" / "png").is_dir():
                    tar.add(p / "images" / "png", arcname=root + "/images/png")
                if (p / "reports" / "image_dataset.json").is_file():
                    tar.add(p / "reports" / "image_dataset.json", arcname=root + "/reports/image_dataset.json")
            else:
                tar.add(p / "dataset", arcname=root + "/dataset")
            for r in REPORTS:
                f = p / "reports" / r
                if f.is_file():
                    tar.add(f, arcname=root + "/reports/" + r)
            for f in ("metadata.json",):
                if (p / f).is_file():
                    tar.add(p / f, arcname=root + "/" + f)
            det = d.get("detail") or {}
            index["designs"].append({"name": d["name"], "sizing": det.get("sizing"),
                                     "signoff": det.get("signoff"), "verify": det.get("verify")})
        data = json.dumps(index, indent=1, ensure_ascii=False).encode()
        info = tarfile.TarInfo("r2g_dataset_%s/index.json" % jid)
        info.size = len(data)
        info.mtime = time.time()
        import io
        tar.addfile(info, io.BytesIO(data))
    return out
