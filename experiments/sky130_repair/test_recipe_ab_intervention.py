import json
from pathlib import Path
import tempfile
import unittest

from run_recipe_ab import relocate_intervention, verify_pdn_arm_inputs
from run_campaign import sha, mk


class RecipeInterventionTests(unittest.TestCase):
    def test_only_exact_reviewed_treatment_is_relocated(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);base='export PLATFORM = sky130hd\nexport SDC_FILE = /work/original.sdc\n'
            self.assertEqual(relocate_intervention(base,'A','pin_pdn_clearance',work,repo),base)
            hook=repo/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks/pin_pdn_clearance.tcl'
            cfg=base+'export PRE_IO_PLACEMENT_TCL = '+str(hook)+'\n'
            result=relocate_intervention(cfg,'B','pin_pdn_clearance',work,repo)
            self.assertEqual(mk(result,'PRE_IO_PLACEMENT_TCL'),'/work/pin_pdn_clearance.tcl')
            self.assertEqual(mk(result,'SDC_FILE'),'/work/original.sdc')
            self.assertEqual(sha(work/'pin_pdn_clearance.tcl'),sha(hook))
            with self.assertRaisesRegex(ValueError,'control arm'):relocate_intervention(cfg,'A','pin_pdn_clearance',work,repo)
            with self.assertRaisesRegex(ValueError,'unreviewed'):relocate_intervention(base,'B','pin_pdn_clearance',work,repo)
            with self.assertRaisesRegex(ValueError,'second hook'):
                relocate_intervention(cfg+'export POST_GLOBAL_PLACE_TCL = custom.tcl\n','B','pin_pdn_clearance',work,repo)

    def test_mutated_rom_or_constraints_invalidate_prepared_arm(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d);(project/'rtl/family').mkdir(parents=True);(project/'constraints').mkdir()
            rom=project/'rtl/family/rom.hex';rom.write_text('0001\n')
            sdc=project/'constraints/constraint.sdc';sdc.write_text('original clock\n')
            prov=project/'source_provenance.json';prov.write_text(json.dumps(dict(design='C00001__top',family='family',source_files={'family/rom.hex':sha(rom)},sdc_sha256=sha(sdc))))
            arm=dict(project=str(project),case='C00001__top',source_repository='family',source_provenance_sha256=sha(prov))
            verify_pdn_arm_inputs(arm)
            rom.write_text('0000\n')
            with self.assertRaisesRegex(ValueError,'RTL/ROM changed'):verify_pdn_arm_inputs(arm)
            rom.write_text('0001\n');sdc.write_text('relaxed clock\n')
            with self.assertRaisesRegex(ValueError,'SDC changed'):verify_pdn_arm_inputs(arm)


if __name__=='__main__':unittest.main()
