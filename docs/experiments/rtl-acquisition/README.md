# Experiment 1: RTL Acquisition

This directory contains the post-Pilot revised, machine-readable materials
for the first paper experiment. The experiment compares five Vanilla LLM conditions
with frozen R2G-Expander Cold. It stops at independent Sky130HD synth-only
qualification. Each method is one continuous 200-slot run with audit checkpoints at
50/100/150/200 qualified candidates. The final submission is locked before formal
evaluator output is exposed.

## Protocol Materials

- `experiment1_task_spec.json`: task, methods, scope, budgets, tools, evaluator profile,
  and current draft/frozen state.
- `experiment1_submission.schema.json`: common final-submission contract for every method.
- `experiment1_execution_manifest.schema.json`: campaign provenance and result bindings.
- `experiment1_submission.example.json`: schema/evaluator canary only; never a paper result.

The human-readable rationale and decisions are in
`docs/experiments/formal_experiment_1_2_key_design_zh.md`. A formal campaign binds every one
of these files by SHA-256. It also binds the campaign controller/evaluator, Vanilla and
R2G method runners, submission builder, route preflight, route configuration, and manifest
schema. Changing the prompt, schema, budget, runner, or evaluator therefore requires a new
campaign, including when those implementation files are not yet tracked by Git.

## Preflight And Campaign Initialization

The five Vanilla LLM routes are defined in `experiment1_model_routes.json`. Credentials
are referenced only by environment variable name and must never be committed. Before a
formal Pilot batch, run:

```bash
python3 tools/preflight_experiment1_model_routes.py \
  --routes docs/experiments/rtl-acquisition/experiment1_model_routes.json \
  --env-file ~/.config/r2g/experiment1_api.env \
  --output CAMPAIGN_ROOT/api/model_route_preflight.json
```

Every selected route must return `ready`. The canary requires the requested model to
call the common `submit_probe` tool with valid structured arguments and return
provider-reported Token usage. A successful text-only response is not sufficient.
The optional credential file must be outside the repository with mode `600`; process
environment variables take precedence over values in that file. R2G-Expander Cold starts
with an empty scheduler state and may not import repositories, candidates, prior queries,
source-yield statistics, or any other acquisition memory.

## Formal Run Sequence

1. Freeze the experiment document and task spec, a clean Agent commit, toolchain, and six
   ready model routes.
2. Initialize one campaign:

   ```bash
   python3 tools/run_experiment1_rtl_acquisition.py init-campaign \
     --campaign-root /path/to/campaign \
     --campaign-id exp1-pilot-v1 \
     --model-preflight /path/to/model_route_preflight.json
   ```

3. Run one method through one continuous acquisition session. The top-level runner
   locks its final submission but never starts the formal evaluator during acquisition:

   ```bash
   export GITHUB_TOKEN="$(gh auth token)"
   python3 tools/run_experiment1_method_campaign.py acquire \
     --campaign-root /path/to/campaign \
     --method-id openai-vanilla \
     --env-file ~/.config/r2g/experiment1_api.env \
     --cores 4
   ```

   R2G Cold uses the same command without `--env-file`. It starts one empty method frontier
   and uses no external LLM Tokens or LLM turns. Both conditions receive the same wall-time,
   CPU, GitHub-search, and synthesis-accounting policy.
   R2G first builds 2,000 synthesis-valid design families. If its public precheck yields
   fewer than 200 qualified candidates, each subsequent round requests fifteen additional
   synthesis-valid design families per missing candidate, imports the new certified
   snapshot, and gates only previously unseen candidate keys and families. It repeats under
   the same continuous budget until 200 candidates qualify, a resource limit is reached, or
   one replenishment round produces neither a new family nor a new screenable candidate.
   Formal-evaluator feedback is never part of this loop.
   Formal acquisition requires the same authenticated GitHub credential for every method.
   A campaign-wide lease serializes methods even when several tmux windows are launched at
   once, preventing shared GitHub Search quota and CPU contention from changing method scores;
   time spent waiting for this lease is outside the method budget.
   A non-scoreable infrastructure termination stops
   the method and requires a fresh, fully recorded rerun.

4. Only after the final 200-slot submission is locked, independently evaluate and create the
   fixed-denominator method report:

   ```bash
   python3 tools/run_experiment1_method_campaign.py evaluate \
     --campaign-root /path/to/campaign \
     --method-id openai-vanilla \
     --cores 4
   ```

The evaluator reclones the pinned commit, checks path safety, license evidence, recursive
header/readmem closure, and then runs the frozen Sky130HD ORFS/Yosys synthesis profile.
It does not accept a method's own success label.

License evidence is a repository-relative file path at the pinned commit, not a remote
web URL. The method must report a canonical SPDX identifier, and the evaluator reads the
local file and independently requires the observed identifier to match. This keeps license
qualification deterministic and independent of network availability during scoring.

Every Vanilla method receives the exact Token, search, and wall-time limits in its task.
The deterministic runner state reports used and remaining budget on every turn. The public
`validate_candidate` precheck uses the same deterministic gate implementation as the final
evaluator: candidate schema and pinned-source identity, active compilation-input closure,
repository-relative license evidence and canonical SPDX match, followed by the frozen
Sky130HD synthesis gates. Only a full precheck pass receives submission credit and is
checkpointed immediately. The final evaluator still reclones the pinned commit and reruns
all gates independently, so a hard stop preserves completed work without treating method-side
results as final truth.

## Primary Reporting

The paper's main result table uses fixed 200-slot denominators:

- submission completion: schema-valid, unique, in-scope locked submissions divided by 200;
- technical qualification: reproducible inputs and independent synthesis success divided by 200;
- publishable qualification: technical pass plus license, provenance, and exact-design
  uniqueness divided by 200; this is the primary success rate;
- diverse-qualified yield: effective repository count among publishable unique designs
  divided by 200.

A method may inspect more than 200 raw candidates, but it may lock at most 200.
Missing slots count as failures, within-run duplicates are rejected, and candidates cannot
be replaced after evaluator results are visible. Raw discovery counts, internal synthesis outcomes,
failure classes, diversity, and design-size strata remain mandatory audit evidence and
belong in diagnostics or the appendix. Token use, wall time, and API cost are secondary
efficiency measures rather than additional capability scores.

## Start Conditions

The formal Pilot must not start until:

- the Agent code and R2G knowledge snapshot are frozen at a clean commit;
- all five Vanilla model routes pass metadata, structured-tool, and usage preflight;
- R2G Cold starts from an empty scheduler state with no imported acquisition memory;
- the same repository-search, Git, HTTP, terminal, Yosys, and ORFS interfaces are
  available to every Vanilla LLM;
- provider-specific deep-search products and the optional R2G LLM patch path are off;
- one top-level runner completes one continuous 200-candidate run per method, writes
  50/100/150/200 audit checkpoints, and exposes no evaluator result before final locking;
- the method runner records actual model identity, all searches, resource use, and the
  hard-stop reason without human candidate replacement.

The API channel may differ by model. The paper should report the requested and returned
model IDs, official API or gateway, invocation date, reasoning setting, and provider
fingerprint when available. Credentials and private account details are not reported.
