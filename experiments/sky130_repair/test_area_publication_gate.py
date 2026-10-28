import json
import tempfile
import unittest
from pathlib import Path
from publish_verified_area_recipe import prepare


class PublicationGateTests(unittest.TestCase):
    def test_incomplete_or_wrong_evidence_cannot_create_destination(self):
        key = dict(strategy='fixed_area_route_relief', symptom_id='route',
                   design_class='logic/small', platform='sky130hd')
        arms = [dict(subject=s, role=r, repeat=n, source_repository=s)
                for s in ('first', 'second') for r in ('A', 'B') for n in (0, 1)]
        scenarios = [
            ('candidate', key, arms, 'not promoted'),
            ('promoted', {**key, 'platform': 'other'}, arms, 'different recipe'),
            ('promoted', key, arms[:-1]+[arms[0]], 'independent repeated'),
            ('promoted', key, [{**a, 'source_repository': 'same'} for a in arms], 'independent source'),
        ]
        for status, judged_key, sample, message in scenarios:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = root/'trials'; source.mkdir(); dest = root/'durable'
                (source/'plan.json').write_text(json.dumps(dict(key=key, arms=sample)))
                (source/'judgement.json').write_text(json.dumps(dict(key=judged_key, lifecycle_status=status)))
                with self.assertRaisesRegex(ValueError, message):
                    prepare(source, dest)
                self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()
