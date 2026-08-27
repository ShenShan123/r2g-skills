# Add-designs source-freeze preflight (2026-08-27)

This receipt covers the metadata-only `freeze -> prepare` preflight for one
`sky130hs/uart` `DENSITY_RELIEF` pair.  It does not run ORFS and it does not
write canonical memory.  The source-freeze command bound the exact campaign
arguments, 153 TEHM/script source files, one ORFS input set, and the packaged
toolchain before materialization.

The freeze was bound to OpenROAD `26Q3-1510-g6cb3f2b704` and Yosys `0.68`, with
fingerprint `5f6545216c040a863a4c169d90f895f218ad038bfd61991445f760ed43567c91`.
Prepare emitted before/after config and SDC digests, the UART source digest,
and a fixed `1.4 ns` timing contract.  A subsequent SDC mutation was rejected
by the input-digest gate.  The full temporary freeze used for this receipt was
under `/tmp/tehm-orfs/add-designs-freeze-r1`; the JSON summary below retains
the content-bound identifiers needed to reproduce it.
