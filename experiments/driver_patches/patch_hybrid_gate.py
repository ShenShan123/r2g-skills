import pathlib
p = pathlib.Path("/proj/workarea/yangao/gf180_hybrid_drc.py")
s = p.read_text()

old = '''        if d.get("drc_status") in NO_VERDICT:
            todo.append(Path(f).parent.name)'''
new = '''        if d.get("drc_status") not in NO_VERDICT:
            continue
        # A design with NO LAYOUT cannot be DRC'd by any checker -- there is
        # nothing to check. Running the official runner on it burns an hour to
        # learn what the absent GDS already says.
        #
        # This filter is why --dry-run exists: the selection without it was 452
        # designs and 40.6 h, because `drc_status is None` also covers every
        # design that never reached a layout. The timeouts -- the designs a
        # second checker can actually help -- are a fraction of that.
        if not (Path(f).parent / "6_final.gds").is_file():
            continue
        todo.append(Path(f).parent.name)'''
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("gf180_hybrid_drc.py: skip designs with no GDS")
