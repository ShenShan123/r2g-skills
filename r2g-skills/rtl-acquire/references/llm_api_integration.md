# LLM API Integration

The main workflow should not call a live LLM API directly inside `run_expansion_round.py`.

Use a 3-step contract instead:

1. Build repair cases
   - `scripts/repair/build_llm_repair_cases.py`
   - outputs `llm_repair_cases.json/.jsonl/.md`

2. Build API-ready patch requests
   - `scripts/repair/build_llm_patch_requests.py`
   - outputs `llm_patch_requests.json/.jsonl/.md`
   - enforces an approximate `max_tokens_per_design` budget
   - skips over-budget requests when policy says to do so

3. Run an external API executor, then validate returned results
   - write model outputs to `llm_patch_results.jsonl`
   - validate with `scripts/repair/evaluate_llm_patch_results.py`
   - evaluator performs patch-minimality analysis against the target source when possible
   - large patches can be warned or rejected based on failure class and policy

An OpenAI-specific executor is now available:

- `scripts/repair/call_openai_llm_patch_api.py`

A local agent executor is also available:

- `scripts/repair/run_local_llm_patch_agent.py`

## Why This Split Exists

This keeps:

- production orchestration deterministic
- network/API choice outside the core loop
- provider choice swappable
- patch execution auditable

## Minimal Result Schema

Each API result should be one JSON object with:

- `request_id`
- `design`
- `decision`
  - `diagnosis_only`
  - `unified_diff_patch`
  - `reject`
- `confidence`
- `summary`
- `patch_unified_diff`
- `notes`

## Patch Minimality

Returned patches should be conservative.

- keep the diff localized
- avoid full-file rewrites for parser/include issues
- prefer `diagnosis_only` when a fix would require broad logic churn

`evaluate_llm_patch_results.py` now analyzes unified diffs against the source file when it can resolve the target path. It computes:

- changed logic lines in the diff
- logic lines in the target file
- change ratio

Policy knobs live in `references/llm_repair_policy.json`:

- `max_logic_change_ratio`
- `reject_large_patch_failure_classes`
- `warn_large_patch_failure_classes`

This is intended to catch cases like “rewrote a large fraction of the module to fix a missing include”.

## Token / Cost Budget

LLM patch requests now carry a budget hint:

- `estimated_prompt_tokens`
- `max_tokens_per_design`
- `approx_chars_per_token`

The request builder uses `references/llm_repair_policy.json`:

- `max_tokens_per_design`
- `approx_chars_per_token`
- `skip_if_estimated_tokens_exceed_budget`

This is a coarse but practical guardrail so obviously hopeless designs do not consume unbounded LLM time.

## Patch Feedback Loop

Validated successful LLM patches can be mined into reusable rule candidates:

- `scripts/repair/mine_llm_patch_rule_candidates.py`
- outputs `llm_patch_rule_candidates.json/.md`

`refresh_failure_knowledge_base.py` now injects these into the auto-generated KB section as promotion candidates. They are not treated as unconditional rules by default.

## OpenAI API Setup

Required environment variables:

- `OPENAI_API_KEY`

Optional:

- `OPENAI_BASE_URL`
  - defaults to `https://api.openai.com/v1`
- `OPENAI_MODEL`
  - defaults to `gpt-5.2`

Example:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-5.2
```

Then:

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/repair/call_openai_llm_patch_api.py \
  --requests-jsonl $HOME/work/data/nangate45_graph_expansion_workspace/failures/llm_patch_requests.jsonl \
  --results-jsonl $HOME/work/data/nangate45_graph_expansion_workspace/failures/llm_patch_results.jsonl \
  --max-requests 10
```

Then validate:

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/repair/evaluate_llm_patch_results.py
```

## Local Agent Path

The preferred path for this skill is often the local agent rather than a remote API.

Example:

```bash
/path/to/python $CODEX_HOME/skills/nangate45-graph-expander/scripts/repair/run_local_llm_patch_agent.py \
  --requests-jsonl $HOME/work/data/nangate45_graph_expansion_workspace/failures/llm_patch_requests.jsonl \
  --results-jsonl $HOME/work/data/nangate45_graph_expansion_workspace/failures/llm_patch_results.jsonl \
  --max-requests 5
```

This uses local `codex exec`, not a network API.

## What Is Not Online Yet

This skill does not yet:

- apply returned patches automatically
- auto-promote LLM patches into publishable data

Those should only be added after:

- patch-result validation
- patch application sandboxing
- post-patch RTL/frontend/graph validation
