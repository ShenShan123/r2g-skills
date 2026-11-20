#!/usr/bin/env python3
"""Exp 4 (conversion), LLM arm, DEVELOPMENT phase: one model writes and debugs ONE converter.

Host side (memlab216). Starts a fresh container (`--network none`) from the conversion image
(ORFS 26Q3 tools + Python 3.13 with numpy/pandas/torch/torch_geometric). Mounted read-only:
the 6 development designs (only 5_route.def, 6_final.spef, constraint.sdc each), the
Nangate45 platform files and the public spec (CONTRACT.md + check_format.py). Writable:
/work. Nothing else: no R2G repository, no def-graph code, no prebuilt dataset, no other
design. The model gets two tools, `shell` (bash in the container) and `finish`. Model calls
are made from this host process, never from inside the container.

Budgets (PROTOCOL.md section 5): 100 model calls, 12 h wall, temperature 0.2 (sent to
DeepSeek; the GPT Responses route rejects a temperature, see PROTOCOL.md), max output tokens
32768, no cost cap. Context management, tool-result clipping, retry/backoff and the
truncated-reply nudge are copied from the Exp 3 bare-agent harness (bare_agent/run_bare.py).

At the end the harness copies /work/converter out of the container, tars it
deterministically, records its sha256 and makes the copy read-only: that tarball is the
frozen converter. Outputs under --out: meta.json, transcript.json, usage.json (every call's
provider usage + failed invocations), commands.jsonl, result.json, converter.tar(+.sha256).

  run_dev_agent.py --out DIR --model deepseek-flash --protocol openai --base-url http://127.0.0.1:18774/v1
  run_dev_agent.py --out DIR --model gpt-6.1-sol --protocol openai-responses --base-url http://127.0.0.1:18775/v1
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

COUNTERS = ("input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens",
            "uncached_input_tokens", "cache_creation_input_tokens", "reasoning_tokens")
CNY_PER_USD = 7.10
PRICES = {  # per 1M tokens
    "deepseek-flash": {"currency": "CNY", "uncached": 2.0, "cached": 0.04, "output": 8.0},
    "gpt-6.1-sol": {"currency": "USD", "uncached": 2.00, "cached": 0.10, "output": 10.00},
}
COMPACT_AT_CHARS = 120_000      # run_bare.py
KEEP_RECENT = 4
MAX_TOOL_RESULT_CHARS = 12000
INVOCATION_WAIT = 1800
MAX_NUDGES = 3
SHELL_TIMEOUT_DEFAULT, SHELL_TIMEOUT_MAX = 600, 3600

SYSTEM = ("You are an expert EDA software engineer. You work only through the shell tool inside a "
          "Linux container. Read the specification carefully, inspect the input files, write code, "
          "test it, and fix it until it is correct.")

TASK = """Write a converter that turns a routed physical design into two PyTorch-Geometric graphs, exactly as
specified in /data/spec/CONTRACT.md (reproduced in full at the end of this message). Follow it to the letter.

Environment (container, no network):
- /data/dev/<design>/ : {n_dev} development designs, each with 5_route.def, 6_final.spef, constraint.sdc (read-only)
- /data/platform/     : NangateOpenCellLibrary.tech.lef, NangateOpenCellLibrary.macro.mod.lef,
                        NangateOpenCellLibrary_typical.lib (read-only)
- /data/spec/         : CONTRACT.md (the specification) and check_format.py (read-only)
- /work/              : your writable work directory
- Tools: python3 (3.13, numpy, pandas, torch, torch_geometric), openroad (with OpenSTA), sta, yosys, tclsh.

Deliverable: /work/converter/run.sh, invoked as
    bash /work/converter/run.sh <design_dir> <platform_dir> <out_dir>
It must write <out_dir>/b_graph.pt and <out_dir>/c_graph.pt. Put everything the converter needs
inside /work/converter/ (scripts, helpers): after you finish, ONLY that directory is copied, frozen and
run by itself, read-only, with no network and no model, on other designs of the same kind (up to ~35,000
cells and ~70 MB of DEF), 3,600 s per design. Write temporary files only under <out_dir> or /tmp.

