import json
from pathlib import Path
import tempfile
import unittest

from run_campaign import reviewed_frontend_placement_retry, sha


class ReviewedFrontendDensityTests(unittest.TestCase):
    def test_only_matching_density_failure_with_unchanged_proof_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'slang.v').write_text('reviewed translation')
            proof=work/'translation_evidence.json'
            proof.write_text(json.dumps({'sha256':{'slang.v':sha(work/'slang.v')}}))
            log=work/'flow.log'
            log.write_text('[INFO DPL-0006] Core area: 10935.49 um^2, Utilization: 95.1%\n[ERROR DPL-0036] Detailed placement failed inside DPL.\n')
            attempt=dict(work=str(work),strategy='sv_frontend',status='fail',
                         implementation_sha256={'translation_evidence.json':sha(proof)})
            previous=dict(status='not_clean',policy_revision=14,attempts=[attempt])
            case={'design':'C03966__rv_plic_target__rv_plic'}
            self.assertTrue(reviewed_frontend_placement_retry(case,previous))
            self.assertFalse(reviewed_frontend_placement_retry({'design':'C99999__other'},previous))
            self.assertFalse(reviewed_frontend_placement_retry(case,dict(previous,policy_revision=15)))
            log.write_text('[INFO DPL-0006] Utilization: 40.0%\n[ERROR DPL-0036]')
            self.assertFalse(reviewed_frontend_placement_retry(case,previous))
            log.write_text('[INFO DPL-0006] Utilization: 95.1%\n[ERROR OTHER]')
            self.assertFalse(reviewed_frontend_placement_retry(case,previous))
            (work/'slang.v').write_text('different translation')
            with self.assertRaisesRegex(ValueError,'translation changed'):
                reviewed_frontend_placement_retry(case,previous)
            proof.write_text('{}')
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                reviewed_frontend_placement_retry(case,previous)


if __name__=='__main__':unittest.main()
