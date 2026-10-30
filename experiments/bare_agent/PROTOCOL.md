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
(first round: deepseek-flash, thinking on, 32768-token responses; second
round: deepseek-v4-pro). The repair-pool R2G results reused here were measured
at 4096; R2G's ~6 calls per design never hit that limit, and a smaller limit can
only hurt R2G, so reusing them keeps the comparison conservative. Pilot v2 ran
the bare arm at 4096: thinking alone used the whole reply on hard steps, the
model returned nothing and the attempt ended `model_stopped` (6 of 18 designs). One frozen runtime commit per round
(`runtime_commit.json`), deployed byte-identically on every host.

## Sample

Stratified by the repair class R2G needed, fixed seed 20261029
(`sample.json`): Nangate45 24 (antenna 10, front-end 7, flow compatibility 3,
already clean 2, unresolved 2), SKY130 21 (LVS feedthrough 7, front-end 7, flow
compatibility 3, already clean 2, unresolved 2). GF180 is drawn once its
baseline LVS defines a repair pool. All sampled designs are baseline failures.

Full run (harness v2.1): every frozen-baseline failure, N45 327 and SKY130 128.
33 N45 outside-pool designs leave the cohort for **both** arms: none of their
RTL is in the corpus (every baseline source path is missing), so there is nothing
to repair (`cohort_exclusions.json`). Cohort: N45 294 (148 pool + 146 outside),
SKY130 128.

**Baseline timeouts are out of scope for both arms** (user decision 2026-10-29,
the same rule as for GF180): 139 designs whose frozen baseline failed by a
timeout (85 DRC, 45 flow stage, 9 LVS; 107 N45, 32 SKY130;
`timeout_exclusions.json`) are reported as unfixable for every configuration.
They stay in the 1,249 denominator as failures; the comparison of repair rate
and cost is over the remaining non-timeout failures, for R2G and bare alike.
Running bare attempts on them were stopped and kept under `excluded_timeout/`.
Baseline **memory exhaustion** is treated the same way (8 designs: the four
FPGA-fabric tiles C01031/C01769/C03200/C03320 on both platforms; GF180's
equivalents are unfixable too), and every EDA step of the bare arm runs under a
64 GB memory bound (cgroup scope; podman --memory on 216) after one orphaned
floorplan held 172 GB on 203. 147 designs are out of scope in all.
(Stopping them by session on 203 and 208 also stopped non-timeout runners that
shared a launcher's session; those were filed under `interrupted/` and re-run
from scratch, as the attempt rule below prescribes.)

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
- Every RTL write is journaled. RTL edits go through a semantics review (same
  rule R2G was held to). Its automatic part (`semantic_review`) clears, and
  records why: `rtl/design.json` (a description the flow never reads); line
  endings, trailing whitespace and a final newline; a file added as a copy of an
  original, or an original moved elsewhere, line for line; edits confined to
  double-quoted path literals (`include`, `$readmemh`). Anything else is
  `pending_semantic_review` for a human. All four pilot-v2 pending cases were
  location fixes of these kinds.

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
attempts are kept under `voided/` and reported. A second incomplete attempt is
graded as it stands, with its usage reported as incomplete.

Inside an attempt, a failed model invocation (relay or provider down, stream
cut) is re-sent with backoff (30 s doubling to 5 min, 30 min in all) instead of
ending the attempt; failed invocations are kept apart from the model calls and
reported. Added in v2.2 after the 2026-10-29 05:20-05:35 relay outage (a pooled
connection leak in the relay, fixed the same day) ended 38 attempts on their
first timeout, five of them already dual-clean. Those 38 designs are a harness
defect: their attempts are kept under `harness_defect/` and the designs re-run
from scratch on v2.2.

Same day, ~06:00-06:45: 203's `/home/yangao` (a 7.3 TB disk shared with other
users) reached 0 bytes free and every flow failed with ENOSPC. All 40 finished
attempts that hit it and the 10 interrupted with it are harness defects
(`bare_v21_home_ENOSPC_harness_defect/` on 203) and re-run from scratch. 203
moved to `/data`: work root `/data/yangao/r2g_vs_bare_work/bare_v21`, ORFS
workspace in a shadow root `/data/yangao/orfs203` (results/logs/objects/reports/
designs real, every other entry a symlink to the checkout), harness v2.3 (the
sandbox resolves its roots like the paths it checks; no other change). Harness defects found during the run are
fixed, recorded, and the affected designs re-run from scratch.

## Memory: independent designs first, a memory arm second

R2G is itself a memory: its recipes and knowledge store are past repairs. And
all 455 failures in this comparison are **dev-split** designs
(`experiments/splits/`), the split those recipes were developed on (with
Claude meta-repair), so on this population R2G answers from its own training
set. A bare arm that forgets everything between designs therefore measures
"R2G's tools and memory vs neither", not "R2G vs a capable agent".

Two bare arms answer the two questions separately:

1. **Bare, independent** (running now). Each design starts from nothing. The
   zero-shot floor; designs are exchangeable, so they run in parallel and the
   pass rate has an ordinary binomial interval.
2. **Bare + self-memory** (after arm 1). The same harness plus one tool,
   `search_notes(query)`, over notes from the arm's own earlier designs on the
   same platform. Rules:
   - A note is written after each design closes, by one extra model call that
     sees its transcript. Its header is written by the harness, not the model:
     platform, the harness verdict, final DRC/LVS counts, and the `config.mk` /
     hook diff. The model cannot record a success it did not get (the same
     firewall as journal vs knowledge in R2G). The note call is charged to the
     arm.
   - Waves: designs in a fixed seeded order, waves of 64; a wave reads only the
     notes of earlier waves, and runs in parallel inside. Order and wave size
     are fixed before the run.
   - Reported: pass rate and cost per wave (the learning curve), and paired
     against arm 1 on the same designs.

The comparison that is out of sample for everyone needs **held-out** failures:
R2G frozen, the memory arm with its notes frozen after dev, and arm 1, on the
held-out designs' frozen-spec baseline failures. That is the headline result;
the dev-split numbers are reported as in-sample for R2G.
