import json
from pathlib import Path
import tempfile
import unittest

from run_auxiliary208 import sha, verify_completed_input_retry


# Real campaign inputs are archived separately from the published tool repository.
@unittest.skipUnless((Path(__file__).parent / 'semantic_evidence/c03125_original_imperial.list').is_file(),
                     "requires archived SKY130 campaign evidence (not shipped in Git)")
class AuxiliaryInputRecoveryTests(unittest.TestCase):
    def test_exact_original_data_and_prephysical_failure_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rel='package/sources/R01743__fpgawars__apio-examples/examples/icezum/marcha-imperial/imperial.list'
            asset=root/rel;asset.parent.mkdir(parents=True)
            asset.write_bytes((Path(__file__).parent/'semantic_evidence/c03125_original_imperial.list').read_bytes())
            digest=sha(asset);(root/'original_files.json').write_text(json.dumps({rel:digest}))
            work=root/'work';work.mkdir();(work/'flow.log').write_text('ERROR: Can not open file `imperial.list` for $readmemh.\n')
            run=root/'run';run.mkdir();result=run/'result.json'
            result.write_text(json.dumps(dict(status='fail',stage='1_1_yosys_canonicalize')))
            case={'design':'C03125__romnotes__apio-examples'}
            previous=dict(status='not_clean',attempts=[dict(work=str(work),run=str(run),status='fail',strategy='pin_pdn_clearance')])
            old={'source_bundle_sha256':{}}
            self.assertEqual(verify_completed_input_retry(case,previous,old,root)['sha256'],digest)
            with self.assertRaisesRegex(ValueError,'already contained'):
                verify_completed_input_retry(case,previous,{'source_bundle_sha256':{rel:digest}},root)
            result.write_text(json.dumps(dict(status='fail',stage='5_2_route')))
            with self.assertRaisesRegex(ValueError,'progressed beyond'):
                verify_completed_input_retry(case,previous,old,root)
            asset.write_bytes(b'00\n')
            with self.assertRaisesRegex(ValueError,'original ROM bytes'):
                verify_completed_input_retry(case,previous,old,root)


if __name__=='__main__':unittest.main()
