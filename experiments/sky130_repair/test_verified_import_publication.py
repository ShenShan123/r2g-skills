import json
from pathlib import Path
import tempfile
import unittest
from import_auxiliary_result import publish_receipt


class AtomicPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_prior_or_active_campaign_receipt_is_never_overwritten(self):
        original={'status':'flow_running','attempts':[{'work':'original'}]}
        publish_receipt(self.root,'case',original)
        self.assertFalse(publish_receipt(self.root,'case',{'status':'dual_clean'},preserve_existing=True))
        self.assertEqual(json.loads((self.root/'case/repair.json').read_text()),original)
        self.assertEqual(list(self.root.iterdir()),[self.root/'case'])

    def test_verified_publication_is_idempotent_but_changed_evidence_is_rejected(self):
        record={'status':'dual_clean','artifact':'sha256'}
        self.assertTrue(publish_receipt(self.root,'case',record))
        self.assertFalse(publish_receipt(self.root,'case',record))
        with self.assertRaisesRegex(ValueError,'already differs'):
            publish_receipt(self.root,'case',dict(record,artifact='changed'))
        self.assertEqual(json.loads((self.root/'case/repair.json').read_text()),record)


if __name__=='__main__':unittest.main()