Feedback: `python3 /data/spec/check_format.py <out_dir>` checks the FORMAT of an output (shapes, dtypes,
required fields, layout, NaN placement). It does not know the correct values; correctness is judged later
against independently computed ground truth, so verify your values against the specification yourself.

Budget: at most {max_calls} model calls (each of your replies is one call) and {wall_h:g} hours in total.
Older tool outputs are replaced by short stubs to keep the context small; re-read files when you need them.
Test the converter on all development designs. When it is finished (or you cannot improve it further),
call finish with a short summary.

==================== /data/spec/CONTRACT.md ====================
{contract}"""

TASK_IMAGE = """Write a converter that turns a placed-and-routed physical design into GCell-raster images (NumPy arrays),
exactly as specified in /data/spec/CONTRACT_IMAGE.md (reproduced in full at the end of this message). Follow it to
the letter.

Environment (container, no network):
- /data/dev/<design>/ : {n_dev} development designs, each with 3_place.def and 5_route.def (read-only)
- /data/platform/     : NangateOpenCellLibrary.tech.lef, NangateOpenCellLibrary.macro.mod.lef,
                        NangateOpenCellLibrary_typical.lib (read-only)
- /data/spec/         : CONTRACT_IMAGE.md (the specification) and check_format_image.py (read-only)
- /work/              : your writable work directory
- Tools: python3 (3.13, numpy, pandas, torch, torch_geometric), openroad (with OpenSTA), sta, yosys, tclsh.

Deliverable: /work/converter/run.sh, invoked as
    bash /work/converter/run.sh <design_dir> <platform_dir> <out_dir>
It must write <out_dir>/features.npz and <out_dir>/labels.npz. Put everything the converter needs inside
/work/converter/ (scripts, helpers): after you finish, ONLY that directory is copied, frozen and run by itself,
read-only, with no network and no model, on other designs of the same kind (up to ~35,000 cells and ~70 MB of
DEF), 3,600 s per design. Write temporary files only under <out_dir> or /tmp.

Feedback: `python3 /data/spec/check_format_image.py <out_dir> <design_dir>` checks the FORMAT of an output (array
names, dtype, shape, finiteness). It does not know the correct values; correctness is judged later against
independently computed ground truth, so verify your values against the specification yourself.

Budget: at most {max_calls} model calls (each of your replies is one call) and {wall_h:g} hours in total.
Older tool outputs are replaced by short stubs to keep the context small; re-read files when you need them.
Test the converter on all development designs. When it is finished (or you cannot improve it further),
call finish with a short summary.

==================== /data/spec/CONTRACT_IMAGE.md ====================
{contract}"""

TASK_FULL = """Run seed: {seed}

Write ONE converter that turns a signed-off physical design into the FULL dataset product of the
"def-graph" tool, as its own documentation describes it. The documentation is in /data/spec/docs/
(SKILL.md, graph-dataset.md, feature-extraction.md, label-extraction.md, four-stage-dataset.md,
B_VIEW_DATASET_STRUCTURE.md). References to the tool's own scripts were removed from it; you do not have
that tool and must build the converter yourself. Read the documentation carefully: the definitions,
node/edge sets, orderings, units, label transforms and NaN conventions it states are what is required.

Products required (all from the same design inputs):
1. Graph views b, c, d, e, f -- BOTH forms the tool writes: heterogeneous HeteroData `<v>_graph.pt` and
   homogeneous Data `<v>_graph_homo.pt` -- with node features, folded-entity edge features/labels
   (interleaved forward/reverse), labels congestion, wirelength, timing (with raw twins) and RC parasitics
   from the SPEF (ground capacitance as node label; coupling capacitance and equivalent resistance as the
   separate rc_edge_* edge set).
2. The four-stage causal graphs (floorplan / placement / cts / route HeteroData on the 1_2_yosys.v canonical
   topology) -- ONLY when the design directory contains 1_2_yosys.v (Nangate45 designs).
