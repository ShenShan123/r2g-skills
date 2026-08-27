# ORFS capability retention ledger (r2 development)

This is a database-bound replay of the frozen candidate policy from
`tehm-capability-attribution-orfs-r2-dev`.  The replay uses the independent
lineage `failpass-r2-retention:toggle32-v2` and is recorded in the isolated
`tehm.sqlite` copy in this directory.

- `retention_report.json` records the ORFS before/after receipt, held-out
  firewall, policy/load binding, and the ledger verification result.
- `tehm.sqlite` is a writable evidence copy, not the source attribution DB,
  canonical memory DB, or learner-support DB.
- `retention_ledger.authority_eligible=true` means the receipt is valid
  authority-grade retention evidence.  It does not promote a capability or
  load a policy into production; `production_promotion_eligible=false` is
  intentional.

Re-run from the repository root with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory \
python3 memory/scripts/build_orfs_capability_retention.py \
  --attribution-report evidence/tehm-capability-attribution-orfs-r2-dev/capability_attribution_report.json \
  --output evidence/tehm-capability-retention-orfs-r2-ledger-dev/retention_report.json \
  --before-project /tmp/tehm-authority-v1/failpass-r2-retention/cases/toggle32_before_u95 \
  --after-project /tmp/tehm-authority-v1/failpass-r2-retention/cases/toggle32_after_u40 \
  --lineage-id failpass-r2-retention:toggle32-v2 \
  --config-json '{"CORE_UTILIZATION":"40"}' \
  --retention-ledger-db evidence/tehm-capability-retention-orfs-r2-ledger-dev/tehm.sqlite
```

