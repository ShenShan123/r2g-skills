# R2G vs bare agent: comparison protocol

Question: for the same orchestration model, how much does R2G (diagnosis,
recipe library, knowledge base, automatic repair loop) save in pass rate,
ORFS runs, signoff runs and tokens, against an agent given the same EDA tools
without that orchestration?

## Arms

| Arm | What the model does | What runs the physical work |
|---|---|---|
| Baseline | nothing (no LLM) | the frozen-spec corpus flow, one run per design |
| Model + R2G | dispatches the R2G Agent job (`output=signoff`), then explains the result | R2G pipeline: flow, signoff, diagnosis, recipes, repair loop |
| Model (bare) | everything: reads logs, edits `config.mk`, writes Tcl hooks, reruns | the same ORFS and signoff scripts, invoked by the model through `bare_tools.py` |

Same model, model client, retry layer and usage counters in both LLM arms
(first round: deepseek-flash, thinking on, 4096-token responses; second
round: deepseek-v4-pro). One frozen runtime commit per round
(`runtime_commit.json`), deployed byte-identically on every host.

## Sample

Stratified by the repair class R2G needed, fixed seed 20261029
(`sample.json`): Nangate45 24 (antenna 10, front-end 7, flow compatibility 3,
already clean 2, unresolved 2), SKY130 21 (LVS feedthrough 7, front-end 7, flow
compatibility 3, already clean 2, unresolved 2). GF180 is drawn once its
baseline LVS defines a repair pool. All sampled designs are baseline failures.

## Starting state of the bare arm

The original RTL (hash-checked against the population manifest) plus the
historical baseline `config.mk` and `constraint.sdc` (`baselines/`, hashes in
`baselines_sha256.json`), with file paths remapped into the project. This is
the exact state the historical baseline failed in. It hands the bare arm the
baseline's die sizing for free, which R2G computes itself: a bias against R2G,
so the comparison is conservative.

## Rules enforced by the harness, not the prompt

- Writes only under the project's `constraints/`, `rtl/`, `hooks/`; the PDK,
  ORFS platform files and rule decks are read-only (re-hashed at the end).
- The R2G skill tree (recipes, knowledge base, docs) is outside every readable root.
- `constraint.sdc` must be byte-identical at the end (no clock or I/O relaxation).
- Every RTL write is journaled; a design passes automatically only with no RTL
  change, otherwise it goes to a semantics review (same rule R2G was held to).

## Budgets per design (both arms, same values)

100 model calls, 5 CNY at deepseek-flash peak prices, 10 flow runs, 16 signoff
runs, 24 h wall. A design that hits a cap is a failure and keeps its full cost.
Total tokens are reported, never capped.

Harness v1 capped total tokens (2M, cache reads included) and kept every tool
output in the conversation. Its bare runs spent ~94% of their tokens re-sending
context and stopped after one flow run: a naive loop, not a competent agent.
They are archived as `harness_v1_naive/` and excluded from the comparison.

## Context management (bare arm)

When the full-length tool results in the conversation exceed 120k characters,
all but the newest four are replaced at once by a stub (tool, arguments, first
line of the result); the model can re-read anything it needs. Compacting in
blocks keeps the prompt prefix stable between compactions so the provider's
cache stays effective. The R2G arm's conversations (~6 calls) never reach it.

## Outcome

Pass = full-deck DRC clean and LVS clean, both bound to the same final GDS
(run tag and digest), decks and SDC unchanged, no unreviewed RTL change. The
harness grades the final layout itself when the model's last signoff did not;
that verification is journaled under a separate job id and is not agent cost.

## Metrics

Per design including failures, and per pass: pass rate; ORFS runs (`run_orfs`
wrapper calls); top-level make launches; signoff runs; model calls; provider
tokens (input, cache reads as an input subset, output, reasoning); transport
retries; agent wall time and EDA time. Tables: `COMPARISON_TABLES.md`.

## Attempts

One attempt per design and arm. An attempt voided by an invocation failure
(transport error, incomplete provider usage) gets one fresh attempt; voided
attempts are kept and reported. Harness defects found during the run are
fixed, recorded, and the affected designs re-run from scratch.
