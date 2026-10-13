#!/usr/bin/env python3
"""Batch-ingest bridged campaign projects through one sqlite connection.

`ingest_run.py` as a CLI costs 2.58 s/design, nearly all of it interpreter
startup plus reopening the 16 MB store -- 7,491 designs would take 5.4 h. Its
`ingest(project, conn)` takes an open connection, so one process and one
connection does the same work with the same code path.

Run from the knowledge/ directory (sibling imports resolve from cwd), e.g.
    cd .../signoff-loop/knowledge && python3 ingest_batch.py --projects ... --db ...

Idempotent: ingest keys run_id on the project plus ppa.json's mtime, so a
re-run over the same tree replaces rather than duplicates.
"""
import argparse, os, sys, time, traceback, glob

import knowledge_db
from ingest_run import ingest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True, help="parent of the project dirs")
    ap.add_argument("--db", required=True)
    ap.add_argument("--commit-every", type=int, default=200)
    ap.add_argument("--errors", default=None, help="write failing projects here")
    a = ap.parse_args()

    projects = sorted(d for d in glob.glob(os.path.join(a.projects, "*"))
                      if os.path.isdir(d))
    print(f"{len(projects)} projects -> {a.db}", flush=True)
    conn = knowledge_db.connect(a.db)
    ok = bad = 0
    errs = []
    t0 = time.time()
    for i, p in enumerate(projects, 1):
        try:
            ingest(p, conn)
            ok += 1
        except Exception as e:
            bad += 1
            errs.append((p, f"{type(e).__name__}: {e}"))
        if i % a.commit_every == 0:
            conn.commit()
            el = time.time() - t0
            print(f"  {i}/{len(projects)}  ok={ok} bad={bad}  "
                  f"{i/el:.1f}/s  eta {(len(projects)-i)/(i/el)/60:.1f} min", flush=True)
    conn.commit()
    conn.close()
    print(f"done: ok={ok} bad={bad} in {(time.time()-t0)/60:.1f} min", flush=True)
    if errs:
        print("first 5 errors:", flush=True)
        for p, e in errs[:5]:
            print(f"  {os.path.basename(p)}: {e}", flush=True)
        if a.errors:
            with open(a.errors, "w") as f:
                for p, e in errs:
                    f.write(f"{p}\t{e}\n")
    return 1 if bad and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
