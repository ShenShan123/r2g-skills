import json
from pathlib import Path
import sys
import tempfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from trace_adapter import generate
from rtlrepair_eval import evaluate


def test_generated_trace_replay_has_no_golden_code_and_detects_bug():
    work=Path(tempfile.mkdtemp(prefix='trace-fixture-',dir=ROOT/'reports'))
    source=work/'inputs';source.mkdir()
    good='module counter(input clk,en,output reg [3:0] q);always @(posedge clk) if(en) q<=q+1;endmodule'
    bad=good.replace('q+1','q+2')
    (source/'good.v').write_text(good);(source/'bad.v').write_text(bad)
    (source/'trace.csv').write_text('en,q\n0,0\n1,0\n1,1\n')
    task={'task':'OFFLINE-TRACE','source_group':'OFFLINE-FIXTURE','top':'counter',
        'reference':str(source/'good.v'),'buggy':str(source/'bad.v'),
        'sources':[str(source/'good.v')],'include_dir':str(source),
        'tests':[{'kind':'cycle_trace','name':'fixture','table':str(source/'trace.csv')}]}
    test=generate(task,work/'adapter')
    assert 'q<=q+1' not in Path(test['sources'][0]).read_text()
    task['tests'].append(test)
    assert evaluate(task,good,work/'reference')['status']=='PASS'
    assert evaluate(task,bad,work/'buggy')['status']=='FAIL'


def test_related_variants_are_not_independent_memory_lineages():
    from formal_runtime import source_lineage
    assert source_lineage({'source_group':'sha3_padder'})==source_lineage({'source_group':'sha3_keccak'})
    assert source_lineage({'source_group':'i2c_master'})==source_lineage({'source_group':'i2c_slave'})
    assert source_lineage({'source_group':'fsm_full'})!=source_lineage({'source_group':'mux_4_1'})
