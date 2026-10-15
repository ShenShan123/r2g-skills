#!/usr/bin/env python3
"""Stage a symptom class's CURRENT exhibitors as flowable repair subjects.

Stages from the CORPUS run dir's own config.mk, and refuses any config carrying
the r2g signoff-fix auto-block. That guard exists because staging from a tree a
previous repair round had already touched is how a measurement silently becomes
meaningless: 20 sky130hd m3.2 subjects were staged by `cp -a` from a pilot tree
whose fixer had written `PLACE_PINS_ARGS = -exclude right:*` into every
config.mk. backend/ and reports/ were reset but the auto-block was not, so all
20 began ALREADY REPAIRED, signed off clean on the first pass with zero fix
iterations, and read as "the violation does not reproduce" when the truth was
"the fix was already applied". The flow itself is deterministic -- the same
config.mk reproduced the corpus DEF byte-for-byte (md5 fc4bd8bf9769,
COMPONENTS 1398) across three runs and two different NUM_CORES values.

Container paths are repointed the way the bridge does: the campaign mounted its
scratch at /work, so SDC_FILE (all platforms) plus PDN_TCL and TAPCELL_TCL
(gf180) name files that exist only inside the flow that produced them.
"""
import argparse, collections, json, os, re, shutil, sqlite3, sys

D = "/proj/workarea/yangao"
CORPUS = {"nangate45": "n45_full", "sky130hd": "full_out", "gf180": "gf180_full"}
WORK_OVERRIDES = {"gf180": {"PDN_TCL": "gf180_pdnfix/a.cfg",
                            "TAPCELL_TCL": "gf180_tapfix/d30.tcl"}}
BLOCK_START = "# >>> r2g signoff-fix (auto) >>>"
KNOB = re.compile(r"^(\s*(?:override\s+)?export\s+)(\w+)(\s*=\s*)(.*)$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--classes", required=True, help="comma-separated symptom classes")
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=f"{D}/knowledge_campaign.sqlite")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    classes = [c.strip() for c in a.classes.split(",") if c.strip()]
    c = sqlite3.connect(a.db)
    sids = [r[0] for cl in classes for r in c.execute(
        "SELECT symptom_id FROM symptoms WHERE class=? AND check_type='drc'", (cl,))]
    if not sids:
        sys.exit(f"no symptom row for {classes}")
    ph = ",".join("?" * len(sids))
    rows = c.execute(
        f"SELECT r.design_name, r.project_path, r.cell_count, r.design_family "
        f"FROM run_violations v JOIN runs r USING(run_id) "
        f"WHERE v.symptom_id IN ({ph}) AND r.platform=? "
        f"  AND r.run_id=(SELECT r2.run_id FROM runs r2 "
        f"                WHERE r2.project_path=r.project_path "
        f"                ORDER BY r2.ingested_at DESC, r2.run_id DESC LIMIT 1) "
        f"  AND (r.lvs_status IS NULL OR r.lvs_status IN ('clean','skipped')) "
        f"  AND r.project_path NOT LIKE '%_abA_%' AND r.project_path NOT LIKE '%_abB_%' "
        f"GROUP BY r.design_name ORDER BY MIN(r.cell_count)", (*sids, a.platform)).fetchall()
    print(f"{a.platform} {classes}: {len(rows)} current exhibitors")

    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    corpus_root = os.path.join(D, CORPUS[a.platform], "runs")
    kept, fams, skipped = [], collections.Counter(), collections.Counter()
    for name, proj, cells, fam in (rows[:a.limit] if a.limit else rows):
        # the bridged project name embeds the corpus run dir after the platform
        base = os.path.basename(proj)
        run = None
        for cand in (base.split("__", 2)[-1], base.split("__", 1)[-1], base):
            if os.path.isdir(os.path.join(corpus_root, cand)):
                run = os.path.join(corpus_root, cand)
                break
        if run is None:
            skipped["no_corpus_run"] += 1
            continue
        src_cfg = os.path.join(run, "config.mk")
        if not os.path.isfile(src_cfg):
            skipped["no_config"] += 1
            continue
        txt = open(src_cfg, errors="ignore").read()
        if BLOCK_START in txt:
            skipped["already_repaired"] += 1      # the guard
            continue
        dst = os.path.join(out, os.path.basename(run))
        os.makedirs(os.path.join(dst, "constraints"))
        sdc_src = os.path.join(run, "constraint.sdc")
        if os.path.isfile(sdc_src):
            shutil.copy2(sdc_src, os.path.join(dst, "constraints", "constraint.sdc"))
        lines, mapping = [], WORK_OVERRIDES.get(a.platform, {})
        for ln in txt.splitlines():
            m = KNOB.match(ln)
            if m and "/work/" in m.group(4):
                k = m.group(2)
                if k == "SDC_FILE":
                    ln = f"{m.group(1)}{k}{m.group(3)}{dst}/constraints/constraint.sdc"
                elif k in mapping:
                    t = os.path.join(D, mapping[k])
                    if os.path.isfile(t):
                        ln = f"{m.group(1)}{k}{m.group(3)}{t}"
            lines.append(ln)
        open(os.path.join(dst, "constraints", "config.mk"), "w").write(
            "\n".join(lines) + "\n")
        left = [l for l in lines if "/work/" in l]
        if left:
            skipped["unresolved_work_path"] += 1
            shutil.rmtree(dst)
            continue
        kept.append(dst)
        fams[fam] += 1
    print(f"staged {len(kept)}  families {len(fams)}  "
          f"cells {rows[0][2]}..{rows[min(len(kept), len(rows)) - 1][2]}")
    if skipped:
        print(f"skipped {dict(skipped)}")
    json.dump(kept, open(f"{out}_paths.json", "w"))
    print(f"wrote {out}_paths.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
