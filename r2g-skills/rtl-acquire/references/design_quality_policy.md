# Design Quality Policy

This file documents how the skill interprets design-level quality signals.

It is not the implementation itself. The goal is to keep the policy legible for future tuning and handoff.

## Main Signals

- `graph_complexity_score`
  - structural richness of the generated graph

- `rare_cell_share`
  - whether the design contributes uncommon cell usage

- `design_novelty_score`
  - whether the design broadens the current dataset distribution

- `design_redundancy_score`
  - whether the design is too similar to already-kept samples

- `degraded_quality`
  - whether fallback or repair likely changed semantics materially

- `fix_ratio`
  - amount of repair/stub intervention relative to recovered logic

## Policy Outcome

- `keep`
  - structurally useful and sufficiently faithful

- `conditional`
  - usable, but should be reviewed in context of current dataset mix or repair cost

- `reject`
  - low-value, too redundant, too degraded, or too dependent on artificial repair

## Practical Reading

- large alone is not enough
  - very large but repetitive or low-fidelity designs should be downgraded

- success alone is not enough
  - repaired designs with high `fix_ratio` can still be poor training data

- novelty matters
  - medium designs with uncommon structure can be more valuable than another redundant large design

## Intended Use

- guide retry prioritization
- support repo/design quality reporting
- explain why a sample was kept, quarantined, or rejected
