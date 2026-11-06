#!/usr/bin/env python3
"""Experiment 1: a DeepSeek agent installs the R2G toolchain in a clean container by following the documentation.

Host side (216). Starts a fresh container from the recorded base image (Containerfile), copies a clean `git archive`
of one commit to /home/tester/repo, and gives the model ONE tool, `shell`, that runs a command inside the container as
the ordinary user `tester`, plus `finish`. No host tool, kit, cache or credential is mounted. Afterwards the
independent verify_checklist.sh decides every checklist item; the model's own report counts for nothing.

Recorded under OUT: transcript.json, usage.json (every call's provider usage), commands.jsonl (each command, exit
code, seconds, output size), network bytes received by the container, checklist.json, result.json.

  run_install_agent.py --repo-archive REPO.tar --commit SHA --out OUT [--image r2g-exp1-base:22.04]
                       [--base-url http://127.0.0.1:18774/v1] [--model deepseek-flash] [--max-calls 80] [--wall-h 6]
"""
import argparse, json, os, subprocess, sys, time, uuid
from pathlib import Path

TASK = """You are in a fresh Linux container as the ordinary user `tester` (no sudo, no pre-installed EDA tools).
The R2G repository is at ~/repo (commit {commit}). Install the EDA toolchain it needs by following ITS OWN
documentation: start with ~/repo/r2g-skills/eda-install/SKILL.md. Install into ~/toolchain. The target process
platforms are nangate45, sky130hd and gf180.
Rules: download only what the documentation requires; do not install anything unrelated; do not edit the
repository's code. Long installs may take many minutes: give the shell tool a long enough timeout.
When the documented verification passes (or you are certain it cannot), call finish with a short summary."""

