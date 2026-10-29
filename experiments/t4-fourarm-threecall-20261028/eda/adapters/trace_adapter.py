"""Qualified CSV replay for the trace-only RTLRepair FPGA cases.

Clock is the single omitted one-bit clock input; all other inputs must be in
the trace. Outputs are sampled BEFORE the row's rising edge, as in synchronous
transition-system traces. Verilator's explicit zero-initial model is disclosed.
No design-specific timing tuning, repair outcome filtering or golden RTL in TB.
"""
import csv
import json
from pathlib import Path
import re

from rtlrepair_eval import copy_inputs,run_tool,record


def integer(value):
    value=value.strip()
    if not re.fullmatch(r'-?[0-9]+',value):raise ValueError('non-integer trace value; do not guess semantics')
    return int(value)


def generate(task,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    traces=[t for t in task['tests'] if t['kind']=='cycle_trace']
    if len(traces)!=1 or not task['top']:raise ValueError('one trace and explicit top required')
    work=out/'interface';files=copy_inputs(task,work,Path(task['reference']).read_text())
    script='read_verilog -sv -I . '+' '.join(files)+'; hierarchy -top '+task['top']+'; proc; write_json interface.json'
    run=run_tool('yosys',['-p',script],work,120,out/'interface-tool.json')
    if run['returncode']!=0 or run['timeout']:raise ValueError('reference interface extraction unavailable')
    ports=json.loads((work/'interface.json').read_text())['modules'][task['top']]['ports']
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*',name) or p['direction']=='inout' for name,p in ports.items()):
        raise ValueError('unsupported port syntax/direction in trace adapter')
    rows=[[s.strip() for s in row] for row in csv.reader(Path(traces[0]['table']).read_text().splitlines())]
    rows=[row for row in rows if row]
    header=rows[0]
    while header and header[-1]=='':header.pop()
    values=[]
    for row in rows[1:]:
        while row and row[-1]=='':row.pop()
        if len(row)!=len(header):raise ValueError('incomplete trace row')
        values.append([integer(v) for v in row])
    if not values or len(set(header))!=len(header):raise ValueError('empty or ambiguous trace')
    if any(name not in ports for name in header):raise ValueError('trace references non-port signals')
    missing=[n for n,p in ports.items() if p['direction']=='input' and n not in header]
    if len(missing)!=1 or len(ports[missing[0]]['bits'])!=1 or 'clk' not in missing[0].lower():
        raise ValueError('trace clock not uniquely determined; no timing guess')
    clock=missing[0]
    inputs=[n for n in header if ports[n]['direction']=='input']
    outputs=[n for n in header if ports[n]['direction']=='output']
    if not outputs:raise ValueError('no measured trace outputs')
    declarations=['`timescale 1ns/1ps','module tb;','integer f;']
    for name,p in ports.items():
        width=len(p['bits']);kind='reg' if p['direction']=='input' else 'wire'
        declarations.append(f'{kind} '+('signed ' if p.get('signed') else '')+f'[{width-1}:0] {name};')
    declarations.append(task['top']+' dut('+','.join('.'+n+'('+n+')' for n in ports)+');')
    declarations += ['initial begin',f'{clock}=0;','f=$fopen("trace-output.csv","w");',
        '$fwrite(f,"time,'+','.join(outputs)+'\\n");']
    oracle=['time,'+','.join(outputs)]
    for cycle,row in enumerate(values):
        data=dict(zip(header,row))
        for name in inputs:
            width=len(ports[name]['bits']);declarations.append(f'{name}={width}\'d{data[name] % (1<<width)};')
        declarations += ['#1;','$fwrite(f,"'+str(cycle)+','+','.join('%0d' for _ in outputs)+'\\n",'+','.join(outputs)+');',
                          f'#1;{clock}=1;#1;{clock}=0;']
        oracle.append(str(cycle)+','+','.join(str(data[n]) for n in outputs))
    declarations += ['$fclose(f);$finish;end','endmodule']
    tb=out/'tb.v';tb.write_text('\n'.join(declarations)+'\n')
    expected=out/'oracle.csv';expected.write_text('\n'.join(oracle)+'\n')
    result={'kind':'output_oracle','name':'qualified-csv-replay','sources':[str(tb)],
        'oracle':str(expected),'output':'trace-output.csv','init_files':[],'timeout':120,
        'simulator':'verilator','semantics':'explicit zero-initial 2-state model, pre-edge sample, one clock, original trace integers',
        'input_trace':traces[0]['table'],'reference_rtl_in_generated_tb':False}
    record(out/'adapter.json',result)
    return result
