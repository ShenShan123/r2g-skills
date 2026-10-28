import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'c03954_inactive_include_evidence.json').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class ConditionalIncludeWitnessTests(unittest.TestCase):
    def test_witness_does_not_allow_changed_macros_order_or_header(self):
        here=Path(__file__).parent
        evidence=json.loads((here/'c03954_inactive_include_evidence.json').read_text())
        fixture=here/'semantic_evidence/c03954_conditional_includes'
        for change in (None,'defines','order','header'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);work=root/'work';work.mkdir()
                shutil.copytree(fixture/'rtl',work/'rtl')
                shutil.copy2(here/'c03954_inactive_include_evidence.json',root/'c03954_inactive_include_evidence.json')
                cfg=(fixture/'original_config.mk').read_text()
                sources=[work/Path(p).relative_to('/work') for p in evidence['compiled_sources']]
                if change=='defines':cfg+='\nexport VERILOG_DEFINES = -DTECH_IS_TSMC_16FFC\n'
                if change=='order':sources.reverse()
                if change=='header':
                    header=work/'rtl'/evidence['inactive_includes'][0][0]
                    header.write_bytes(header.read_bytes()+b'\n`define TECH_IS_TSMC_16FFC\n')
                with patch.object(campaign,'HERE',root):
                    if change:
                        with self.assertRaises(ValueError):campaign.verified_inactive_includes(work,cfg,sources)
                        self.assertFalse((work/'conditional_include_evidence.json').exists())
                    else:
                        self.assertEqual(campaign.verified_inactive_includes(work,cfg,sources),evidence['inactive_includes'])


if __name__=='__main__':unittest.main()
