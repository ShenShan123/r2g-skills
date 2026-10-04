"""DEV checks for core parser v0.4 (contract 6140cbc): identity where v0.3 parses,
bounded fallback, fail-closed negatives, and the unblocked v3 static validation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from tehm.rtl.verilog_parse import PARSE_VERSION, parse_verilog

PILOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')
V03 = PILOT / 'software/frozen-i2c-v3-dd22cc9/memory/tehm/rtl/verilog_parse.py'
V03_SHA = None  # recorded in the output; the frozen worktree itself is the pin
SWEEP_ROOTS = ('training', 'dev', 'targets', 'transfer', 'qualification', 'train', 'final')
CLONE = '/data1/zhangdy/RTL/RTL_testbench/chance189/I2C_Master'
COMMIT = '84cdaab5cfd6e00d594e4273f7b978ca1a0a08a4'


def _v03():
    spec = importlib.util.spec_from_file_location('verilog_parse_v03', V03)
    module = importlib.util.module_from_spec(spec)
    sys.modules['verilog_parse_v03'] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    assert module.PARSE_VERSION == 'verilog-parse-v0.3'
    return module


def _dump(modules):
    return json.dumps([m.to_dict() for m in modules], sort_keys=True)


def sweep(v03):
    seen, total, parsed, identical, mismatches, fallback_only = set(), 0, 0, 0, [], []
    for root in SWEEP_ROOTS:
        base = PILOT / root
        if not base.is_dir():
            continue
        for path in base.rglob('*'):
            if path.suffix not in ('.v', '.sv') or path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > 2_000_000:
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            total += 1
            text = data.decode('utf-8', errors='replace')
            old = v03.parse_verilog(text)
            new = parse_verilog(text)
            if old:
                parsed += 1
                if _dump(old) == _dump([m for m in new]):
                    identical += 1
                else:
                    mismatches.append(str(path))
            elif new:
                fallback_only.append(str(path.relative_to(PILOT)))
    return {'distinct_files': total, 'v03_parsed': parsed, 'identical': identical,
            'mismatches': mismatches[:20], 'fallback_only_count': len(fallback_only),
            'fallback_only_sample': sorted(fallback_only)[:20]}


def check():
    v03 = _v03()
    out = {}
    report = sweep(v03)
    out['identity_where_v03_parses'] = report['v03_parsed'] > 0 and report['identical'] == report['v03_parsed']
    clean = subprocess.run(['git', '-C', CLONE, 'show', COMMIT + ':i2c_master.v'], check=True,
                           capture_output=True).stdout.decode()
    fault = clean.replace("nack <= 1'b1;", "nack <= 1'b0;")
    for label, src in (('clean', clean), ('fault', fault)):
        mods = parse_verilog(src)
        out[f'chance189_{label}_parses'] = (not v03.parse_verilog(src) and len(mods) == 1 and
                                            mods[0].name == 'i2c_master' and 'nack' in mods[0].signals and
                                            len(mods[0].always_blocks) >= 3)
    debug = parse_verilog(fault, defined_macros=('DEBUG',))
    out['chance189_debug_defined_parses'] = len(debug) == 1 and 'state' in debug[0].signals
    body = 'module m(input wire clk, output reg q);\nalways @(posedge clk) begin q <= clk; end\nendmodule\n'
    negatives = {
        'nested_ifdef': '`ifdef A\n`ifdef B\n`endif\n`endif\n' + body.replace('input', '`ifdef X\n`endif\ninput', 1),
        'define': '`define W 8\n' + body.replace('(input', '(`ifdef X\n`endif\ninput', 1),
        'include': '`include "x.v"\n' + body.replace('(input', '(`ifdef X\n`endif\ninput', 1),
        'macro_use': body.replace('(input', '(`ifdef X\n`endif\n`MACRO input', 1),
        'non_display_string': body.replace('(input', '(`ifdef X\n`endif\ninput', 1).replace(
            'endmodule', 'wire [23:0] s = "abc";\nendmodule'),
        'unterminated_string': body.replace('(input', '(`ifdef X\n`endif\ninput', 1).replace(
            'endmodule', 'initial $display("abc);\nendmodule'),
        'unbalanced_endif': body.replace('(input', '(`endif\ninput', 1),
    }
    for name, src in negatives.items():
        out['negative_' + name] = not v03.parse_verilog(src) and parse_verilog(src) == []
    out['header_macro_still_rejected'] = not parse_verilog('module bad #(`WIDTH) (input wire clk); endmodule')
    out['header_string_param_still_rejected'] = not parse_verilog(
        'module bad #(parameter NAME="foo") (input wire clk); endmodule')
    from tehm.assets import r5_train_evidence_i2c_v3 as ev, r5_train_raw_i2c_v3 as raw
    from tehm.assets.i2c_binding_v3 import with_i2c_nack_binding_v3
    from tehm.assets.structural_binding import bind_rtl_asset_to_source
    from tehm.assets.synthesis import build_rtl_asset_proposal
    from tehm.assets.validation import validate_rtl_rewrite_asset
    from tehm.rtl.i2c_nack_action_v3 import PROFILE, payload_from_source_i2c_v3
    src, ctx = ev.train_source('alex'), raw.SOURCES['alex']['public_context']
    proposal = with_i2c_nack_binding_v3(build_rtl_asset_proposal(
        {}, name='parser-v04-check', transformation_family='i2c_nack_status_latch_v3',
        action_payload_template=payload_from_source_i2c_v3(src, ctx), compatibility_profile=PROFILE,
        verifier_obligations=('target', 'preservation'), creator='parser_v04_check'), src, ctx)
    asset = {'asset_id': 'parser-v04-check', 'definition': proposal.definition,
             'compatibility': proposal.compatibility, 'provenance': proposal.provenance,
             'verifier_contract': getattr(proposal, 'verifier_contract', {}),
             **{k: v for k, v in vars(proposal).items() if k not in ('definition', 'compatibility', 'provenance')}}
    static = {}
    for case in raw.CASE_ORDER:
        s, c = ev.train_source(case), raw.SOURCES[case]['public_context']
        bound = bind_rtl_asset_to_source(asset, s, design_id=case, public_context=c)
        receipt = validate_rtl_rewrite_asset(bound, s, verifier=lambda cand, a: {'verdict': 'PASS'},
                                             regression_verifier=lambda cand, a: {'verdict': 'PASS'}).to_dict()
        static[case] = receipt['static_valid']
    out['v3_train_static_valid_all_four'] = all(static.values())
    return {'schema': 'r5-core-parser-v04-dev-checks-v1', 'contract_commit': '6140cbc',
            'parser_version': PARSE_VERSION, 'valid': all(out.values()), 'check_count': len(out),
            'failed': sorted(k for k, v in out.items() if not v), 'checks': out, 'sweep': report,
            'static_valid_by_source': static,
            'v03_sha256': hashlib.sha256(V03.read_bytes()).hexdigest(), 'model_calls': 0}


if __name__ == '__main__':
    result = check()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result['valid'] else 1)
