"""Evaluator-private axis_skid DEV adapter; not a general RTL sandbox/launcher.

Trusted caller supplies frozen test/dependency/tool identities. Model data never
controls commands, mounts, tests or verdict metadata. No provider is invoked.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

TESTS = ("skid_random_backpressure", "skid_full_throughput")
SOURCE = "rtl/axis_skid.sv"
SEED = "20260926"
SUMMARY = re.compile(r"\bTESTS=(\d+) PASS=(\d+) FAIL=(\d+) SKIP=(\d+)\b")

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def inventory(root):
    return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob("*")) if p.is_file()}

def write(path, value):
    with path.open("x", encoding="utf-8") as h:
        h.write(json.dumps(value, indent=2, sort_keys=True)+"\n")

def unknown(reason):
    return {"obligations":dict.fromkeys(("target","preservation","native"),"UNKNOWN"),"reason":reason}

def native_verdict(xml: bytes | None, stdout: str, rc: int, timed_out: bool) -> dict:
    if timed_out or not xml: return unknown("timeout_or_missing_report")
    try:
        root=ET.fromstring(xml)
        if root.tag!="testsuites" or len(root)!=1 or root[0].tag!="testsuite":
            return unknown("junit_structure")
        cases=root[0].findall("testcase")
        if len(cases)!=2 or {c.get("name") for c in cases}!=set(TESTS):
            return unknown("test_registry")
        if any(c.get("classname")!="test_axis_skid" or c.find("error") is not None
               or c.find("skipped") is not None or len(c)>1 for c in cases):
            return unknown("test_identity_or_incomplete")
        seeds=[p.get("value") for p in root[0].findall("property") if p.get("name")=="random_seed"]
        if seeds!=[SEED]: return unknown("seed_identity")
        verdicts={c.get("name"):"FAIL" if c.find("failure") is not None else "PASS" for c in cases}
        failed=sum(v=="FAIL" for v in verdicts.values())
        summaries=SUMMARY.findall(stdout)
        if len(summaries)!=1 or tuple(map(int,summaries[0]))!=(2,2-failed,failed,0):
            return unknown("native_summary_contradiction")
        if type(rc) is not int or (rc==0)!=(failed==0) or rc not in {0,2}:
            return unknown("make_exit_contradiction")
        return {"obligations":{"target":verdicts[TESTS[0]],"preservation":verdicts[TESTS[1]],
                               "native":"FAIL" if failed else "PASS"},
                "reason":"native_make_xml_summary_agree","test_verdicts":verdicts}
    except (ET.ParseError,ValueError,TypeError): return unknown("unparseable_report")

def admitted_preprocessed(data: bytes) -> bool:
    # Deliberately conservative even in comments/strings; macros already expanded.
    return bool(data) and len(data)<=262144 and b"$" not in data and b"\0" not in data

def sandbox():
    return ["bwrap","--unshare-all","--die-with-parent","--new-session","--clearenv",
            "--ro-bind","/usr","/usr","--ro-bind","/lib","/lib","--ro-bind","/lib64","/lib64",
            "--symlink","usr/bin","/bin","--proc","/proc","--dev","/dev","--tmpfs","/tmp",
            "--setenv","PATH","/usr/bin:/bin","--setenv","PYTHONDONTWRITEBYTECODE","1"]

def _limits():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,16*1024*1024))

def execute(argv, out, timeout):
    out.mkdir(exist_ok=False)
    write(out/"launch.json",{"argv":argv,"timeout_seconds":timeout,"retry_limit":0})
    started=time.monotonic(); timed=False
    with (out/"stdout.log").open("xb") as stdout,(out/"stderr.log").open("xb") as stderr:
        proc=subprocess.Popen(argv,stdout=stdout,stderr=stderr,start_new_session=True,preexec_fn=_limits)
        try: rc=proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed=True; os.killpg(proc.pid,signal.SIGTERM)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL); proc.wait()
            rc=124
    result={"returncode":rc,"timed_out":timed,"wall_seconds":time.monotonic()-started,
            "stdout_sha256":sha(out/"stdout.log"),"stderr_sha256":sha(out/"stderr.log")}
    write(out/"process.json",result)
    return result

def evaluate(candidate: Path, private: Path, deps: Path, lock: dict, output: Path) -> dict:
    if inventory(private)!=lock["private_files"] or inventory(deps)!=lock["dependency_files"]:
        raise ValueError("private test or dependency drift")
    if any(sha(name)!=value for name,value in lock["tools"].items()): raise ValueError("host tool drift")
    if any(p.is_symlink() for root in (candidate,private,deps) for p in root.rglob("*")):
        raise ValueError("linked evaluator input")
    if list(inventory(candidate))!=[SOURCE]: raise ValueError("candidate closure outside C2 profile")
    before=sha(candidate/SOURCE)
    output.mkdir(exist_ok=False)
    pre=output/"preprocess-output"; pre.mkdir()
    command=sandbox()+["--ro-bind",str(candidate),"/candidate","--bind",str(pre),"/out",
        "--chdir","/candidate","/usr/bin/iverilog","-E","-g2012","-s","axis_skid",
        "-o","/out/axis_skid.sv","/candidate/"+SOURCE]
    preprocessing=execute(command,output/"preprocess-process",30)
    processed=pre/"axis_skid.sv"
    result=unknown("preprocess_failed_or_unsupported_system_construct")
    if preprocessing["returncode"]==0 and processed.is_file() and admitted_preprocessed(processed.read_bytes()):
        runout=output/"native-output"; runout.mkdir()
        command=sandbox()+["--ro-bind",str(private),"/private","--ro-bind",str(deps),"/deps",
            "--ro-bind",str(pre),"/preprocessed","--bind",str(runout),"/out",
            "--setenv","PATH","/deps/bin:/usr/bin:/bin","--setenv","PYTHONPATH","/deps:/private/tb",
            "--setenv","COCOTB_RANDOM_SEED",SEED,"--chdir","/private/tb",
            "/usr/bin/make","DUT=axis_skid","VERILOG_SOURCES=/preprocessed/axis_skid.sv",
            "SIM_BUILD=/out/build","COCOTB_RESULTS_FILE=/out/results.xml","sim"]
        process=execute(command,output/"native-process",90)
        xml=runout/"results.xml"; vvp=runout/"build/sim.vvp"
        stdout=(output/"native-process/stdout.log").read_text(errors="replace")
        result=native_verdict(xml.read_bytes() if xml.is_file() else None,stdout,process["returncode"],process["timed_out"])
        if not vvp.is_file() or b'"/preprocessed/axis_skid.sv"' not in vvp.read_bytes() or "/usr/bin/iverilog " not in stdout:
            result=unknown("compiled_candidate_identity_missing")
        result["compiled_vvp_sha256"]=sha(vvp) if vvp.is_file() else None
        result["preprocessed_sha256"]=sha(processed)
    if sha(candidate/SOURCE)!=before or inventory(private)!=lock["private_files"]:
        raise ValueError("candidate or private input mutated")
    result.update({"source_sha256":before,"role":"SCRIPTED_DEV_NATIVE_INTEGRATION_NOT_AGENT",
                   "model_calls":0,"method_tasks":0,"raw_files":inventory(output)})
    write(output/"private-result.json",result)
    return result
