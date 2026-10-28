#!/usr/bin/env python3
"""Exercise installed Slang through the Agent's normal frontend interface.

Run with the installed Agent Python, or PYTHONPATH=agent from the checkout.
This checks dependency discovery and translation, not whole-design equivalence.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from r2g_agent import frontend, project

with tempfile.TemporaryDirectory(prefix='r2g-slang-smoke-') as tmp:
    root = Path(tmp)
    src = root / 'source'
    src.mkdir()
    rtl = src / 'top.sv'
    rtl.write_text('module top(input logic [3:0] lanes [0:1], output logic [3:0] y);\n'
                   'assign y = lanes[0] ^ lanes[1];\nendmodule\n')
    p = root / 'project'
    meta = project.create(src, p, 'nangate45', top='top')
    original = (p / 'rtl/top.sv').read_bytes()
    attempt = frontend.prepare_slang(p, meta)
    run = subprocess.run(attempt['command'], capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    result = project.activate_slang(p, attempt)
    if (p / 'rtl/top.sv').read_bytes() != original:
        raise RuntimeError('Original RTL changed')
    output = Path(attempt['output'])
    if output.stat().st_size == 0 or 'sv_frontend' not in result:
        raise RuntimeError('Translation was not activated')
    print(json.dumps(dict(status='pass', tool=attempt['tool'],
                          input_sha256=hashlib.sha256(original).hexdigest(),
                          translated_bytes=output.stat().st_size), indent=2))
