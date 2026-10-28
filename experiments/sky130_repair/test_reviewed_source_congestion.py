import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from run_campaign import reviewed_source_congestion_retry


class ReviewedCongestionTests(unittest.TestCase):
    def test_only_reviewed_source_preserving_congestion_is_retried(self):
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);audit=work/'source_audit.json';audit.write_text(json.dumps({'status':'source_preservation_checked'}))
            (work/'flow.log').write_text('[ERROR GRT-0116] Global routing finished with congestion.\n')
            case={'design':'C01034__TOP__vga-controller-verilog'}
            attempt=dict(work=str(work),strategy='source_normalization',failure_class='route_failed',
                         implementation_sha256={'source_audit.json':hashlib.sha256(audit.read_bytes()).hexdigest()})
            previous=dict(status='not_clean',policy_revision=13,attempts=[attempt])
            self.assertTrue(reviewed_source_congestion_retry(case,previous))
            self.assertFalse(reviewed_source_congestion_retry({'design':'C99999__unreviewed'},previous))
            self.assertFalse(reviewed_source_congestion_retry(case,dict(previous,policy_revision=14)))
            (work/'flow.log').write_text('another failure')
            self.assertFalse(reviewed_source_congestion_retry(case,previous))
            audit.write_text(json.dumps({'status':'source_preservation_checked','changed':True}))
            with self.assertRaisesRegex(ValueError,'evidence changed'):reviewed_source_congestion_retry(case,previous)


if __name__=='__main__':unittest.main()
