"""Bundle a job's passing designs into one downloadable dataset archive.

Only designs whose status is PASSED (dual-clean sign-off AND a green dataset
verifier) are included -- the archive is the deliverable, so it carries
nothing that was not judged clean. index.json records, per design, the verdicts
and the sizing it was built under, so a user can filter without unpacking.
"""
from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path

from .jobs import PASSED, JobStore
from .paths import r2g_home

REPORTS = ("drc.json", "lvs.json", "ppa.json", "graph_dataset.json", "verify.json",
           "signoff_manifest.json", "layout.png")


def _add_design(tar: tarfile.TarFile, job: dict, d: dict, root: str) -> None:
    p = Path(d["project"])
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
    if (p / "metadata.json").is_file():
        tar.add(p / "metadata.json", arcname=root + "/metadata.json")


def _add_index(tar: tarfile.TarFile, name: str, index: dict) -> None:
    data = json.dumps(index, indent=1, ensure_ascii=False).encode()
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = time.time()
    tar.addfile(info, io.BytesIO(data))


def package_job(store: JobStore, jid: str, partial: bool = False) -> Path:
    from .lifecycle import resource_lock
    with resource_lock('job', jid):
        if store.deletion('job', jid) or not store.job(jid):
            raise ValueError('任务正在删除或已删除')
        return _package_job(store, jid, partial)


def _package_job(store: JobStore, jid: str, partial: bool = False) -> Path:
    """All PASSED designs of a job. partial=True: a snapshot of those passed so
    far, written beside (never over) the final archive."""
    job = store.job(jid)
    out_dir = r2g_home() / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("r2g_dataset_%s%s.tar.gz" % (jid, "_partial" if partial else ""))
    tmp = out.with_suffix(".tmp")
    params = (job["spec"] or {}).get("params") or {}
    # which parameters built it: r2g's measured defaults, or what the user changed
    index = {"job": jid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "partial": partial,
             "platform": job["spec"].get("platform"), "output": job["spec"].get("output", "graph"),
             "params_source": "custom" if params else "r2g_default", "params": params,
             "designs": []}
    with tarfile.open(tmp, "w:gz") as tar:
        for d in job["designs"]:
            if d["status"] != PASSED:
                continue
            _add_design(tar, job, d, "r2g_dataset_%s/%s" % (jid, d["name"]))
            det = d.get("detail") or {}
            index["designs"].append({"name": d["name"], "sizing": det.get("sizing"),
                                     "signoff": det.get("signoff"), "verify": det.get("verify")})
        _add_index(tar, "r2g_dataset_%s/index.json" % jid, index)
    tmp.replace(out)           # a download never sees a half-written archive
    return out


def package_design(store: JobStore, jid: str, name: str) -> Path:
    from .lifecycle import resource_lock
    with resource_lock('job', jid):
        if store.deletion('job', jid) or not store.job(jid):
            raise ValueError('任务正在删除或已删除')
        return _package_design(store, jid, name)


def _package_design(store: JobStore, jid: str, name: str) -> Path:
    job, d = store.job(jid), store.design(jid, name)
    if not job or not d or d["status"] != PASSED:
        raise ValueError("design has not passed")
    out = r2g_home() / "datasets" / "per_design" / jid / ("r2g_%s_%s.tar.gz" % (jid, name))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        _add_design(tar, job, d, "r2g_%s" % name)
        det = d.get("detail") or {}
        _add_index(tar, "r2g_%s/index.json" % name, {"job": jid, "designs": [
            {"name": name, "sizing": det.get("sizing"), "signoff": det.get("signoff"), "verify": det.get("verify")}]})
    tmp.replace(out)
    return out
