import pathlib
p = pathlib.Path("/proj/workarea/yangao/orfs_spec.py")
s = p.read_text()

# the three per-stage ODBs were added only for def-graph's four-stage dataset.
# Those graph experiments use their own corpus, so this corpus never feeds them.
old = '''                    # The per-stage views def-graph's four-stage causal dataset
                    # (E3) needs. ORFS writes no per-stage .def at all -- it
                    # works in ODB and streams a DEF on demand -- so these
                    # cannot be copied later from anything.
                    #
                    # Keeping the ODB rather than converting now, decided AGAINST
                    # my first instinct by measurement: write_def on a 197 MB
                    # 3_place.odb takes 20.9 s and produces a 311.7 MB DEF. So
                    # converting eagerly costs MORE disk (7.0 TB against 4.5 TB
                    # over 7491 designs) AND 11 h of campaign time. The one
                    # argument for converting -- an ODB is a version-bound
                    # OpenROAD database, a DEF is tool-independent text -- is
                    # answered more cheaply by provenance: the launch gate
                    # records the container image id, so the conversion can
                    # always be re-run in the image that wrote the database.
                    "2_floorplan.odb", "3_place.odb", "4_cts.odb",
'''
assert s.count(old) == 1, "阶段 ODB 锚点不唯一"
s = s.replace(old, "")

old2 = '''                    # 1_2_yosys.v, not just 1_synth.v: the four-stage dataset
                    # takes its canonical topology from 1_2_yosys.v (so a
                    # resizer-deleted gate stays a node) and rtl-acquire's
                    # netlist_graph.pt is built from it too. Two downstream
                    # products, neither of which 1_synth.v substitutes for.
                    "1_2_yosys.v",
'''
assert s.count(old2) == 1, "yosys 锚点不唯一"
s = s.replace(old2, "")

# record WHY they are absent, so the next person does not re-add them
marker = '''                    "5_route.odb", "6_final.odb",'''
note = '''                    "5_route.odb", "6_final.odb",
                    # NOT kept, on purpose (2026-10-17): the per-stage ODBs
                    # 2_floorplan / 3_place / 4_cts and 1_2_yosys.v. They would
                    # only serve def-graph's four-stage dataset and
                    # rtl-acquire's netlist_graph.pt, and the graph experiments
                    # run on their OWN corpus -- this corpus never feeds them.
                    # Keeping them cost 4.5 TB for nothing. If that ever
                    # changes, note that ORFS writes no per-stage .def, and that
                    # converting an ODB eagerly is the WRONG trade: write_def on
                    # a 197 MB 3_place.odb takes 20.9 s and yields a 311.7 MB
                    # DEF, so it costs more disk AND more time than keeping the
                    # ODB. The launch gate records the image id, so a later
                    # conversion can use the binary that wrote the database.'''
assert s.count(marker) == 1, "标记锚点不唯一"
s = s.replace(marker, note)
p.write_text(s)
print("orfs_spec.py: 已撤掉为转图保留的文件（阶段 ODB + 1_2_yosys.v）")
