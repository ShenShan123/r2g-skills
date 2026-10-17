# Driver patches for the frozen baseline re-run

The corpus driver (`orfs_baseline.py`) lives on the run host, not in this repo.
These scripts record the delta applied to it, so the change is reviewable and
re-appliable:

```
cp orfs_baseline.py orfs_spec.py
python3 patch_spec_driver.py     # spec-table hook + the three SDC additions
python3 patch_sdc2.py            # derived constraints as Python-computed literals
```

`patch_spec_driver.py` adds:
- a `R2G_SPEC_TABLE` hook that replaces `CORE_UTILIZATION` with the explicit
  `DIE_AREA`/`CORE_AREA` from `make_spec_table.py`, and feeds that design's
  period into the SDC
- `set_clock_uncertainty` (was `0.0`) and `set_input_delay`/`set_output_delay`

`patch_sdc2.py` then moves the derived values out of Tcl: `expr {2} * 0.05` is
legal (Tcl's `expr` concatenates its arguments) but it is a brace-escaping
hazard inside a Python `.format()` template and reads wrong, so the uncertainty
and I/O delay are computed in Python and emitted as literals.

Every assertion in both scripts is an anchor-uniqueness check — they refuse to
patch rather than patch the wrong place if the driver has moved on.

## Artifact retention (2026-10-17)

```
python3 patch_retain_lvs.py       # flow-side keep list
python3 patch_signoff_retain.py   # signoff-side copy-back, before the rmtree
```

LVS's whole input chain and its result were being discarded. `make lvs` builds

```
6_final.v -> 6_final.cdl -> 6_final_concat.cdl (+ the platform std-cell CDL)
          -> 6_lvs.lvsdb
```

inside the signoff project directory, and that directory is deleted at the end
of every design. `6_lvs.lvsdb` is **LVS's equivalent of DRC's `6_drc.lyrdb`** —
the per-device result — so without it an LVS verdict cannot be re-examined at
all: the layout is frozen, `make lvs` refuses to regenerate the physical stages
(RMD-P0-01), and `EXPERIMENT_RUNBOOK_zh.md` settles LVS equivalence questions by
the md5 of `6_final.cdl` and the extracted netlist, which needs both to survive.

Three separate investigations this session had to reconstruct or abandon for
exactly this reason, the last being 11 designs whose LVS crashed with a
backtrace carrying addresses but no exception message.

The signoff-side patch is the load-bearing one: these files are produced by the
SIGNOFF pass, not by the flow, so the flow-side keep list never finds them. It
inserts before the final `rmtree` — the other two `rmtree` calls in that file
are a pre-build clear and a `no_gds` early exit, neither of which has artifacts
to lose.

What is deliberately NOT kept: the per-stage ODBs. One design's `results`
directory is **2.6 GB** because each of ~15 ODBs is ~200 MB, so keeping them all
would cost 2.6 GB x 2497 x 3 = **19.5 TB** against 36 TB free. `5_route.odb` and
`6_final.odb` are already kept, which is what the frozen-layout LVS path needs.
Note that ORFS writes no per-stage `.def` at all — it works in ODB and streams a
DEF only on demand — so the four-stage dataset's `2_floorplan`/`3_place`/`4_cts`
DEFs would have to be generated before cleanup rather than copied. That is a
separate decision and is not part of this change.

### What the retention list ends up as, and why each entry

```
config.mk constraint.sdc flow.log
6_final.def 6_final.spef 6_final.sdc 6_final.v 6_final.gds
1_synth.v 1_2_yosys.v
5_route.odb 6_final.odb 2_floorplan.odb 3_place.odb 4_cts.odb
6_final.cdl 6_final_concat.cdl 6_lvs.lvsdb 6_cdl.log 6_lvs.log
```

- **`1_2_yosys.v`, not just `1_synth.v`** — the four-stage dataset takes its
  canonical topology from `1_2_yosys.v` (so a resizer-deleted gate stays a node
  with `placement_valid=0`), and rtl-acquire's `netlist_graph.pt` is built from
  it. Two downstream products; `1_synth.v` substitutes for neither.
- **`2_floorplan.odb` / `3_place.odb` / `4_cts.odb`** — the per-stage views E3
  needs. ORFS writes no per-stage `.def`, so they cannot be recovered later from
  anything else.
- **ODB rather than a DEF generated now** — decided against my first instinct by
  measurement. `write_def` on a 197 MB `3_place.odb` takes **20.9 s and produces
  a 311.7 MB DEF**: eager conversion costs MORE disk (7.0 TB against 4.5 TB over
  7491 designs) *and* 11 h of campaign time. The one real argument for
  converting -- an ODB is a version-bound OpenROAD database while a DEF is
  tool-independent text -- is answered more cheaply by provenance, since
  `rerun_preflight_gates.py` records the container image id and the conversion
  can be re-run in the image that wrote the database.
- **NOT kept**: the ~10 other per-stage ODBs (`1_synth`, `2_1`..`2_4`, `3_1`..`3_5`,
  `4_1`, `5_1`). One design's `results` directory is 2.6 GB; keeping all of it
  would cost 19.5 TB against 36 TB free, and nothing downstream reads them.

Total standing cost of the three added ODBs: about 4.5 TB over the three
platforms, on a volume with 36 TB free.