3. GCell images: images/features.npz with cell_density, macro_region, rudy, pin_rudy, pin_density (from
   3_place.def) and images/labels.npz with congestion, congestion_raw (the documented image product).
EXCLUDED: power -- IR drop and every power feature/label. Their slots may hold anything (NaN is fine);
they are never evaluated. netlist_graph.pt and manifest/metadata files are not required.

Interface:  bash /work/converter/run.sh <design_dir> <platform_dir> <out_dir>
- <design_dir> (read-only): config.mk, constraint.sdc, 6_final.sdc, 6_final.def (final routed layout),
  6_final.spef, 3_place.def, and for Nangate45 also 1_2_yosys.v, 2_floorplan.def, 4_cts.def, 5_route.def.
- <platform_dir>: the OpenROAD-flow-scripts platform directory of the design's PLATFORM (config.mk):
  /r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/{{nangate45,sky130hd,gf180}}. The converter will be run
  on designs of ALL THREE platforms; you only have Nangate45 designs here, but the other platform
  directories are available to read.
- <out_dir> must receive:
    dataset/{{b,c,d,e,f}}_graph.pt, dataset/{{b,c,d,e,f}}_graph_homo.pt
    four_stage/generated/stages/{{floorplan,placement,cts,route}}/heterograph.pt   (Nangate45 only)
    images/features.npz, images/labels.npz

Environment (container, no network): /data/dev/<design>/ = {n_dev} Nangate45 development designs (read-only);
/data/spec/ = docs/ and check_format_v2.py (read-only); /work/ writable. Tools: python3 (3.13, numpy, pandas,
torch, torch_geometric), openroad (with OpenSTA), sta, yosys, tclsh.

Deliverable: /work/converter/run.sh (+ any helper files inside /work/converter/). After you finish ONLY that
directory is copied, frozen and run by itself, read-only, no network, no model, on 90 unseen designs
(Nangate45, SKY130HD, GF180; up to ~35,000 cells), 3,600 s per design for all products together. Write
temporary files only under <out_dir> or /tmp.

Feedback: `python3 /data/spec/check_format_v2.py <out_dir> <design_dir>` checks the FORMAT of an output per
product (loadability, keys, shapes, dtypes, layout, NaN conventions). It does not know the correct values;
correctness is judged later against independently computed ground truth.

