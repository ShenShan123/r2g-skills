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
