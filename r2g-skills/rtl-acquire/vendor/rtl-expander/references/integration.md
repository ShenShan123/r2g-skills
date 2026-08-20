# RTL Expander Integration Contract

## Install

Extract or copy the complete `rtl-expander/` directory into the receiving
Codex skills directory. Keep `SKILL.md`, `agents/`, `scripts/`, `tests/`, and
`references/` together. The package contains no corpus, database, ledger,
controller, campaign contract, credentials, or production artifact.

Provide Python 3, Git, Yosys, and enough local storage for immutable repository
revisions. Install controlled VHDL/SystemVerilog frontends only when those
languages are enabled. Supply provider credentials at runtime through
`GITHUB_TOKEN` and `GITLAB_TOKEN`; never write credentials into the skill.

Run the bundled tests after installation:

```bash
python3 -m unittest discover -s /path/to/rtl-expander/tests -p 'test_*.py'
```

## Start a caller-defined campaign

Choose a fresh corpus root and an objective ID that identifies the caller's
campaign. Both the target and objective are required parameters; the skill has
no fixed Family target.

```bash
python3 /path/to/rtl-expander/scripts/run_until_family_target.py \
  --corpus-root /path/to/rtl_corpus \
  --objective-id design-family-20k \
  --target-global-design-families 20000 \
  --revision-batch 2000
```

Set `--max-revision-batch` when the requested micro-cohort ceiling should
exceed its conservative default. Run the command under tmux or a service
manager for host-independent persistence. Restarting the identical command
resumes the same objective and child round.

Create a separate `CAMPAIGN_INTERNAL` consumption contract for that objective
only when automatic test-profile rollover is desired and the caller can
explicitly assert that no intermediate split has been consumed by training or
formal evaluation. Never reuse another campaign's contract.

## Downstream output boundary

Consume only a `CERTIFIED` snapshot under `<corpus-root>/snapshots/<id>/`.
Backend flows should use snapshot DesignInstances and their source closure,
top, per-file language, provenance, generic-synthesis evidence, DesignFamily,
SplitGroup, quality, license, and release metadata.

Treat frontier state, processing queues, child batches, attempts, staging
artifacts, reconciliation proposals, controller files, and mutable manifests as
factory internals. Do not couple a downstream mapping, floorplanning,
placement, CTS, routing, or GDS flow to those internal records.

## Validation provenance

The implementation has completed a certified production campaign with 33,707
immutable RepositoryRevisions, 13,466 Formal DesignInstances, and 10,606 Formal
DesignFamilies. This is validation evidence only. It is not included runtime
state and does not constrain a caller's target.
