"""Container entrypoint: local TCP-to-Unix bridge and an unmodified native CLI."""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import socketserver
import subprocess
import threading
from pathlib import Path


class Bridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Forward(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = socket.socket(socket.AF_UNIX)
        upstream.connect("/experiment/api/gateway.sock")

        def copy(source, target):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    target.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        worker = threading.Thread(target=copy, args=(self.request, upstream), daemon=True)
        worker.start()
        copy(upstream, self.request)
        upstream.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=("dsh", "codex", "r2g"), required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--protocol", choices=("anthropic", "openai-responses"), default="anthropic")
    ap.add_argument("--cpu-count", type=int, default=8)
    ap.add_argument("--cpuset", default="")
    ap.add_argument("--reasoning-effort", default="")
    ap.add_argument("--version-only", action="store_true")
    args = ap.parse_args()
    available = sorted(os.sched_getaffinity(0))
    selected = [int(x) for x in args.cpuset.split(",")] if args.cpuset else available[:args.cpu_count]
    if not selected or not set(selected) <= set(available):
        raise SystemExit("requested CPU affinity is unavailable")
    os.sched_setaffinity(0, selected)
    os.chdir("/work")
    home = Path("/agent-home")
    home.mkdir(exist_ok=True)
    os.environ.update(HOME=str(home), LANG="C.UTF-8", NO_COLOR="1")
    cli = "/opt/native/%s/node_modules/.bin/%s" % (args.harness, args.harness)
    version = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip() if args.harness != "r2g" else "r2g-agent"
    Path("/work/native_runtime.json").write_text(json.dumps({"harness": args.harness, "harness_version": version,
                                                           "cpu_affinity": selected, "inner_sandbox_mode": "danger-full-access",
                                                           "isolation": "outer network-none read-only container"}))
    if args.version_only:
        if args.harness == "r2g":
            return subprocess.call(["python3", "-c", "import r2g_agent; print(r2g_agent.__file__)"])
        print(version, flush=True)
        return 0 if version else 1
    bridge = Bridge(("127.0.0.1", 0), Forward)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % bridge.server_address[1]
    os.environ.update(EXP4_API_BASE_URL=url + "/v1", EXP4_MODEL=args.model, EXP4_PROTOCOL=args.protocol,
                      EXP4_API_KEY="exp4-local-accounting-gateway")
    if args.harness == "dsh":
        os.environ.update(DSH_HOME=str(home), DEEPSEEK_API_KEY="exp4-local-accounting-gateway", DEEPSEEK_BASE_URL=url)
        # Nested sandbox backends are unavailable in this image/kernel. All arms
        # use the same outer read-only, network-none container as their boundary.
        command = [cli, "headless", "--permission-mode", "danger-full-access", "--approval-policy", "never", "--json", "-"]
    elif args.harness == "codex":
        os.environ.update(CODEX_HOME=str(home), EXP4_API_KEY="exp4-local-accounting-gateway")
        # Fresh API provider config: no subscription login or personal settings.
        config = ('model = %s\nmodel_provider = "exp4"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
                  '[model_providers.exp4]\nname = "Exp4 fixed model relay"\nbase_url = %s\n'
                  'wire_api = "responses"\nenv_key = "EXP4_API_KEY"\nrequires_openai_auth = false\n') % (
                      json.dumps(args.model), json.dumps(url + "/v1"))
        if args.reasoning_effort:
            config = 'model_reasoning_effort = %s\n' % json.dumps(args.reasoning_effort) + config
        (home / "config.toml").write_text(config)
        command = [cli, "exec", "--json", "--skip-git-repo-check", "-m", args.model, "-C", "/work", "-"]
    else:
        command = ["python3", "/experiment/runner_r2g.py", "--config", "/experiment/config.json"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    def stop(signum, frame):
        process.terminate()
    signal.signal(signal.SIGTERM, stop)
    process.communicate(Path("/experiment/task.txt").read_bytes())
    bridge.shutdown()
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
