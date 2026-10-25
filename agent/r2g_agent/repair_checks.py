"""One registered functional oracle, not a model-supplied test or shell command."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from .paths import PKG_DIR, repo_root, signoff_scripts, tools_dir

ORACLE_ID = "counter8_contract_v1"
TESTBENCH = r'''
module edacode_contract;
  reg clk=0, rst_n=1, en=0, up=0, load=0;
  reg [7:0] d=0;
  wire [7:0] q;
  wire wrap;
  reg [7:0] expected=0;
  integer i;
  counter8 dut(clk,rst_n,en,up,load,d,q,wrap);
  always #5 clk=~clk;
  initial begin
    #1 rst_n=0; #1;
    if (q !== 0) $fatal(1,"asynchronous reset failed");
    rst_n=1;
    for(i=0;i<1024;i=i+1) begin
      @(negedge clk);
      d=(i*73); en=(i%3!=0); up=(i%2!=0); load=(i%7==0);
      if(i%257==0) begin rst_n=0; expected=0; end else rst_n=1;
      @(posedge clk);
      if(!rst_n) expected=0;
      else if(load) expected=d;
      else if(en) expected=up ? expected+8'd1 : expected-8'd1;
      #1;
      if(q !== expected) $fatal(1,"state mismatch at cycle %0d",i);
      if(wrap !== (en & (up ? (expected==255) : (expected==0))))
        $fatal(1,"wrap mismatch at cycle %0d",i);
    end
    $display("EDACODE_ORACLE={\"oracle\":\"counter8_contract_v1\",\"checks\":2048,\"ok\":true}");
    $finish;
  end
endmodule
'''


def oracle_policy() -> dict:
    executables = {}
    for name, variable in (("iverilog", "R2G_IVERILOG_EXE"), ("vvp", "R2G_VVP_EXE")):
        executable = os.environ.get(variable) or shutil.which(name)
        if not executable or not Path(executable).is_file():
            raise ValueError("functional checker unavailable: " + name)
        path = Path(executable).resolve()
        executables[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    relevant = [PKG_DIR / name for name in ("pipeline.py", "project.py", "profile.py", "repair_checks.py",
                                           "repairs.py", "input_snapshots.py", "package.py")]
    relevant.extend([signoff_scripts() / "flow" / "run_orfs.sh", tools_dir() / "run_signoff.sh",
                     tools_dir() / "verify_graph_dataset.py"])
    policy_files = {str(path.relative_to(repo_root())): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in relevant}
    for name in ("signoff-loop", "def-graph"):
        path = repo_root() / "r2g-skills" / name / "references" / "env.local.sh"
        if path.is_file():
            policy_files[str(path.relative_to(repo_root()))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"id": ORACLE_ID, "testbench_sha256": hashlib.sha256(TESTBENCH.encode()).hexdigest(),
            "tools": executables, "checks": 2048, "flow_policy_files": policy_files}


def check(workspace: Path, rtl: Path, policy: dict, deadline: float) -> dict:
    if policy != oracle_policy():
        raise ValueError("functional checker version changed after approval")
    tb, output = workspace / "oracle.v", workspace / "simulation.vvp"
    tb.write_text(TESTBENCH)
    checks = []
    commands = [("compile", [policy["tools"]["iverilog"]["path"], "-g2012", "-s", "edacode_contract",
                             "-o", str(output), str(rtl), str(tb)]),
                ("simulate", [policy["tools"]["vvp"]["path"], str(output)])]
    for name, command in commands:
        remaining = min(15, deadline - time.time())
        if remaining <= 0:
            raise ValueError("repair wall-clock budget exhausted")
        log = workspace / (name + ".log")
        with log.open("wb") as stream:
            process = subprocess.Popen(command, cwd=workspace, stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True, env={"PATH": os.defpath, "HOME": str(workspace),
                                       "TMPDIR": str(workspace), "LANG": "C"})
            try:
                rc = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                rc = 124
        text = log.read_bytes()[-12000:].decode(errors="replace")
        checks.append({"name": name, "exit_code": rc, "log_tail": text,
                       "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
        if rc:
            return {"ok": False, "oracle": policy, "checks": checks}
    marker = [line.removeprefix("EDACODE_ORACLE=") for line in text.splitlines()
              if line.startswith("EDACODE_ORACLE=")]
    verdict = json.loads(marker[0]) if len(marker) == 1 else None
    passed = verdict == {"oracle": ORACLE_ID, "checks": 2048, "ok": True}
    return {"ok": passed, "oracle": policy, "checks": checks, "functional_result": verdict}
