import pathlib
p = pathlib.Path("/proj/workarea/yangao/orfs_spec.py")
s = p.read_text()
old = '''                    "5_route.odb", "6_final.odb"):
        for src in work.rglob(pattern):
            shutil.copy2(src, dest / src.name)
            break'''
new = '''                    "5_route.odb", "6_final.odb",
                    # The LVS INPUT chain and its RESULT, added 2026-10-17 after
                    # three forensics attempts had to reconstruct or give up:
                    #   6_final.v -> 6_final.cdl -> 6_final_concat.cdl (+ the
                    #   platform std-cell CDL) -> 6_lvs.lvsdb
                    # None of it was kept. 6_lvs.lvsdb is LVS's equivalent of
                    # DRC's 6_drc.lyrdb -- the per-device result -- so without it
                    # an LVS verdict can only ever be re-derived by re-running,
                    # and `make lvs` cannot re-run on a frozen layout
                    # (RMD-P0-01). EXPERIMENT_RUNBOOK_zh.md settles LVS
                    # equivalence questions by the md5 of 6_final.cdl and the
                    # extracted netlist, which is impossible if neither survives.
                    "6_final.cdl", "6_final_concat.cdl", "6_lvs.lvsdb",
                    # and the logs that name the rule or exception, which the
                    # result.json verdict never does
                    "6_cdl.log", "6_lvs.log"):
        for src in work.rglob(pattern):
            shutil.copy2(src, dest / src.name)
            break'''
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("orfs_spec.py: LVS 的输入链与结果库已加入保留清单")
