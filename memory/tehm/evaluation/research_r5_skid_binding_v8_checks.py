"""Bounded source-only v8 checks on a supplied, already observed DEV source."""
import argparse
import copy
from pathlib import Path
import re
import unittest
from . import research_r5_skid_binding_v8 as b

SOURCE = None
ASSET = {'binding_template': b.TEMPLATE}

class Checks(unittest.TestCase):
    def bind(self, text=None, context=None):
        return b.bind_skid_payload_v8(ASSET,SOURCE if text is None else text,
                                      b.CONTEXT if context is None else context)

    def test_unique_binding_and_exact_edit(self):
        before=SOURCE;binding=self.bind();self.assertEqual(binding['status'],'BOUND')
        w=binding['witness'];start,stop=w['rhs_span']
        self.assertEqual(before[start:stop],'s_tdata')
        edited,receipt=b.apply_bound_skid_payload_v8(ASSET,before,b.CONTEXT,binding)
        self.assertEqual(edited,before[:start]+'skid_data'+before[stop:])
        self.assertEqual(SOURCE,before)
        self.assertEqual(receipt['functional_verdict'],'NOT_EVALUATED')
        self.assertFalse(receipt['memory_authority_granted'])

    def test_healthy_and_idempotent(self):
        fixed,_=b.apply_bound_skid_payload_v8(ASSET,SOURCE,b.CONTEXT,self.bind())
        result=self.bind(fixed)
        self.assertEqual((result['status'],result['reason']),('NO_MATCH','payload_already_uses_captured_slot'))
        with self.assertRaises(ValueError):b.apply_bound_skid_payload_v8(ASSET,fixed,b.CONTEXT,result)

    def test_context_exactness(self):
        for context in ({'WIDTH':8},{'WIDTH':8,'defined_macros':['FORMAL']},
                        {'WIDTH':8.0,'defined_macros':[]},{'WIDTH':True,'defined_macros':[]},
                        {'WIDTH':16,'defined_macros':[]},{**b.CONTEXT,'gold':'private'},
                        {'WIDTH':8,'defined_macros':()},{}):
            with self.subTest(context=context):self.assertEqual(self.bind(context=context)['status'],'UNSUPPORTED')

    def test_no_module_and_multiple_modules(self):
        self.assertEqual(self.bind('// only a comment')['status'],'NO_MATCH')
        active=b.active_text(SOURCE)[0]
        self.assertEqual(self.bind(active+active)['status'],'AMBIGUOUS')

    def test_tamper_and_stale_source(self):
        binding=self.bind()
        for key,value in [('rhs_span',[0,1]),('replacement_rhs','other'),('source_sha256','wrong')]:
            changed=copy.deepcopy(binding);changed['witness'][key]=value
            with self.assertRaises(ValueError):b.apply_bound_skid_payload_v8(ASSET,SOURCE,b.CONTEXT,changed)
        with self.assertRaises(ValueError):b.apply_bound_skid_payload_v8(ASSET,SOURCE+'\n',b.CONTEXT,binding)
        self.assertEqual(b.bind_skid_payload_v8({'binding_template':{}},SOURCE,b.CONTEXT)['status'],'UNSUPPORTED')

    def test_role_alpha_renaming(self):
        names=['clk','rst_n','s_tvalid','s_tready','s_tdata','s_tlast','m_tvalid','m_tready',
               'm_tdata','m_tlast','skid_valid','skid_data','skid_last','s_xfer','out_free','axis_skid']
        rename={name:f'role_{i}' for i,name in enumerate(names)}
        text=re.sub(r'\b[A-Za-z_]\w*\b',lambda m:rename.get(m[0],m[0]),SOURCE)
        binding=self.bind(text);self.assertEqual(binding['status'],'BOUND')
        edited,_=b.apply_bound_skid_payload_v8(ASSET,text,b.CONTEXT,binding)
        self.assertEqual(self.bind(edited)['status'],'NO_MATCH')

    def test_comment_invariance(self):
        text=SOURCE.replace('module axis_skid','/* unrelated golden answer: WRONG */\nmodule axis_skid')
        text=text.replace('skid_data  <=','skid_data /* capture */ <=' )
        binding=self.bind(text);self.assertEqual(binding['status'],'BOUND')
        edited,_=b.apply_bound_skid_payload_v8(ASSET,text,b.CONTEXT,binding)
        self.assertIn('unrelated golden answer: WRONG',edited)

    def test_formal_preserved_and_not_consulted(self):
        opening=SOURCE.index('`ifdef FORMAL');closing=SOURCE.index('`endif',opening)+len('`endif')
        alternative=SOURCE[:opening]+'`ifdef FORMAL\n  any arbitrary opaque inactive tokens\n`endif'+SOURCE[closing:]
        binding=self.bind(alternative);self.assertEqual(binding['status'],'BOUND')
        edited,_=b.apply_bound_skid_payload_v8(ASSET,alternative,b.CONTEXT,binding)
        self.assertEqual(edited[edited.index('`ifdef FORMAL'):],alternative[alternative.index('`ifdef FORMAL'):])
        self.assertTrue(edited.startswith('`default_nettype none'))
        self.assertTrue(edited.rstrip().endswith('`default_nettype wire'))

    def test_macro_and_lexical_rejections(self):
        variants=[SOURCE.replace('`ifdef FORMAL','`ifdef OTHER'),
            SOURCE.replace('`ifdef FORMAL','`ifdef FORMAL\n`ifdef NESTED'),
            SOURCE.replace('`endif','`else\n`endif'),
            SOURCE.replace('`default_nettype wire','`default_nettype none'),
            SOURCE.replace('module axis_skid','`include "private.sv"\nmodule axis_skid'),
            SOURCE.replace('skid_data  <= s_tdata','skid_data <= `ANSWER'),SOURCE+'\x00']
        for text in variants:
            with self.subTest(text=text[:30]):self.assertNotEqual(self.bind(text)['status'],'BOUND')

    def test_formal_not_allowed_to_hide_active_prefix(self):
        text=SOURCE.replace('`ifdef FORMAL','`ifdef FORMAL',1)
        opening=text.index('`ifdef FORMAL');closing=text.index('`endif',opening)+len('`endif')
        formal=text[opening:closing]
        text=text[:opening]+text[closing:]
        text=text.replace('module axis_skid',formal+'\nmodule axis_skid')
        self.assertEqual(self.bind(text)['reason'],'formal_not_in_trailing_inactive_slot')

    def test_extra_writer_or_process(self):
        for extra in ('assign m_tdata = s_tdata;', 'always_ff @(posedge clk) skid_data <= s_tdata;',
                      'logic spare;', 'initial begin end'):
            text=SOURCE.replace('`ifdef FORMAL',extra+'\n`ifdef FORMAL')
            self.assertEqual(self.bind(text)['status'],'UNSUPPORTED')

    def test_flow_sideband_and_alias_rejections(self):
        variants=[SOURCE.replace('!skid_valid;','skid_valid;'),
            SOURCE.replace('skid_data  <= s_tdata','skid_data <= m_tdata'),
            SOURCE.replace('skid_valid ? skid_last : s_tlast','skid_valid ? s_tlast : s_tlast'),
            SOURCE.replace('!m_tvalid || m_tready','m_tvalid || m_tready'),
            SOURCE.replace('skid_valid ? s_tdata : s_tdata','skid_valid ? unknown_data : s_tdata'),
            re.sub(r'\bskid_data\b','s_tdata',SOURCE)]
        for text in variants:self.assertNotEqual(self.bind(text)['status'],'BOUND')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    args,remaining=parser.parse_known_args();SOURCE=args.source.read_text()
    unittest.main(argv=['v8-checks',*remaining])
