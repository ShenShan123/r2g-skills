"""DEV checks for the I2C NACK v3 binder (contract ae86c75). DEV conformance only.

usage: python3 -m tehm.evaluation.research_r5_i2c_nack_binding_dev_v3_checks [--output NEW_DIR]
Sources: chance189 (DEV_OBSERVED_V3) from its pinned clone; the three v2 TRAIN pairs from
the sealed TRAIN package. The DEV oracle run uses the frozen research-augmented oracle.
"""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import tempfile

from tehm.assets import r5_train_evidence_i2c_v2 as evidence
from tehm.assets import r5_train_raw_i2c_v2 as raw
from tehm.evaluation import research_r5_i2c_nack_binding_dev_v2 as v2
from tehm.evaluation import research_r5_i2c_nack_binding_dev_v3 as v3
from tehm.evaluation.research_r5_s2_native import sandbox
from tehm.rtl import i2c_nack_action_v2 as transport

CLONE = Path('/data1/zhangdy/RTL/RTL_testbench/chance189/I2C_Master')
COMMIT = '84cdaab5cfd6e00d594e4273f7b978ca1a0a08a4'
AUG = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/targets/chance189-i2c-r1/aug-dev')
PRELAUNCH_SHA = '481a58fedeac730ee621d821d702ce956a00015d3fb7116794e72be782d86727'
OSS = Path('/opt/pdk_klayout_openroad/oss-cad-suite/bin')
A3 = {'binding_template': v3.TEMPLATE}
A2 = {'binding_template': v2.TEMPLATE}
CTX = {'interface': v3.NEW_INTERFACE, 'status_output': 'nack', 'defined_macros': []}
CLEAN_FILES = {'alex': 'original/oracles/alex/candidate/stage/rtl/i2c_master.v',
               'zip': 'original/oracles/zip/candidate/stage/rtl/wbi2cmaster.v',
               'freecores': 'original/oracles/freecores/candidate/stage/rtl/verilog/i2c_master_top.v'}


