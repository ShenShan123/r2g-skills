# V3 to V4 migration audit (negative replay evidence)

`migration_report.json` records an output-only migration of the tracked
`../staging/tehm.sqlite` snapshot from `tehm-v3` to `tehm-v4`.  All 13 existing
canonical tables preserve their row counts and content digests, and
`source_unchanged=true`; the migration sets `replay_required=true`.

`honesty_report.json` is a read-only H1–H12/A1 audit of the migrated output.
H1 passes, but H2 (dangling transition/episode provenance), H7 (incomplete
obligation status), and H10 (missing activation rollback authority) fail.  The
result is `DENY_REPLAY_NOT_VERIFIED`; this snapshot is not support, policy, or
promotion input.

`migrated_snapshot.sqlite` is retained locally for replay.  It is intentionally
ignored by the source repository's SQLite rule; the JSON reports are the
portable, reviewable evidence.