Budget: at most {max_calls} model calls (each of your replies is one call) and {wall_h:g} hours in total.
Older tool outputs are replaced by short stubs to keep the context small; re-read files when you need them.
When the converter is finished (or you cannot improve it further), call finish with a short summary."""

SPECS = [
    {"name": "shell", "description": "Run a bash command in the container (cwd /work). Returns exit code, "
     "seconds, and stdout+stderr (outputs over 11,000 characters keep the first and last 5,000).",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string"},
         "timeout_s": {"type": "integer", "description": f"default {SHELL_TIMEOUT_DEFAULT}, max {SHELL_TIMEOUT_MAX}"}},
         "required": ["command"]}},
    {"name": "finish", "description": "Declare the converter finished and summarise it.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]


def podman(*a, **kw):
    return subprocess.run(["podman", *a], capture_output=True, text=True, errors="replace", **kw)


def call_cost_usd(c: dict, model: str) -> float:
    p = PRICES[model]
    # every input token not read from cache is billed at the input price (cache WRITES too:
    # OpenRouter reports them separately for GPT, list price charges them as plain input)
    cached = c.get("cache_read_input_tokens") or 0
    if c.get("input_tokens") is not None:
        unc = c["input_tokens"] - cached
    else:
        unc = c.get("uncached_input_tokens") or 0
    v = (unc * p["uncached"] + cached * p["cached"] + (c.get("output_tokens") or 0) * p["output"]) / 1e6
    return v / CNY_PER_USD if p["currency"] == "CNY" else v


def compact(messages: list, transcript: list) -> bool:
    full = [i for i, m in enumerate(messages) if m["role"] == "tool" and not m.get("_compacted")]
    if sum(len(messages[i]["content"]) for i in full) <= COMPACT_AT_CHARS or len(full) <= KEEP_RECENT:
        return False
    args = {tc["id"]: tc for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []}
    for i in full[:-KEEP_RECENT]:
        m = messages[i]
        a = json.dumps((args.get(m["tool_call_id"]) or {}).get("arguments", {}), ensure_ascii=False)[:160]
        head = m["content"][:240].replace("\n", " ")
        m["content"] = "[older tool result compacted to save context; re-run to see it] %s %s -> %s ..." % (m.get("name"), a, head)
        m["_compacted"] = True
    transcript.append({"harness_compaction": len(full) - KEEP_RECENT})
    return True


def clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TOOL_RESULT_CHARS else s[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"


def tar_dir_deterministic(src: Path, dst: Path) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for p in sorted(src.rglob("*")):
            if p.is_symlink() or not (p.is_file() or p.is_dir()):
                continue
            ti = tf.gettarinfo(str(p), arcname="converter/" + str(p.relative_to(src)))
            ti.uid = ti.gid = 0; ti.uname = ti.gname = ""; ti.mtime = 0
            ti.mode = 0o755 if (p.is_dir() or os.access(p, os.X_OK)) else 0o644
            if p.is_file():
                with open(p, "rb") as fh:
                    tf.addfile(ti, fh)
            else:
                tf.addfile(ti)
    dst.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--task", choices=["graph", "image", "full"], default="graph")
    ap.add_argument("--seed", type=int, default=0, help="v2 run seed (stated in the task header, recorded)")
    ap.add_argument("--image", default="localhost/exp4c-conv:1")
    ap.add_argument("--dev-root", type=Path, required=True, help="dir of <design>/{5_route.def,6_final.spef,constraint.sdc}")
    ap.add_argument("--platform", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True, help="dir with CONTRACT.md + check_format.py")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True, choices=sorted(PRICES))
    ap.add_argument("--protocol", required=True, choices=["openai", "openai-responses"])
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--max-calls", type=int, default=100)
    ap.add_argument("--wall-h", type=float, default=12.0)
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--memory", default="64g")
    ap.add_argument("--cpus", default="8")
    ap.add_argument("--agent-src", type=Path, required=True, help="dir containing the r2g_agent package")
    a = ap.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        sys.exit("already done: %s" % out)
    sys.path.insert(0, str(a.agent_src))
    from r2g_agent.config import ModelConfig
    from r2g_agent.llm.openai_compat import OpenAICompatAdapter
    from r2g_agent.llm.openai_responses import OpenAIResponsesAdapter
    key = os.environ.setdefault("R2G_LOCAL_RELAY_AUTH", "local-relay-only-not-a-provider-key")
    mc = ModelConfig(provider="deepseek" if a.protocol == "openai" else "openai", model=a.model, base_url=a.base_url,
                     protocol=a.protocol, api_key_env="R2G_LOCAL_RELAY_AUTH", thinking=True,
                     max_tokens=a.max_tokens, temperature=a.temperature).resolved()
    adapter = (OpenAIResponsesAdapter(mc, key, a.reasoning_effort) if a.protocol == "openai-responses"
               else OpenAICompatAdapter(mc, key))
    work = out / "work"
    work.mkdir(exist_ok=True)
    designs = sorted(p.name for p in a.dev_root.iterdir() if p.is_dir())
    name = "exp4c_dev%s_%s_%s" % ({"graph": "", "image": "img", "full": "v2s%d" % a.seed}[a.task], a.model.replace(".", "").replace("-", ""), uuid.uuid4().hex[:6])
    r = podman("run", "-d", "--name", name, "--network", "none", "--memory", a.memory, "--cpus", a.cpus,
               "-v", f"{a.dev_root.resolve()}:/data/dev:ro,Z", "-v", f"{a.platform.resolve()}:/data/platform:ro,Z",
               "-v", f"{a.spec.resolve()}:/data/spec:ro,Z", "-v", f"{work}:/work:Z", "-w", "/work",
               # 216 is rootless cgroups v1: --memory/--cpus are ignored there, so cap BLAS/torch threads
               "-e", f"OMP_NUM_THREADS={a.cpus}", "-e", f"MKL_NUM_THREADS={a.cpus}", "-e", f"OPENBLAS_NUM_THREADS={a.cpus}",
               a.image, "sleep", "infinity")
    if r.returncode:
        sys.exit("container start failed: " + r.stderr)
    meta = {"container": name, "image": a.image, "image_id": podman("image", "inspect", a.image, "--format", "{{.Id}}").stdout.strip(),
            "task": a.task, "seed": a.seed, "model": a.model, "protocol": a.protocol, "reasoning_effort": a.reasoning_effort, "base_url": a.base_url,
            "max_calls": a.max_calls, "wall_h": a.wall_h, "max_tokens": a.max_tokens, "temperature": a.temperature,
            "temperature_sent": a.protocol == "openai", "dev_designs": designs, "started": time.time(),
            "spec_sha256": {str(p.relative_to(a.spec)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(a.spec.rglob("*")) if p.is_file()}}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    if a.task == "full":
        task = TASK_FULL.format(seed=a.seed, n_dev=len(designs), max_calls=a.max_calls, wall_h=a.wall_h)
    else:
        tmpl, cfile = (TASK, "CONTRACT.md") if a.task == "graph" else (TASK_IMAGE, "CONTRACT_IMAGE.md")
        task = tmpl.format(n_dev=len(designs), max_calls=a.max_calls, wall_h=a.wall_h,
                           contract=(a.spec / cfile).read_text())
    messages = [{"role": "user", "content": task}]
    transcript, calls, failed = [{"task": task, "system": SYSTEM}], [], []
    cmdlog = (out / "commands.jsonl").open("a")
    t0, stop, summary, nudges = time.monotonic(), None, None, 0
    while stop is None:
        if len(calls) >= a.max_calls:
            stop = "cap_model_calls"; break
        compact(messages, transcript)
        if time.monotonic() - t0 >= a.wall_h * 3600:
            stop = "cap_wall"; break
        text, tool_calls, usage, err, stop_reason = "", [], None, None, None
        wire = [{k: v for k, v in m.items() if k != "_compacted"} for m in messages]
        for ev in adapter.stream(wire, SPECS, SYSTEM):
            if ev["type"] == "text":
                text += ev["delta"]
            elif ev["type"] == "tool_call":
                tool_calls.append(ev)
            elif ev["type"] == "usage":
                usage = ev
            elif ev["type"] == "error":
                err = ev["message"]
            elif ev["type"] == "done":
                stop_reason = ev.get("stop_reason")
        if err:
            failed.append({**(usage or {"usage_reported": False, "stream_complete": False}), "error": err,
                           "partial_output": bool(text or tool_calls), "at": time.time()})
            transcript.append({"model_error": err})
            waited = sum(f.get("backoff", 0) for f in failed[:-1])
            if waited >= INVOCATION_WAIT:
                stop = "model_error"; break
            failed[-1]["backoff"] = min(30 * 2 ** (len(failed) - 1), 300, INVOCATION_WAIT - waited)
            time.sleep(failed[-1]["backoff"])
            continue
        calls.append({**(usage or {"usage_reported": False, "stream_complete": False}),
                      "call_index": len(calls) + 1, "at": time.time()})
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in tool_calls]})
        transcript.append({"call": len(calls), "assistant": text,
                           "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in tool_calls]})
        if not tool_calls:
            if (stop_reason == "length" or not text.strip()) and nudges < MAX_NUDGES:
                nudges += 1
                note = ("(Your previous reply hit the length limit without a tool call. Call a tool directly, "
                        "or call finish.)" if stop_reason == "length" else
                        "(Your previous reply was empty. Call a tool directly, or call finish.)")
                messages.append({"role": "user", "content": note})
                transcript.append({"harness_nudge": note})
                continue
            stop = "model_stopped"; break
        nudges = 0
        for c in tool_calls:
            args = c["arguments"] if isinstance(c["arguments"], dict) else {}
            if c["name"] == "finish":
                stop, summary, res = "finished", str(args.get("summary", "")), {"ok": True}
            elif c["name"] == "shell":
                cmd = str(args.get("command", ""))
                try:
                    to = max(10, min(int(args.get("timeout_s") or SHELL_TIMEOUT_DEFAULT), SHELL_TIMEOUT_MAX))
                except (TypeError, ValueError):
                    to = SHELL_TIMEOUT_DEFAULT
                remaining = a.wall_h * 3600 - (time.monotonic() - t0)
                to = int(max(10, min(to, remaining)))
                ts = time.time()
                try:
                    p = podman("exec", "-w", "/work", name, "timeout", "-k", "10", str(to), "bash", "-lc", cmd, timeout=to + 60)
                    rc, outp = p.returncode, (p.stdout or "") + (p.stderr or "")
                except subprocess.TimeoutExpired:
                    rc, outp = 124, "timed out after %ds" % to
                dt = round(time.time() - ts, 1)
                cmdlog.write(json.dumps({"at": ts, "call": len(calls), "seconds": dt, "rc": rc, "out_bytes": len(outp),
                                         "command": cmd}) + "\n"); cmdlog.flush()
                if len(outp) > 11000:
                    outp = outp[:5000] + "\n...[%d characters omitted]...\n" % (len(outp) - 10000) + outp[-5000:]
                res = {"exit_code": rc, "seconds": dt, "output": outp}
            else:
                res = {"error": "unknown tool %s" % c["name"]}
            transcript.append({"tool": c["name"], "arguments": args, "result": res})
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": clip(res)})
            if stop == "finished":
                break
    wall = time.monotonic() - t0
    # freeze the converter
    frozen = out / "frozen"
    conv = work / "converter"
    sha = None
    if (conv / "run.sh").is_file():
        sha = tar_dir_deterministic(conv, out / "converter.tar")
        (out / "converter.tar.sha256").write_text(sha + "  converter.tar\n")
        frozen.mkdir(exist_ok=True)
        with tarfile.open(out / "converter.tar") as tf:
            tf.extractall(frozen, filter="data")
        for p in frozen.rglob("*"):
            os.chmod(p, (p.stat().st_mode & 0o555))
        os.chmod(out / "converter.tar", 0o444)
    complete = (bool(calls) and all(c.get("usage_reported") and c.get("stream_complete") for c in calls)
                and not any(f["partial_output"] and not f.get("usage_reported") for f in failed))
    totals = {k: sum(c.get(k) or 0 for c in calls) for k in COUNTERS}
    cost = sum(call_cost_usd(c, a.model) for c in calls) + sum(call_cost_usd(f, a.model) for f in failed if f.get("usage_reported"))
    result = {"task": a.task, "model": a.model, "protocol": a.protocol, "stop_reason": stop, "summary": summary,
              "model_calls": len(calls), "failed_invocations": len(failed),
              "failed_invocations_with_partial_output": sum(1 for f in failed if f["partial_output"]),
              "usage_complete": complete, "usage": totals,
              "cost_usd": round(cost, 6), "prices": PRICES[a.model], "cny_per_usd": CNY_PER_USD,
              "wall_seconds": round(wall, 1), "shell_commands": sum(1 for _ in open(out / "commands.jsonl")),
              "compactions": sum(1 for e in transcript if "harness_compaction" in e),
              "converter_present": sha is not None, "converter_sha256": sha,
              "converter_files": sorted(str(p.relative_to(conv)) for p in conv.rglob("*") if p.is_file()) if conv.is_dir() else []}
    (out / "usage.json").write_text(json.dumps({"calls": calls, "failed_invocations": failed}, indent=1, ensure_ascii=False))
    (out / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False, default=str))
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    podman("rm", "-f", name)
    print(json.dumps({k: result[k] for k in ("model", "stop_reason", "model_calls", "cost_usd", "wall_seconds", "converter_sha256")}))


if __name__ == "__main__":
    main()