def h(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def chance189():
    clean = subprocess.run(['git', '-C', str(CLONE), 'show', COMMIT + ':i2c_master.v'],
                           check=True, capture_output=True).stdout.decode()
    assert clean.count("nack <= 1'b1;") == 1
    return clean.replace("nack <= 1'b1;", "nack <= 1'b0;"), clean


def train_pairs():
    pairs = {}
    for case in evidence.CASE_ORDER:
        fault = evidence.train_source(case)
        clean_top = (raw.TRAIN / CLEAN_FILES[case]).read_text()
        if case == 'freecores':
            roles = transport.decode_closure(fault)
            pairs[case] = (roles, {**roles, 'top': clean_top})
        else:
            pairs[case] = (fault, clean_top)
    return pairs


def status(result):
    return result['status'], result['reason']


def not_bound(source, ctx=CTX, want=None):
    r = v3.bind(A3, source, ctx)
    return r['status'] != 'BOUND' and (want is None or r['status'] in want), status(r)


def rejects(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def checks():
    fault, clean = chance189()
    out, notes = {}, {}
    # ---- P: chance189 positives
    bound = v3.bind(A3, fault, CTX)
    out['P1_fault_bound'] = bound['status'] == 'BOUND'
    candidate, receipt = v3.apply(A3, fault, CTX, bound)
    out['P1_candidate_equals_clean'] = candidate == clean and receipt['rewritten_spans'] == 1
    out['P2_clean_no_match'] = status(v3.bind(A3, clean, CTX)) == ('NO_MATCH', 'status_event_already_sets_status')
    debug = v3.bind(A3, fault, {**CTX, 'defined_macros': ['DEBUG']})
    out['P3_debug_defined_binds_same_span'] = (debug['status'] == 'BOUND' and
                                               debug['witness']['rhs_span'] == bound['witness']['rhs_span'])
    out['P3_define_rejected'] = not_bound('`define DEBUG\n' + fault, want={'UNSUPPORTED'})[0]
    # ---- D1: exact delegation to frozen v2 on all six TRAIN sources
    for case, (tf, tc) in train_pairs().items():
        ctx = raw.SOURCES[case]['public_context']
        for label, src in (('fault', tf), ('clean', tc)):
            r2, r3 = v2.bind(A2, src, ctx), v3.bind(A3, src, ctx)
            if r2['status'] == 'BOUND':
                c2, _ = v2.apply(A2, src, ctx, r2)
                c3, _ = v3.apply(A3, src, ctx, r3)
                ok = (r3['status'] == 'BOUND' and r3['witness']['v2_binding'] == r2 and
                      r3['witness']['v2_action_digest'] == r2['witness']['action_digest'] and c2 == c3)
            else:
                ok = status(r3) == status(r2)
            out[f'D1_delegation_{case}_{label}'] = ok
            notes[f'{case}_{label}'] = {'v2': status(r2), 'v3': status(r3)}
    # ---- N: negative constructions derived from the chance189 fault source
    start, stop = bound['witness']['rhs_span']
    clear_line = "nack <= 1'b0;  "
    assert fault.count(clear_line) == 1
    event_stmt = fault[start - len('nack <= '):stop + 1]
    assert event_stmt == "nack <= 1'b0;"
    body_end = fault.rindex('endmodule')
    negatives = {
        'status_port_absent': (fault, {**CTX, 'status_output': 'nack_missing'}, {'NO_MATCH'}),
        'status_not_reg': (fault.replace('output reg        nack', 'output            nack', 1), CTX, {'UNSUPPORTED'}),
        'status_output_names_non_port': (fault, {**CTX, 'status_output': 'sda_prev'}, {'NO_MATCH'}),
        'third_writer_blocking': (fault.replace(clear_line, "nack <= 1'b0; nack = 1'b0;", 1), CTX, {'UNSUPPORTED'}),
        'third_writer_continuous': (fault[:body_end] + "assign nack = 1'b0;\n" + fault[body_end:], CTX, {'UNSUPPORTED'}),
        'third_writer_nonblocking': (fault.replace(clear_line, "nack <= 1'b0; nack <= 1'b0;", 1), CTX, {'AMBIGUOUS'}),
        'event_moved_out_of_else': (fault[:start - len('nack <= ')] + fault[stop + 1:].replace(
            'state <= next_state;\n', "state <= next_state; nack <= 1'b0;\n", 1), CTX, {'UNSUPPORTED'}),
        'two_ack_low_else_writers': (fault.replace(clear_line, "nack <= 1'b0; if(!sda_prev) begin end else begin nack <= 1'b0; end", 1),
                                     CTX, {'AMBIGUOUS'}),
        'writer_in_second_process': (fault[:body_end] + "always@(posedge i_clk or negedge reset_n) begin if(!reset_n) begin end else nack <= 1'b0; end\n" + fault[body_end:],
                                     CTX, {'UNSUPPORTED'}),
        'nested_ifdef': (fault.replace('`ifndef DEBUG\n', '`ifndef DEBUG\n`ifdef FOO\n`endif\n', 1), CTX, {'UNSUPPORTED'}),
        'include': ('`include "x.v"\n' + fault, CTX, {'UNSUPPORTED'}),
        'macro_use': (fault.replace('DIV_100MHZ-1', '`DIVISOR-1', 1), CTX, {'UNSUPPORTED'}),
        'non_display_string': (fault[:body_end] + 'wire [23:0] tag = "abc";\n' + fault[body_end:], CTX, {'UNSUPPORTED'}),
        'unterminated_string': (fault.replace('START INDICATION!", $time);', 'START INDICATION!, $time);', 1), CTX, {'UNSUPPORTED'}),
        'unknown_context_key': (fault, {**CTX, 'hint': 'x'}, {'UNSUPPORTED'}),
        'second_module': (fault + '\nmodule extra(); endmodule\n', CTX, {'AMBIGUOUS'}),
        'oversized_source': (fault + '\n//' + 'x' * 70000 + '\n', CTX, {'UNSUPPORTED'}),
    }
    alex_fault = evidence.train_source('alex')
    negatives['alex_under_new_interface'] = (alex_fault, {**CTX, 'status_output': 'missed_ack'}, None)
    for name, (src, ctx, want) in negatives.items():
        ok, got = not_bound(src, ctx, want)
        out['N_' + name] = ok
        notes['N_' + name] = got
    # comment lure must not change the result (appended after endmodule: spans unchanged)
    lure = fault + "\n// nack <= 1'b1;\n"
    lured = v3.bind(A3, lure, CTX)
    out['N_comment_lure_unchanged'] = (lured['status'] == 'BOUND' and
                                       lured['witness']['rhs_span'] == bound['witness']['rhs_span'] and
                                       status(v3.bind(A3, clean + "\n// nack <= 1'b0;\n", CTX))[0] == 'NO_MATCH')
    # ---- firewall, tamper, idempotence
    out['F_signature_source_and_context_only'] = list(inspect.signature(v3.bind).parameters) == \
        ['asset', 'buggy_source', 'public_context']
    tampered = json.loads(json.dumps(bound))
    tampered['witness']['action_digest'] = 'sha256:' + '0' * 64
    out['F_tampered_witness_rejected'] = rejects(lambda: v3.apply(A3, fault, CTX, tampered))
    out['F_stale_binding_rejected'] = rejects(lambda: v3.apply(A3, fault + '\n', CTX, bound))
    out['F_idempotent_second_apply_no_match'] = (v3.bind(A3, candidate, CTX)['status'] == 'NO_MATCH' and
                                                 rejects(lambda: v3.apply(A3, candidate, CTX, v3.bind(A3, candidate, CTX))))
    out['F_sandboxed_bind_equal'] = sandboxed(fault) == bound
    return out, notes, fault, candidate


def sandboxed(fault):
    """Run bind in bwrap: only the code tree, the source on stdin, no /data1, no network."""
    memory = Path(v3.__file__).resolve().parents[2]
    child = ('import json,sys,os\nsys.path.insert(0,"/app")\n'
             'assert not os.path.exists("/data1")\n'
             'from tehm.evaluation import research_r5_i2c_nack_binding_dev_v3 as v3\n'
             'src=sys.stdin.read()\n'
             'ctx={"interface":v3.NEW_INTERFACE,"status_output":"nack","defined_macros":[]}\n'
             'print(json.dumps(v3.bind({"binding_template":v3.TEMPLATE},src,ctx)))\n')
    argv = sandbox() + ['--ro-bind', str(memory), '/app', '--chdir', '/', '/usr/bin/python3', '-I', '-B', '-c', child]
    p = subprocess.run(argv, input=fault.encode(), capture_output=True, timeout=120)
    return json.loads(p.stdout) if p.returncode == 0 else {'error': p.stderr.decode()[-400:]}


def dev_oracle(candidate, root):
    """Frozen research-augmented oracle on the v3 candidate (DEV evidence only)."""
    freeze = json.loads((AUG / 'prelaunch.json').read_text())
    assert h((AUG / 'prelaunch.json').read_bytes()) == PRELAUNCH_SHA
    assert h((AUG / 'run.py').read_bytes()) == freeze['runner_sha256'] and \
        h((AUG / 'tb_nack_aug.v').read_bytes()) == freeze['tb_sha256']
    spec = importlib.util.spec_from_file_location('chance189_aug_runner', AUG / 'run.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    root.mkdir(parents=True)
    (root / 'i2c_master.v').write_text(candidate)
    (root / 'tb_nack_aug.v').write_bytes((AUG / 'tb_nack_aug.v').read_bytes())
    comp = runner.run([str(OSS / 'iverilog'), '-g2012', '-o', 'tb.vvp', '-s', 'tb_nack_aug',
                       'tb_nack_aug.v', 'i2c_master.v'], root, 60, 'compile')
    sim = runner.run([str(OSS / 'vvp'), '-n', 'tb.vvp'], root, 300, 'sim') if comp['rc'] == 0 else None
    stdout = (root / 'sim.stdout.log').read_text(errors='replace') if sim else ''
    return {'compile': comp, 'sim': sim, 'candidate_sha256': h(candidate),
            'verdicts': runner.verdicts(sim, stdout) if sim else {'target': 'UNKNOWN', 'preservation': 'UNKNOWN'}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    out, notes, fault, candidate = checks()
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='v3-dev-'))
    if args.output:
        root.mkdir(exist_ok=False)
    oracle = dev_oracle(candidate, root / 'dev-oracle')
    out['O_dev_oracle_target_pass'] = oracle['verdicts']['target'] == 'PASS'
    out['O_dev_oracle_preservation_pass'] = oracle['verdicts']['preservation'] == 'PASS'
    result = {'schema': 'r5-i2c-nack-binding-v3-dev-checks-v1', 'contract_commit': 'ae86c75',
              'valid': all(out.values()), 'check_count': len(out),
              'failed': sorted(k for k, v in out.items() if not v), 'checks': out, 'notes': notes,
              'binder_sha256': h(Path(v3.__file__).read_bytes()), 'checks_sha256': h(Path(__file__).read_bytes()),
              'dev_oracle': oracle, 'role': 'DEV_ONLY', 'memory_authority_granted': False,
              'heldout_transfer': False, 'model_calls': 0}
    (root / 'dev-checks.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ('valid', 'check_count', 'failed')}, indent=1))
    print(json.dumps({k: v for k, v in notes.items() if k.startswith('N_')}, indent=None))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
