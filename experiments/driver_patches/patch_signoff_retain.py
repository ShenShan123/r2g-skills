import pathlib
p = pathlib.Path("/proj/workarea/yangao/signoff45.py")
s = p.read_text()
old = """    out["seconds"] = round(time.time() - t0, 1)
    shutil.rmtree(proj, ignore_errors=True)
    return out"""
new = '''    out["seconds"] = round(time.time() - t0, 1)

    # Keep LVS's input chain and its result BEFORE the project dir is deleted.
    # Added 2026-10-17: three separate forensics attempts had to reconstruct or
    # abandon, because `make lvs` builds
    #     6_final.v -> 6_final.cdl -> 6_final_concat.cdl (+ platform std-cell
    #     CDL) -> 6_lvs.lvsdb
    # inside this project dir and none of it survived the rmtree. 6_lvs.lvsdb is
    # LVS's equivalent of DRC's 6_drc.lyrdb -- the per-device result -- so
    # without it an LVS verdict cannot be re-examined at all: the layout is
    # frozen, so `make lvs` refuses to regenerate (RMD-P0-01), and re-deriving
    # is exactly what this is meant to make unnecessary.
    # EXPERIMENT_RUNBOOK_zh.md settles LVS equivalence by the md5 of 6_final.cdl
    # and the extracted netlist, which needs both to exist.
    for _pat in ("6_final.cdl", "6_final_concat.cdl", "6_lvs.lvsdb",
                 "6_cdl.log", "6_lvs.log"):
        for _src in proj.rglob(_pat):
            try:
                shutil.copy2(_src, run_dir / _src.name)
            except OSError:
                pass
            break

    shutil.rmtree(proj, ignore_errors=True)
    return out'''
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("signoff45.py: LVS 产物在删除 proj 之前回拷")
