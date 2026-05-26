# Specification Template

Normalize user requirements into the following structure before generating RTL.

```yaml
design_name:
top_module:
description:
inputs:
  - name:
    width:
    desc:
outputs:
  - name:
    width:
    desc:
clock:
  name:
  edge: posedge
reset:
  name:
  active_level:
  sync_or_async:
functional_requirements:
  -
timing_target:
target_flow: orfs
target_platform: nangate45
verification_targets:
  - reset behavior
  - normal operation
  - corner cases
signoff_targets:
  - drc
  - lvs       # only on platforms with LVS rules (sky130hd, ihp-sg13g2)
  - rcx        # parasitic extraction (SPEF)
assumptions:
  - single clock domain
  - no CDC
```

## Minimum Required Fields

Before running synthesis or backend steps, ensure the following fields exist:

- `design_name`
- `top_module`
- Interface list (inputs and outputs)
- Clock definition
- Reset definition, or an explicit note if no reset exists
- Target platform (`nangate45`, `sky130hd`, `sky130hs`, `asap7`, `gf180`, `ihp-sg13g2`)

## Ask Before Guessing

If any of the following are missing, ask the user or document assumptions clearly:

- Bus widths
- Valid/ready handshake semantics
- Reset polarity
- Timing targets (clock period in ns)
- Pipeline depth
- Whether area or frequency is the primary optimization target

## Signoff Considerations

When normalizing the spec, note signoff requirements:

- **DRC**: Always run after backend. Available on nangate45, sky130hd, asap7, ihp-sg13g2.
- **LVS**: Only available on sky130hd and ihp-sg13g2. Gracefully skipped on other platforms.
- **RCX**: Available on all platforms with `rcx_patterns.rules`. Produces SPEF for post-route timing analysis.
- If the user requires parasitic-aware timing (e.g., for tapeout), specify `rcx` in signoff_targets and use SPEF for STA.
