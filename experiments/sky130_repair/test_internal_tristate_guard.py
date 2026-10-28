import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_campaign as campaign


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'c02108_tribuf_evidence.json').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class InternalTristateGuardTests(unittest.TestCase):
    def test_original_rom_and_actual_transformation_are_bound_to_validation(self):
        here=Path(__file__).parent
        for change in (None,'rom','translation','definitions','sources'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);work=root/'work';(work/'rtl/src').mkdir(parents=True)
                shutil.copy2(here/'c02108_tribuf_evidence.json',root/'c02108_tribuf_evidence.json')
                source=here/'semantic_evidence/c02108_tribuf_review'
                for name in ('core.v','rom.v','soc.v'):shutil.copy2(source/'rtl'/name,work/'rtl/src'/name)
                cfg='export DESIGN_NAME = soc\nexport VERILOG_FILES = /work/rtl/src/core.v /work/rtl/src/rom.v /work/rtl/src/soc.v\n'
                if change=='rom':(work/'rtl/src/rom.v').write_text('different ROM contents')
                if change=='definitions':cfg+='export VERILOG_DEFINES = -DCHANGED\n'
                if change=='sources':cfg=cfg.replace(' /work/rtl/src/soc.v','')
                calls=[]
                def compile_candidate(work,*args,**kwargs):
                    calls.append(True)
                    raw=(source/'execution_expected.v').read_bytes()
                    (work/'frontend.v').write_bytes(b'changed output' if change=='translation' else raw)
                    return 0
                with patch.object(campaign,'HERE',root),patch.object(campaign,'podman',side_effect=compile_candidate):
                    if change:
                        with self.assertRaises(ValueError):campaign.translate_internal_tristate(work,cfg,'soc','fixture')
                        self.assertFalse((work/'translation_evidence.json').exists())
                        self.assertEqual(bool(calls),change=='translation')
                    else:
                        result=campaign.translate_internal_tristate(work,cfg,'soc','fixture')
                        self.assertEqual(campaign.mk(result,'DESIGN_NAME'),'soc')
                        self.assertEqual(campaign.mk(result,'VERILOG_FILES'),'/work/frontend.v')
                        evidence=json.loads((work/'translation_evidence.json').read_text())
                        self.assertFalse(evidence['formal_equivalence'])
                        self.assertFalse(evidence['physical_signoff'])


if __name__=='__main__':unittest.main()