SPECS = [
    {"name": "shell", "description": "Run a bash command inside the container as user tester (cwd: home). Returns "
     "exit code and the tail of stdout+stderr.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"},
                    "timeout_s": {"type": "integer", "description": "default 600, max 10800"}},
                    "required": ["command"]}},
    {"name": "finish", "description": "Declare the installation finished and summarise the result.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]
SYSTEM = "You are a careful build engineer. Use the shell tool. Keep commands focused; read documentation before acting."
TAIL = 6000


def podman(*a, **kw):
    return subprocess.run(["podman", *a], capture_output=True, text=True, **kw)


def net_rx(name):
    r = podman("exec", name, "python3", "-c", "import glob;print(sum(int(open(f).read()) for f in glob.glob('/sys/class/net/*/statistics/rx_bytes')))")
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-archive", type=Path, required=True); ap.add_argument("--commit", required=True)
    ap.add_argument("--out", type=Path, required=True); ap.add_argument("--image", default="localhost/r2g-exp1-base:22.04")
    ap.add_argument("--base-url", default="http://127.0.0.1:18774/v1"); ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--max-calls", type=int, default=80); ap.add_argument("--wall-h", type=float, default=6.0)
    ap.add_argument("--agent-src", type=Path, default=Path(__file__).resolve().parents[2] / "agent")
    a = ap.parse_args()
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(a.agent_src))
    from r2g_agent.config import ModelConfig
    from r2g_agent.llm.openai_compat import OpenAICompatAdapter
    key = os.environ.setdefault("R2G_LOCAL_RELAY_AUTH", "local-relay-only-not-a-provider-key")
    mc = ModelConfig(provider="deepseek", model=a.model, base_url=a.base_url, protocol="openai",
                     api_key_env="R2G_LOCAL_RELAY_AUTH", thinking=True, max_tokens=16384, temperature=0.2)
    adapter = OpenAICompatAdapter(mc.resolved(), key)

    name = "r2g_exp1_" + uuid.uuid4().hex[:8]
    r = podman("run", "-d", "--name", name, "--network", "slirp4netns", a.image, "sleep", "infinity")
    if r.returncode:
        sys.exit("container start failed: " + r.stderr)
    podman("cp", str(a.repo_archive), name + ":/tmp/repo.tar")
    podman("exec", name, "bash", "-c", "mkdir -p ~/repo && tar -xf /tmp/repo.tar -C ~/repo && rm /tmp/repo.tar")
    rx0 = net_rx(name)
    meta = {"container": name, "image": a.image, "commit": a.commit, "model": a.model, "base_url": a.base_url,
            "started": time.time(), "rx_bytes_start": rx0,
            "image_id": podman("image", "inspect", a.image, "--format", "{{.Id}}").stdout.strip()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))

    messages = [{"role": "user", "content": TASK.format(commit=a.commit)}]
    transcript, calls, failed = [], [], []
    cmdlog = (out / "commands.jsonl").open("a")
    t0, stop, summary = time.monotonic(), None, None
    while stop is None:
        if len(calls) >= a.max_calls:
            stop = "cap_model_calls"; break
        if time.monotonic() - t0 > a.wall_h * 3600:
            stop = "cap_wall"; break
        text, tool_calls, usage, err = "", [], None, None
        for ev in adapter.stream(messages, SPECS, SYSTEM):
            if ev["type"] == "text": text += ev["delta"]
            elif ev["type"] == "tool_call": tool_calls.append(ev)
            elif ev["type"] == "usage": usage = ev
            elif ev["type"] == "error": err = ev["message"]
        if err:
            failed.append({"error": err, "at": time.time()})
            if len(failed) > 8: stop = "model_error"; break
            time.sleep(min(30 * len(failed), 300)); continue
        calls.append({**(usage or {"usage_reported": False}), "call_index": len(calls) + 1, "at": time.time()})
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in tool_calls]})
        transcript.append({"assistant": text, "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in tool_calls]})
        if not tool_calls:
            messages.append({"role": "user", "content": "Continue with the shell tool, or call finish."}); continue
        for c in tool_calls:
            args = c["arguments"] if isinstance(c["arguments"], dict) else {}
            if c["name"] == "finish":
                stop, summary = "finished", args.get("summary", ""); res = {"ok": True}
            elif c["name"] == "shell":
                cmd = str(args.get("command", "")); to = max(10, min(int(args.get("timeout_s") or 600), 10800))
                ts = time.time()
                try:
                    p = podman("exec", "-u", "tester", "-w", "/home/tester", name, "bash", "-lc", cmd, timeout=to + 30)
                    rc, outp = p.returncode, (p.stdout or "") + (p.stderr or "")
                except subprocess.TimeoutExpired:
                    rc, outp = 124, "timed out after %ds" % to
                dt = round(time.time() - ts, 1)
                cmdlog.write(json.dumps({"at": ts, "seconds": dt, "rc": rc, "out_bytes": len(outp), "command": cmd}) + "\n"); cmdlog.flush()
                res = {"exit_code": rc, "seconds": dt, "output_tail": outp[-TAIL:]}
            else:
                res = {"error": "unknown tool"}
            transcript.append({"tool": c["name"], "arguments": args, "result": res})
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": json.dumps(res, ensure_ascii=False)})
    wall = time.monotonic() - t0
    rx1 = net_rx(name)
    podman("cp", str(Path(__file__).with_name("verify_checklist.sh")), name + ":/tmp/verify.sh")
    v = podman("exec", "-u", "tester", name, "bash", "/tmp/verify.sh", timeout=3600)
    try:
        checklist = json.loads(v.stdout.strip().splitlines()[-1])
    except Exception:
        checklist = {"_verifier_error": (v.stdout + v.stderr)[-2000:]}
    pk = podman("exec", "-u", "tester", name, "bash", "-lc",
                "for c in ~/toolchain/*/bin/conda ~/toolchain/bin/conda; do [ -x $c ] && $c list 2>/dev/null; done | head -400; "
                "ls ~/toolchain 2>/dev/null").stdout
    (out / "installed_packages.txt").write_text(pk)
    K = ("input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens", "uncached_input_tokens")
    items = {k: v for k, v in checklist.items() if not k.startswith("_") and isinstance(v, dict)}
    result = {"stop_reason": stop, "summary": summary, "model": a.model, "model_calls": len(calls),
              "failed_invocations": len(failed), "usage": {k: sum(c.get(k) or 0 for c in calls) for k in K},
              "usage_complete": all(c.get("usage_reported", True) is not False for c in calls),
              "wall_seconds": round(wall, 1), "shell_commands": sum(1 for _ in open(out / "commands.jsonl")),
              "rx_bytes": (rx1 - rx0) if (rx0 is not None and rx1 is not None) else None,
              "checklist": checklist, "items_ok": sum(1 for v in items.values() if v.get("ok")), "items_total": len(items)}
    (out / "usage.json").write_text(json.dumps({"calls": calls, "failed": failed}, indent=1))
    (out / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False))
    (out / "checklist.json").write_text(json.dumps(checklist, indent=1))
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps({k: result[k] for k in ("stop_reason", "model_calls", "items_ok", "items_total", "wall_seconds", "rx_bytes")}))
    print("container kept for inspection:", name)


if __name__ == "__main__":
    main()
