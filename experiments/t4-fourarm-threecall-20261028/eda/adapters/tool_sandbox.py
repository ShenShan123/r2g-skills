#!/usr/bin/python3
"""Run native EDA binaries with only their toolchain and current workspace mounted."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = Path("/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite")
TOOLS = {"iverilog": Path("/usr/bin/iverilog"), "vvp": Path("/usr/bin/vvp"),
         "verilator": TOOLCHAIN / "bin/verilator", "yosys": TOOLCHAIN / "bin/yosys"}


def command(tool, args, cwd):
    cwd = Path(cwd).resolve()
    if not any(cwd.is_relative_to(ROOT / name) for name in ('hosts','reports','qualification')):
        raise ValueError("EDA execution outside isolated Table 4 workspace")
    if tool in TOOLS:
        executable = TOOLS[tool]
    elif Path(tool).is_absolute() and Path(tool).resolve().is_relative_to(cwd):
        executable = Path(tool).resolve()
    else:
        raise ValueError("unregistered native tool or simulation binary")
    cmd = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
           "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
           "--ro-bind", "/lib64", "/lib64", "--symlink", "usr/bin", "/bin",
           "--proc", "/proc", "--dev", "/dev",
           "--tmpfs", "/tmp", "--ro-bind", str(TOOLCHAIN), str(TOOLCHAIN),
           "--bind", str(cwd), str(cwd), "--chdir", str(cwd), "--clearenv",
           "--setenv", "PATH", str(TOOLCHAIN / "bin") + ":/usr/bin:/bin",
           "--setenv", "LC_ALL", "C", "--setenv", "HOME", "/tmp",
           "--", str(executable), *args]
    return cmd


if __name__ == "__main__":
    tool = Path(sys.argv[0]).name
    cmd = command(tool, sys.argv[1:], Path.cwd())
    os.execv(cmd[0], cmd)
